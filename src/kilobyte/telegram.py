from __future__ import annotations

import asyncio
import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .agent import Agent


class TelegramBridge:
    """Optional long-polling bridge; every message uses the daemon's same Agent/runtime."""

    def __init__(self, config_path: Path, agent: Agent):
        self.config_path = config_path
        self.agent = agent
        self.offset = 0
        self.running = False

    def config(self) -> dict[str, Any] | None:
        try:
            config = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return None
        token = str(config.get("token", "")).strip()
        allowed = {int(item) for item in config.get("allowed_chat_ids", [])}
        return {"token": token, "allowed": allowed} if token and allowed else None

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

    async def run(self) -> None:
        config = self.config()
        if not config:
            return
        token, allowed = config["token"], config["allowed"]
        self.running = True
        while self.running:
            try:
                response = await asyncio.to_thread(self._call, token, "getUpdates", {"offset": self.offset, "timeout": 30, "allowed_updates": json.dumps(["message"])})
                for update in response.get("result", []):
                    self.offset = max(self.offset, int(update["update_id"]) + 1)
                    message = update.get("message") or {}
                    chat_id = int((message.get("chat") or {}).get("id", 0))
                    text = message.get("text")
                    if chat_id not in allowed or not text:
                        continue
                    output: list[str] = []
                    async for event in self.agent.run(str(text), f"telegram-{chat_id}", remote=True):
                        if event.get("type") == "token":
                            output.append(event.get("text", ""))
                    await self.send(token, chat_id, "".join(output))
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(5)

    def stop(self) -> None:
        self.running = False
