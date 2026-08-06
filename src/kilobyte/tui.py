from __future__ import annotations

import asyncio
import os
import signal
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

# 3D shadow block "KILO" wordmark, rendered left of the live status panel.
KILO_ART = (
    "██╗  ██╗██╗██╗      ██████╗ ",
    "██║ ██╔╝██║██║     ██╔═══██╗",
    "█████╔╝ ██║██║     ██║   ██║",
    "██╔═██╗ ██║██║     ██║   ██║",
    "██║  ██╗██║███████╗╚██████╔╝",
    "╚═╝  ╚═╝╚═╝╚══════╝ ╚═════╝ ",
)


class TerminalUI:
    """Dependency-free bordered, animated streaming TUI."""

    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, client: RPCClient):
        self.client = client
        self.session_id: str | None = None

    async def banner(self) -> None:
        online = True
        model_name = "unknown"
        context_size = threads = gpu_layers = "?"
        try:
            status = await self.client.request("status")
            profile = status.get("profile") or {}
            model_name = Path(str(status.get("model", ""))).stem or "unknown"
            context_size = profile.get("context_size", "?")
            threads = profile.get("threads", "?")
            gpu_layers = profile.get("gpu_layers", "?")
        except (ConnectionError, FileNotFoundError, OSError):
            online = False

        dot = f"{GREEN}●{RESET}" if online else f"{YELLOW}●{RESET}"
        state = f"{GREEN}online{RESET}" if online else f"{YELLOW}offline{RESET}"
        info = (
            f"{BOLD}{GREEN}KILOBYTE{RESET}  {dot} {state}",
            f"{DIM}local-first AI · one model · no cloud{RESET}",
            f"{DIM}model{RESET}    {model_name}" if online else f"{YELLOW}sudo systemctl start kilobyte{RESET}",
            f"{DIM}context{RESET}  {context_size}   {DIM}threads{RESET} {threads}   {DIM}gpu{RESET} {gpu_layers}" if online else "",
            "",
            f"{DIM}made by 0v3r51ght{RESET}",
        )
        print()
        for art_line, info_line in zip(KILO_ART, info):
            print(f"  {GREEN}{BOLD}{art_line}{RESET}   {info_line}")
        print(f"\n  {DIM}/help · /status · /new · /clear · /exit · Ctrl-C{RESET}\n")

    @staticmethod
    def _width() -> int:
        return max(48, min(os.get_terminal_size().columns if sys.stdout.isatty() else 80, 100))

    def _panel_top(self, label: str, color: str) -> None:
        width = self._width()
        head = f"─ {label} "
        print(f"{color}╭{head}{'─' * max(0, width - len(head) - 2)}╮{RESET}")

    def _panel_bottom(self, color: str, note: str = "") -> None:
        width = self._width()
        tail = f" {note} ─" if note else ""
        print(f"{color}╰{'─' * max(0, width - len(tail) - 2)}{tail}╯{RESET}")

    async def _permission(self, event: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        prompt = f"\n{YELLOW}Permission required [{event['risk']}]:{RESET} {event['detail']}\nAllow once? [y/N] "
        answer = await asyncio.to_thread(input, prompt)
        writer.write((__import__("json").dumps({"type": "permission_response", "id": event["id"], "allow": answer.lower() in {"y", "yes"}}) + "\n").encode())
        await writer.drain()

    async def _animate(self, state: dict[str, Any]) -> None:
        """Redraw the activity line on a timer so slow steps never look frozen."""
        frame = 0
        while True:
            if state["streaming"]:
                await asyncio.sleep(0.12)
                frame += 1
                continue
            elapsed = time.monotonic() - state["started"]
            glyph = self.SPINNER[frame % len(self.SPINNER)]
            sys.stdout.write(
                f"\r\033[2K{GREEN}│{RESET} {GREEN}{glyph}{RESET} {DIM}{state['phase']} · {elapsed:0.0f}s{RESET}"
                f"  {DIM}(ctrl-c to cancel){RESET}"
            )
            sys.stdout.flush()
            frame += 1
            await asyncio.sleep(0.12)

    async def ask(self, text: str) -> None:
        reader, writer = await asyncio.open_unix_connection(self.client.socket_path)
        request = {"command": "chat", "text": text, "session_id": self.session_id, "cwd": str(Path.cwd())}
        writer.write((__import__("json").dumps(request) + "\n").encode())
        await writer.drain()
        started = time.monotonic()
        state: dict[str, Any] = {"phase": "connecting", "started": started, "streaming": False}
        animator = asyncio.create_task(self._animate(state))
        last_flush = started
        pending = ""
        printed = False
        cancelled = False
        try:
            while raw := await reader.readline():
                event = __import__("json").loads(raw)
                kind = event.get("type")
                if kind == "session":
                    self.session_id = event["session_id"]
                    state["phase"] = "thinking"
                elif kind == "thinking":
                    state["phase"] = f"thinking · step {event['step']}"
                    state["streaming"] = False
                elif kind == "token":
                    if not printed:
                        state["streaming"] = True
                        sys.stdout.write(f"\r\033[2K{GREEN}│{RESET} ")
                        printed = True
                    pending += event.get("text", "")
                    now = time.monotonic()
                    if now - last_flush >= 0.05:
                        sys.stdout.write(pending.replace("\n", f"\n{GREEN}│{RESET} "))
                        sys.stdout.flush()
                        pending = ""
                        last_flush = now
                elif kind == "tool_start":
                    if pending:
                        sys.stdout.write(pending.replace("\n", f"\n{GREEN}│{RESET} "))
                        pending = ""
                    state["phase"] = f"running {event['name']}"
                    state["streaming"] = False
                    sys.stdout.write(f"\r\033[2K{GREEN}│{RESET} {PURPLE}◈{RESET} {DIM}{event['name']}…{RESET}\n")
                    sys.stdout.flush()
                    printed = False
                elif kind == "tool_end":
                    icon = f"{GREEN}✓{RESET}" if event.get("ok") else f"{YELLOW}!{RESET}"
                    print(f"\r\033[2K{GREEN}│{RESET} {icon} {DIM}{event['name']}: {event.get('summary', '')[:160]}{RESET}")
                    state["phase"] = "interpreting result"
                    state["streaming"] = False
                    printed = False
                elif kind == "permission":
                    state["streaming"] = True  # pause the animator while we prompt
                    await self._permission(event, writer)
                    state["streaming"] = False
                elif kind == "error":
                    print(f"\r\033[2K{GREEN}│{RESET} {YELLOW}error:{RESET} {event.get('error')}")
                    printed = False
                elif kind == "done":
                    break
            if pending:
                sys.stdout.write(pending.replace("\n", f"\n{GREEN}│{RESET} "))
            if printed:
                sys.stdout.write(RESET)
            sys.stdout.flush()
        except (asyncio.CancelledError, KeyboardInterrupt):
            cancelled = True
            raise
        finally:
            animator.cancel()
            # Dropping the connection tells the daemon to close the agent run and
            # release the model slot, so a cancel actually stops the work.
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            sys.stdout.write("\r\033[2K")
            elapsed = time.monotonic() - started
            if cancelled:
                self._panel_bottom(YELLOW, f"cancelled after {elapsed:0.1f}s")
            else:
                self._panel_bottom(GREEN, f"done in {elapsed:0.1f}s")
            print()

    async def run(self) -> None:
        await self.banner()
        while True:
            try:
                self._panel_top("you", CYAN)
                text = (await asyncio.to_thread(input, f"{CYAN}│{RESET} ")).strip()
                self._panel_bottom(CYAN)
            except (EOFError, KeyboardInterrupt):
                print(f"\n{DIM}bye{RESET}")
                return
            if not text:
                continue
            if text in {"/exit", "/quit", "/q", "exit", "quit"}:
                print(f"{DIM}bye{RESET}")
                return
            if text == "/new":
                self.session_id = None
                self._panel_top("session", PURPLE)
                print(f"{PURPLE}│{RESET} {DIM}new session started; previous context is not carried over{RESET}")
                self._panel_bottom(PURPLE)
                print()
                continue
            if text == "/help":
                self._panel_top("commands", PURPLE)
                for name, description in (
                    ("/new", "start a separate session"),
                    ("/status", "show daemon, model and resource status"),
                    ("/clear", "clear the screen and redraw the banner"),
                    ("/help", "show this list"),
                    ("/exit", "leave Kilobyte"),
                ):
                    print(f"{PURPLE}│{RESET} {GREEN}{name:<8}{RESET} {DIM}{description}{RESET}")
                print(f"{PURPLE}│{RESET} {DIM}anything else is sent to the local brain{RESET}")
                self._panel_bottom(PURPLE)
                print()
                continue
            if text == "/clear":
                sys.stdout.write("\033[2J\033[H")
                await self.banner()
                continue
            if text == "/status":
                try:
                    status = await self.client.request("status")
                except (ConnectionError, FileNotFoundError, OSError) as exc:
                    self._panel_top("status", YELLOW)
                    print(f"{YELLOW}│{RESET} daemon unavailable: {exc}")
                    self._panel_bottom(YELLOW)
                    print()
                    continue
                profile = status.get("profile") or {}
                memory = status.get("memory") or {}
                self._panel_top("status", PURPLE)
                for label, value in (
                    ("healthy", status.get("healthy")),
                    ("uptime", f"{status.get('uptime_seconds', 0)}s"),
                    ("model", Path(str(status.get("model", ""))).name),
                    ("context", f"{profile.get('context_size')}   threads {profile.get('threads')}   gpu layers {profile.get('gpu_layers')}"),
                    ("memory", f"{profile.get('available_mb')} MiB available of {profile.get('total_mb')} MiB"),
                    ("sessions", f"{memory.get('sessions')} sessions · {memory.get('messages')} messages · {memory.get('facts')} facts"),
                ):
                    print(f"{PURPLE}│{RESET} {DIM}{label:<9}{RESET}{value}")
                self._panel_bottom(PURPLE)
                print()
                continue
            self._panel_top("kilo", GREEN)
            # Run the exchange as a task so SIGINT can cancel just this generation
            # instead of tearing down the whole interface.
            task = asyncio.create_task(self.ask(text))
            loop = asyncio.get_running_loop()
            try:
                loop.add_signal_handler(signal.SIGINT, task.cancel)
            except (NotImplementedError, RuntimeError):
                loop = None
            try:
                await task
            except (KeyboardInterrupt, asyncio.CancelledError):
                # First ctrl-c cancels this generation only; the session continues.
                print(f"{DIM}generation cancelled — type /exit to leave{RESET}\n")
            except (ConnectionError, FileNotFoundError) as exc:
                print(f"{YELLOW}Daemon unavailable:{RESET} {exc}\nTry: sudo systemctl restart kilobyte")
                self._panel_bottom(YELLOW, "not delivered")
                print()
            finally:
                if loop is not None:
                    loop.remove_signal_handler(signal.SIGINT)
