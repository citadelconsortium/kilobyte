from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .agent import Agent


log = logging.getLogger("kilobyte.telegram")


class TelegramBridge:
    """Optional long-polling bridge; every message uses the daemon's same Agent/runtime."""

    CONFIG_POLL_SECONDS = 30

    def __init__(self, config_path: Path, agent: Agent):
        self.config_path = config_path
        self.agent = agent
        self.offset = 0
        self.running = False

    def config(self) -> dict[str, Any] | None:
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("telegram config unreadable (%s); staying disabled", exc)
            return None
        token = str(config.get("token", "")).strip()
        try:
            allowed = {int(item) for item in config.get("allowed_chat_ids", [])}
        except (TypeError, ValueError):
            log.warning("telegram allowed_chat_ids must be integers; staying disabled")
            return None
        if not token or token == "PASTE_BOT_TOKEN_HERE":
            log.warning("telegram config has no bot token; staying disabled")
            return None
        if not allowed:
            log.warning("telegram config has an empty allowed_chat_ids; staying disabled")
            return None
        return {"token": token, "allowed": allowed}

    # Shown under the message box by Telegram once registered with setMyCommands.
    COMMANDS = (
        ("start", "what Kilo is and how to use it"),
        ("status", "model, backend and resource status"),
        ("new", "start a fresh conversation"),
        ("help", "list commands"),
    )

    MENU = {
        "inline_keyboard": [
            [{"text": "Status", "callback_data": "status"}, {"text": "New chat", "callback_data": "new"}],
            [{"text": "Help", "callback_data": "help"}],
        ]
    }

    @staticmethod
    def _call(token: str, method: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(data or {})
        # Nested structures (keyboards, allowed_updates) must be JSON, not form values.
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                payload[key] = json.dumps(value)
        encoded = urllib.parse.urlencode(payload).encode()
        request = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=encoded)
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.load(response)

    async def send(self, token: str, chat_id: int, text: str, keyboard: dict[str, Any] | None = None) -> None:
        chunks = [text[start : start + 3900] for start in range(0, len(text) or 1, 3900)] or ["(empty response)"]
        for index, chunk in enumerate(chunks):
            data: dict[str, Any] = {"chat_id": chat_id, "text": chunk or "(empty response)"}
            # Attach the menu only to the final chunk so it appears once, at the end.
            if keyboard and index == len(chunks) - 1:
                data["reply_markup"] = keyboard
            await asyncio.to_thread(self._call, token, "sendMessage", data)

    async def _send_progress(self, token: str, chat_id: int, text: str) -> int | None:
        try:
            response = await asyncio.to_thread(self._call, token, "sendMessage", {"chat_id": chat_id, "text": text})
            return int(response["result"]["message_id"])
        except Exception:
            return None

    async def _edit_progress(self, token: str, chat_id: int, message_id: int | None, text: str) -> None:
        """Rewrite the live status line. Telegram rejects an edit that would not change
        the text, and that rejection is not worth surfacing."""
        if message_id is None:
            return
        try:
            await asyncio.to_thread(
                self._call, token, "editMessageText",
                {"chat_id": chat_id, "message_id": message_id, "text": text},
            )
        except Exception:
            pass

    async def _delete(self, token: str, chat_id: int, message_id: int | None) -> None:
        if message_id is None:
            return
        try:
            await asyncio.to_thread(self._call, token, "deleteMessage", {"chat_id": chat_id, "message_id": message_id})
        except Exception:
            pass

    async def _keep_typing(self, token: str, chat_id: int) -> None:
        """Telegram clears the typing indicator after ~5s, and a reply here can take
        minutes, so refresh it until the answer is ready."""
        try:
            while True:
                try:
                    await asyncio.to_thread(self._call, token, "sendChatAction", {"chat_id": chat_id, "action": "typing"})
                except Exception:
                    pass
                await asyncio.sleep(4)
        except asyncio.CancelledError:
            pass

    async def _reply(self, token: str, chat_id: int, text: str) -> None:
        typing = asyncio.create_task(self._keep_typing(token, chat_id))
        # A reply can take minutes here; a status message that is edited as work
        # progresses is the only way the sender can tell it is alive.
        progress = await self._send_progress(token, chat_id, "◈ thinking…")
        output: list[str] = []
        tools: list[str] = []
        try:
            async for event in self.agent.run(str(text), f"telegram-{chat_id}", remote=True):
                kind = event.get("type")
                if kind == "token":
                    output.append(event.get("text", ""))
                elif kind == "thinking":
                    await self._edit_progress(token, chat_id, progress, f"◈ thinking · step {event.get('step')}…")
                elif kind == "tool_start":
                    name = str(event.get("name"))
                    tools.append(name)
                    await self._edit_progress(token, chat_id, progress, f"◈ running {name}…")
                elif kind == "tool_end":
                    mark = "✓" if event.get("ok") else "!"
                    await self._edit_progress(token, chat_id, progress, f"{mark} {event.get('name')} — interpreting…")
                elif kind == "error":
                    output.append(f"\n[error: {event.get('error')}]")
        except Exception as exc:
            # Silence looks identical to a hung bot, so always tell the user.
            log.exception("telegram request failed for chat %s", chat_id)
            await self._delete(token, chat_id, progress)
            await self.send(token, chat_id, f"Kilo hit an error: {exc}", self.MENU)
            return
        finally:
            typing.cancel()
        await self._delete(token, chat_id, progress)
        answer = "".join(output).strip()
        if tools:
            footer = "used: " + ", ".join(dict.fromkeys(tools))
            answer = f"{answer}\n\n— {footer}" if answer else f"({footer}, no text returned)"
        await self.send(token, chat_id, answer or "(no response)", self.MENU)

    async def _command(self, token: str, chat_id: int, command: str) -> bool:
        """Handle a slash command or menu button. Returns True when handled."""
        command = command.lstrip("/").split("@")[0].split()[0].lower() if command.strip() else ""
        if command in {"start", "help"}:
            lines = [
                "Kilo — your local AI, running on your own machine.",
                "",
                "Send a message and it is answered by the same brain the terminal uses.",
                "Nothing is sent to a cloud service.",
                "",
                "Commands:",
                *[f"/{name} — {description}" for name, description in self.COMMANDS],
                "",
                "Over Telegram Kilo is read-only: it can look things up, search the web and",
                "remember facts, but cannot run commands, write files or change the system.",
            ]
            await self.send(token, chat_id, "\n".join(lines), self.MENU)
            return True
        if command == "new":
            self.agent.memory.new_session("telegram", "reset")
            await self.send(token, chat_id, "Started a fresh conversation.", self.MENU)
            return True
        if command == "status":
            try:
                status = self.agent.runtime.status()
                profile = status.get("profile") or {}
                lines = [
                    f"state     {'running' if status.get('running') else 'stopped'}",
                    f"model     {Path(str(status.get('model', ''))).stem}",
                    f"uptime    {status.get('uptime_seconds', 0)}s",
                    f"context   {profile.get('context_size')}",
                    f"threads   {profile.get('threads')}   gpu layers {profile.get('gpu_layers')}",
                    f"memory    {profile.get('available_mb')} MiB free of {profile.get('total_mb')} MiB",
                ]
                await self.send(token, chat_id, "\n".join(lines), self.MENU)
            except Exception as exc:
                await self.send(token, chat_id, f"Could not read status: {exc}", self.MENU)
            return True
        return False

    async def run(self) -> None:
        self.running = True
        config = self.config()
        while self.running and config is None:
            # Let the operator enable Telegram by writing the config file, without
            # having to restart the daemon to be noticed.
            await asyncio.sleep(self.CONFIG_POLL_SECONDS)
            config = self.config()
        if not self.running or config is None:
            return
        token, allowed = config["token"], config["allowed"]
        log.info("telegram bridge enabled for %d authorised chat(s)", len(allowed))
        try:
            # Publishes the command list into Telegram's UI menu.
            await asyncio.to_thread(
                self._call, token, "setMyCommands",
                {"commands": [{"command": name, "description": description} for name, description in self.COMMANDS]},
            )
        except Exception:
            log.warning("could not publish telegram command menu", exc_info=True)
        while self.running:
            try:
                response = await asyncio.to_thread(self._call, token, "getUpdates", {"offset": self.offset, "timeout": 30, "allowed_updates": ["message", "callback_query"]})
                for update in response.get("result", []):
                    self.offset = max(self.offset, int(update["update_id"]) + 1)

                    query = update.get("callback_query")
                    if query:
                        chat_id = int(((query.get("message") or {}).get("chat") or {}).get("id", 0))
                        if chat_id not in allowed:
                            log.warning("ignored telegram button from unauthorised chat %s", chat_id)
                            continue
                        # Acknowledge promptly or the client shows a spinner on the button.
                        try:
                            await asyncio.to_thread(self._call, token, "answerCallbackQuery", {"callback_query_id": query.get("id")})
                        except Exception:
                            pass
                        await self._command(token, chat_id, str(query.get("data", "")))
                        continue

                    message = update.get("message") or {}
                    chat_id = int((message.get("chat") or {}).get("id", 0))
                    text = message.get("text")
                    if not text:
                        continue
                    if chat_id not in allowed:
                        log.warning("ignored telegram message from unauthorised chat %s", chat_id)
                        continue
                    if text.startswith("/") and await self._command(token, chat_id, text):
                        continue
                    await self._reply(token, chat_id, text)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("telegram poll failed; retrying")
                await asyncio.sleep(5)

    def stop(self) -> None:
        self.running = False
