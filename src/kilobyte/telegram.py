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

    @staticmethod
    def _call(token: str, method: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        encoded = urllib.parse.urlencode(data or {}).encode()
        request = urllib.request.Request(f"https://api.telegram.org/bot{token}/{method}", data=encoded)
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.load(response)

    async def send(self, token: str, chat_id: int, text: str) -> None:
        for start in range(0, len(text) or 1, 3900):
            chunk = text[start : start + 3900] or "(empty response)"
            await asyncio.to_thread(self._call, token, "sendMessage", {"chat_id": chat_id, "text": chunk})

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
        output: list[str] = []
        tools: list[str] = []
        try:
            async for event in self.agent.run(str(text), f"telegram-{chat_id}", remote=True):
                kind = event.get("type")
                if kind == "token":
                    output.append(event.get("text", ""))
                elif kind == "tool_start":
                    tools.append(str(event.get("name")))
                elif kind == "error":
                    output.append(f"\n[error: {event.get('error')}]")
        except Exception as exc:
            # Silence looks identical to a hung bot, so always tell the user.
            log.exception("telegram request failed for chat %s", chat_id)
            await self.send(token, chat_id, f"Kilo hit an error: {exc}")
            return
        finally:
            typing.cancel()
        answer = "".join(output).strip()
        if tools:
            answer = f"{answer}\n\n— used: {', '.join(dict.fromkeys(tools))}" if answer else f"(no text; used {', '.join(tools)})"
        await self.send(token, chat_id, answer or "(no response)")

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
        while self.running:
            try:
                response = await asyncio.to_thread(self._call, token, "getUpdates", {"offset": self.offset, "timeout": 30, "allowed_updates": json.dumps(["message"])})
                for update in response.get("result", []):
                    self.offset = max(self.offset, int(update["update_id"]) + 1)
                    message = update.get("message") or {}
                    chat_id = int((message.get("chat") or {}).get("id", 0))
                    text = message.get("text")
                    if not text:
                        continue
                    if chat_id not in allowed:
                        log.warning("ignored telegram message from unauthorised chat %s", chat_id)
                        continue
                    await self._reply(token, chat_id, text)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("telegram poll failed; retrying")
                await asyncio.sleep(5)

    def stop(self) -> None:
        self.running = False
