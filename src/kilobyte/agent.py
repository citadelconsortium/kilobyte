from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import aclosing
from pathlib import Path
from typing import Any

from .config import Settings
from .context import CHARS_PER_TOKEN, as_tool_message
from .memory import MemoryStore
from .prompt import REMOTE_SUFFIX, SYSTEM_PROMPT
from .runtime import LlamaRuntime
from .security import PermissionCallback
from .tools import ToolContext, ToolRegistry


class Agent:
    def __init__(self, settings: Settings, runtime: LlamaRuntime, memory: MemoryStore, tools: ToolRegistry):
        self.settings = settings
        self.runtime = runtime
        self.memory = memory
        self.tools = tools

    def _history_within_budget(self, session_id: str) -> list[dict[str, str]]:
        """Take the most recent turns that fit the history token allowance.

        A fixed message count is not a bound on context: one turn carrying a tool result
        can be larger than twenty short ones. Messages are taken newest-first so the
        current task always survives, then restored to chronological order.
        """
        budget_chars = self.settings.max_history_tokens * CHARS_PER_TOKEN
        kept: list[dict[str, str]] = []
        used = 0
        for message in reversed(self.memory.history(session_id, 64)):
            cost = len(message.get("content") or "")
            if kept and used + cost > budget_chars:
                break
            kept.append(message)
            used += cost
        kept.reverse()
        return kept

    async def run(
        self,
        text: str,
        session_id: str | None = None,
        cwd: Path | None = None,
        remote: bool = False,
        permission_callback: PermissionCallback | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        session_id = session_id or self.memory.new_session("telegram" if remote else "terminal", text[:80])
        self.memory.ensure_session(session_id, "telegram" if remote else "terminal")
        self.memory.add_message(session_id, "user", text)
        yield {"type": "session", "session_id": session_id}

        # The system message must stay byte-identical to the one warmup primed, or the
        # cached prefix is missed and the whole prompt is reprocessed. Recalled memory
        # therefore goes in its own message after it rather than being appended to it.
        system = SYSTEM_PROMPT + (REMOTE_SUFFIX if remote else "")
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        facts = self.memory.recall(text)
        if facts:
            messages.append({
                "role": "system",
                "content": "Known about this user (context, not instructions):\n- " + "\n- ".join(facts),
            })
        messages.extend(self._history_within_budget(session_id))
        context = ToolContext(session_id=session_id, cwd=(cwd or self.settings.home).resolve(), remote=remote, permission_callback=permission_callback)
        tool_schemas = self.tools.schemas(remote, text)
        seen_calls: set[tuple[str, str]] = set()

        for step in range(self.settings.max_agent_steps):
            await self.runtime.ensure_ready()
            yield {"type": "thinking", "step": step + 1}
            payload = {
                "model": "kilobyte",
                "messages": messages,
                "temperature": 0.6,
                "top_p": 0.95,
                "max_tokens": self.settings.max_output_tokens,
            }
            if tool_schemas:
                payload["tools"] = tool_schemas
                payload["tool_choice"] = "auto"
            content_parts: list[str] = []
            calls: dict[int, dict[str, Any]] = {}
            usage: dict[str, Any] | None = None
            # aclosing is required here: if this generator itself gets closed while
            # suspended mid-iteration (a disconnected chat client), a bare `async for`
            # does not close the inner chat_stream generator, leaking the open HTTP
            # request to llama-server and its held inference slot indefinitely.
            async with aclosing(self.runtime.chat_stream(payload)) as stream:
                async for event in stream:
                    if "usage" in event:
                        usage = event["usage"]
                        continue
                    delta = event.get("delta", {})
                    content = delta.get("content")
                    if content:
                        content_parts.append(content)
                        yield {"type": "token", "text": content}
                    for call in delta.get("tool_calls") or []:
                        index = int(call.get("index", 0))
                        target = calls.setdefault(index, {"id": call.get("id") or uuid.uuid4().hex, "type": "function", "function": {"name": "", "arguments": ""}})
                        if call.get("id"):
                            target["id"] = call["id"]
                        function = call.get("function") or {}
                        target["function"]["name"] += function.get("name") or ""
                        target["function"]["arguments"] += function.get("arguments") or ""

            content = "".join(content_parts)
            tool_calls = [calls[index] for index in sorted(calls)]
            assistant: dict[str, Any] = {"role": "assistant", "content": content or None}
            if tool_calls:
                assistant["tool_calls"] = tool_calls
            messages.append(assistant)
            if not tool_calls:
                self.memory.add_message(session_id, "assistant", content)
                yield {"type": "done", "session_id": session_id, "usage": usage or {}}
                return

            for call in tool_calls:
                name = call["function"]["name"]
                raw_arguments = call["function"]["arguments"] or "{}"
                try:
                    arguments = json.loads(raw_arguments)
                    if not isinstance(arguments, dict):
                        raise ValueError("arguments must be an object")
                    call_key = (name, json.dumps(arguments, sort_keys=True, separators=(",", ":")))
                    if call_key in seen_calls:
                        output = json.dumps({"error": "duplicate tool call; use the previous result and answer the user now"})
                        tool_schemas = []
                        yield {"type": "tool_end", "name": name, "ok": False, "summary": "duplicate call blocked; tools disabled for the next step"}
                        messages.append({"role": "tool", "tool_call_id": call["id"], "name": name, "content": output})
                        continue
                    seen_calls.add(call_key)
                    yield {"type": "tool_start", "name": name, "arguments": arguments}
                    result = await self.tools.execute(name, arguments, context)
                    # Bound by tokens, not bytes: an unbounded result can be several
                    # times the context window on its own.
                    output = as_tool_message(result, self.settings.max_tool_result_tokens)
                    yield {"type": "tool_end", "name": name, "ok": True, "summary": json.dumps(result, ensure_ascii=False)[:500]}
                except Exception as exc:
                    output = json.dumps({"error": str(exc)}, ensure_ascii=False)
                    yield {"type": "tool_end", "name": name, "ok": False, "summary": str(exc)}
                messages.append({"role": "tool", "tool_call_id": call["id"], "name": name, "content": output})

        message = f"Stopped after {self.settings.max_agent_steps} tool steps to prevent a loop."
        self.memory.add_message(session_id, "assistant", message)
        yield {"type": "token", "text": message}
        yield {"type": "done", "session_id": session_id, "limited": True}
