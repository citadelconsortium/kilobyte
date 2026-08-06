from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Any

from .rpc import RPCClient


CYAN = "\033[38;5;51m"
PURPLE = "\033[38;5;141m"
GREEN = "\033[38;5;84m"
YELLOW = "\033[38;5;220m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"


class TerminalUI:
    """Dependency-free bordered, animated streaming TUI."""

    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, client: RPCClient):
        self.client = client
        self.session_id: str | None = None

    @staticmethod
    def banner() -> None:
        print(f"{GREEN}{BOLD}╭──────────────────────────────────────────────────────────────╮{RESET}")
        print(f"{GREEN}{BOLD}│  K I L O B Y T E   ·   LOCAL BRAIN   ·   ONLINE             │{RESET}")
        print(f"{GREEN}{BOLD}│  Made by 0v3r51ght                                      ◉   │{RESET}")
        print(f"{GREEN}{BOLD}╰──────────────────────────────────────────────────────────────╯{RESET}")
        print(f"{DIM}  One local model · private by default · /help · /exit · Ctrl-C{RESET}\n")

    async def _permission(self, event: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        prompt = f"\n{YELLOW}Permission required [{event['risk']}]:{RESET} {event['detail']}\nAllow once? [y/N] "
        answer = await asyncio.to_thread(input, prompt)
        writer.write((__import__("json").dumps({"type": "permission_response", "id": event["id"], "allow": answer.lower() in {"y", "yes"}}) + "\n").encode())
        await writer.drain()

    async def ask(self, text: str) -> None:
        reader, writer = await asyncio.open_unix_connection(self.client.socket_path)
        request = {"command": "chat", "text": text, "session_id": self.session_id, "cwd": str(Path.cwd())}
        writer.write((__import__("json").dumps(request) + "\n").encode())
        await writer.drain()
        spinner = 0
        started = time.monotonic()
        last_flush = started
        pending = ""
        printed = False
        try:
            while raw := await reader.readline():
                event = __import__("json").loads(raw)
                kind = event.get("type")
                if kind == "session":
                    self.session_id = event["session_id"]
                elif kind == "thinking":
                    elapsed = time.monotonic() - started
                    sys.stdout.write(f"\r\033[2K{GREEN}{self.SPINNER[spinner % len(self.SPINNER)]}{RESET} {DIM}planning step {event['step']}… {elapsed:0.0f}s{RESET}")
                    sys.stdout.flush()
                    spinner += 1
                elif kind == "token":
                    if not printed:
                        sys.stdout.write(f"\r\033[2K{GREEN}")
                        printed = True
                    pending += event.get("text", "")
                    now = time.monotonic()
                    if now - last_flush >= 0.05:
                        sys.stdout.write(pending)
                        sys.stdout.flush()
                        pending = ""
                        last_flush = now
                elif kind == "tool_start":
                    sys.stdout.write(f"\r\033[2K{DIM}↳ {event['name']}…{RESET}\n")
                    sys.stdout.flush()
                elif kind == "tool_end":
                    icon = f"{GREEN}✓" if event.get("ok") else f"{YELLOW}!"
                    print(f"{icon}{RESET} {DIM}{event['name']}: {event.get('summary', '')[:180]}{RESET}")
                elif kind == "permission":
                    await self._permission(event, writer)
                elif kind == "error":
                    print(f"\r\033[2K{YELLOW}Error:{RESET} {event.get('error')}")
                elif kind == "done":
                    break
            if pending:
                sys.stdout.write(pending)
            if printed:
                sys.stdout.write(RESET)
            sys.stdout.flush()
        finally:
            writer.close()
            await writer.wait_closed()
            print(f"\n{GREEN}╰─ response complete ──────────────────────────────────────────╯{RESET}\n")

    async def run(self) -> None:
        self.banner()
        while True:
            try:
                text = (await asyncio.to_thread(input, f"{GREEN}╭─ you ›{RESET} ")).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return
            if not text:
                continue
            if text in {"/exit", "/quit"}:
                return
            if text == "/new":
                self.session_id = None
                print(f"{DIM}New session started.{RESET}")
                continue
            if text == "/help":
                print(
                    "/new    start a separate session\n"
                    "/status show daemon, model and resource status\n"
                    "/clear  clear the screen\n"
                    "/exit   leave Kilobyte\n"
                    "Normal text talks to the local brain."
                )
                continue
            if text == "/clear":
                sys.stdout.write("\033[2J\033[H")
                self.banner()
                continue
            if text == "/status":
                try:
                    status = await self.client.request("status")
                except (ConnectionError, FileNotFoundError, OSError) as exc:
                    print(f"{YELLOW}Daemon unavailable:{RESET} {exc}")
                    continue
                profile = status.get("profile") or {}
                print(f"{DIM}healthy{RESET}   {status.get('healthy')}")
                print(f"{DIM}uptime{RESET}    {status.get('uptime_seconds', 0)}s")
                print(f"{DIM}model{RESET}     {status.get('model')}")
                print(f"{DIM}context{RESET}   {profile.get('context_size')}  {DIM}threads{RESET} {profile.get('threads')}  {DIM}gpu layers{RESET} {profile.get('gpu_layers')}")
                print(f"{DIM}memory{RESET}    {status.get('memory')}\n")
                continue
            print(f"{GREEN}╭─ kilo ›{RESET} ", end="", flush=True)
            try:
                await self.ask(text)
            except (ConnectionError, FileNotFoundError) as exc:
                print(f"{YELLOW}Daemon unavailable:{RESET} {exc}\nTry: sudo systemctl restart kilobyte")
