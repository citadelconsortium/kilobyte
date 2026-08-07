"""Full-screen Kilo terminal application.

A persistent layout that fills the window: a banner on top, a live stats bar, a scrollable
conversation that streams character by character, a runtime panel toggled with F2, and an
input box fixed at the bottom. The stats bar shows what Kilo is doing plus live numeric
counters — elapsed runtime, tools used, tool steps, and tokens produced.

Built on prompt_toolkit's widgets (TextArea) so input focus and scrolling are handled
robustly. When prompt_toolkit or a real terminal is unavailable, cli.py falls back to the
streaming line-based UI, so nothing here is a hard requirement.

Inference happens in the daemon over the Unix socket; this process only renders and
forwards keystrokes, so the interface stays responsive while a reply streams.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea

from .rpc import RPCClient

KILO_ART = (
    "██╗  ██╗██╗██╗      ██████╗ ",
    "██║ ██╔╝██║██║     ██╔═══██╗",
    "█████╔╝ ██║██║     ██║   ██║",
    "██╔═██╗ ██║██║     ██║   ██║",
    "██║  ██╗██║███████╗╚██████╔╝",
    "╚═╝  ╚═╝╚═╝╚══════╝ ╚═════╝ ",
)

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
PULSE = "▁▂▃▄▅▆▇█▇▆▅▄▃▂"
ACTIVITY = ("thinking", "reasoning", "planning", "working", "composing", "considering")

STYLE = Style.from_dict({
    "banner": "#5fd787 bold",
    "tagline": "#8a8a8a",
    "on": "#5fd787 bold",
    "off": "#ffd75f bold",
    "sep": "#3a3a3a",
    "stat": "#5fd787",
    "stat.k": "#8a8a8a",
    "you": "#5fafff bold",
    "kilo": "#5fd787",
    "dim": "#8a8a8a",
    "warn": "#ffd75f",
    "err": "#ff5f5f",
    "panel.title": "#af87ff bold",
    "panel.key": "#8a8a8a",
    "output": "bg:#0a0a0a",
    "prompt": "#5fafff bold",
})


class KiloApp:
    def __init__(self, client: RPCClient):
        self.client = client
        self.session_id: str | None = None
        self.model_name = "local brain"
        self.status: dict[str, Any] = {}

        self.busy = False          # a request is in flight
        self.streaming = False     # tokens are currently arriving
        self.phase = ""
        self.started = 0.0
        self.spin = 0
        # Live numeric counters for the stats bar.
        self.tokens = 0
        self.tools_used = 0
        self.steps = 0
        self.show_panel = False
        self.effort = "medium"
        self._sessions: list[dict[str, Any]] = []
        self._active: asyncio.Task | None = None

        self.output = TextArea(
            text="", read_only=True, scrollbar=True, wrap_lines=True,
            focusable=False, style="class:output",
        )
        self.input = TextArea(
            height=1, multiline=False, wrap_lines=False, prompt="  › ",
            style="class:prompt", accept_handler=self._accept,
        )
        self._build_layout()

    # ---- layout -------------------------------------------------------------

    def _banner_text(self):
        online = bool(self.status.get("healthy"))
        prof = self.status.get("profile") or {}
        info = [
            [("class:banner", "KILOBYTE  "), ("class:on" if online else "class:off", "● online" if online else "● offline")],
            [("class:tagline", "local-first · one model · no cloud by default")],
            [("class:dim", f"brain   {self.model_name}")],
            [("class:dim", f"context {prof.get('context_size','?')}   threads {prof.get('threads','?')}   gpu {prof.get('gpu_layers','?')}")],
            [("class:dim", "tools   files · shell · web · memory · skills")],
            [("class:tagline", "/help · F2 runtime · Ctrl-C cancel · Ctrl-Q quit")],
        ]
        rows: list[tuple[str, str]] = []
        for i, art in enumerate(KILO_ART):
            rows.append(("class:banner", "  " + art + "   "))
            rows += info[i] if i < len(info) else []
            rows.append(("", "\n"))
        return rows

    def _stats_bar(self):
        elapsed = (time.monotonic() - self.started) if (self.busy and self.started) else 0
        if self.busy:
            glyph = SPINNER[self.spin % len(SPINNER)]
            phase = self.phase or ACTIVITY[(self.spin // 10) % len(ACTIVITY)]
            head = [("class:stat", f" {glyph} "), ("class:kilo", f"{phase}")]
        else:
            head = [("class:on", " ● "), ("class:dim", "ready")]
        bar = head + [
            ("class:stat.k", "   ⏱ "), ("class:stat", f"{elapsed:0.0f}s"),
            ("class:stat.k", "   ◆ steps "), ("class:stat", f"{self.steps}"),
            ("class:stat.k", "   🔧 tools "), ("class:stat", f"{self.tools_used}"),
            ("class:stat.k", "   ⇥ tokens "), ("class:stat", f"{self.tokens}"),
            ("class:stat.k", "   effort "), ("class:stat", f"{self.effort}"),
        ]
        return bar

    def _panel_text(self):
        prof = self.status.get("profile") or {}
        mem = self.status.get("memory") or {}
        return [
            ("class:panel.title", " RUNTIME\n\n"),
            ("class:panel.key", " model    "), ("", f"{self.model_name}\n"),
            ("class:panel.key", " healthy  "), ("", f"{self.status.get('healthy')}\n"),
            ("class:panel.key", " uptime   "), ("", f"{self.status.get('uptime_seconds',0)}s\n"),
            ("class:panel.key", " context  "), ("", f"{prof.get('context_size','?')}\n"),
            ("class:panel.key", " threads  "), ("", f"{prof.get('threads','?')}\n"),
            ("class:panel.key", " gpu      "), ("", f"{prof.get('gpu_layers','?')} layers\n"),
            ("class:panel.key", " memory   "), ("", f"{prof.get('available_mb','?')} MiB\n\n"),
            ("class:panel.title", " THIS TURN\n\n"),
            ("class:panel.key", " tokens   "), ("", f"{self.tokens}\n"),
            ("class:panel.key", " tools    "), ("", f"{self.tools_used}\n"),
            ("class:panel.key", " steps    "), ("", f"{self.steps}\n\n"),
            ("class:panel.title", " MEMORY\n\n"),
            ("class:panel.key", " sessions "), ("", f"{mem.get('sessions','?')}\n"),
            ("class:panel.key", " facts    "), ("", f"{mem.get('facts','?')}\n"),
        ]

    def _build_layout(self) -> None:
        panel = ConditionalContainer(
            VSplit([
                Window(width=1, char="│", style="class:sep"),
                Window(FormattedTextControl(self._panel_text), width=30),
            ]),
            filter=Condition(lambda: self.show_panel),
        )
        root = HSplit([
            Window(FormattedTextControl(self._banner_text), height=len(KILO_ART)),
            Window(height=1, char="─", style="class:sep"),
            VSplit([self.output, panel]),
            Window(height=1, char="─", style="class:sep"),
            Window(FormattedTextControl(self._stats_bar), height=1),
            Window(height=1, char="─", style="class:sep"),
            self.input,
        ])
        self.layout = Layout(root, focused_element=self.input)

    def _append(self, text: str) -> None:
        buff = self.output.buffer
        new = buff.text + text
        buff.set_document(Document(new, len(new)), bypass_readonly=True)

    # ---- interaction --------------------------------------------------------

    def _accept(self, buff) -> bool:
        text = buff.text.strip()
        # Returning False clears the input for the next message.
        if not text:
            return False
        if self._handle_command(text):
            return False
        self._append(f"\n{_you(text)}\n\n")
        self.tokens = self.tools_used = self.steps = 0
        self._active = asyncio.create_task(self._ask(text))
        return False

    def _handle_command(self, text: str) -> bool:
        if text in {"/quit", "/exit", "/q", "quit", "exit"}:
            self.app.exit()
            return True
        if text == "/clear":
            self.output.buffer.set_document(Document("", 0), bypass_readonly=True)
            return True
        if text == "/new":
            self.session_id = None
            self._append("\n— new session —\n")
            return True
        if text == "/help":
            self._append(
                "\ncommands:\n"
                "  /effort high|medium|low   depth vs speed of replies\n"
                "  /chats                    list past sessions to resume\n"
                "  /chat <n>                 open a past session by number\n"
                "  /cloud <question>         send one message to a cloud model\n"
                "  /new · /clear · /quit\n"
                "keys: F2 runtime panel · Ctrl-C cancel · Ctrl-Q quit\n"
            )
            return True
        if text == "/chats":
            asyncio.create_task(self._list_chats())
            return True
        if text.startswith("/chat "):
            asyncio.create_task(self._open_chat(text.split(maxsplit=1)[1].strip()))
            return True
        if text.startswith("/effort"):
            parts = text.split()
            level = parts[1].lower() if len(parts) > 1 else ""
            if level in {"high", "medium", "low"}:
                self.effort = level
                self._append(f"\n— effort set to {level} —\n")
            else:
                self._append("\n— use /effort high|medium|low —\n")
            return True
        if text.startswith("/cloud"):
            rest = text[len("/cloud"):].strip()
            if not rest:
                self._append("\n— usage: /cloud <question> —\n")
                return True
            self._append(f"\n{_you(rest)}\n\n")
            self.tokens = self.tools_used = self.steps = 0
            self._active = asyncio.create_task(self._ask(rest, provider=""))
            return True
        return False

    async def _list_chats(self) -> None:
        try:
            data = await self.client.request("sessions")
        except (ConnectionError, FileNotFoundError, OSError) as exc:
            self._append(f"\n⚠ could not list sessions: {exc}\n")
            return
        self._sessions = data.get("sessions", [])
        if not self._sessions:
            self._append("\n— no past sessions yet —\n")
            return
        lines = ["\npast sessions — /chat <n> to resume:"]
        for i, s in enumerate(self._sessions, 1):
            title = (s.get("title") or "").strip() or "(untitled)"
            lines.append(f"  {i:>2}. {title[:56]}  · {s.get('messages',0)} msgs")
        self._append("\n".join(lines) + "\n")

    async def _open_chat(self, arg: str) -> None:
        try:
            session = self._sessions[int(arg) - 1]
        except (ValueError, IndexError):
            self._append("\n— unknown chat number; run /chats first —\n")
            return
        self.session_id = session["id"]
        try:
            data = await self.client.request("session_history", session_id=self.session_id)
        except (ConnectionError, FileNotFoundError, OSError) as exc:
            self._append(f"\n⚠ could not load session: {exc}\n")
            return
        self.output.buffer.set_document(Document("", 0), bypass_readonly=True)
        self._append(f"— resumed session · {session.get('messages',0)} messages —\n")
        for m in data.get("messages", []):
            self._append(f"\n{_you(m['content']) if m['role']=='user' else m['content']}\n")
        self._append("\n— continue below —\n")

    async def _ask(self, text: str, provider: str | None = None) -> None:
        self.busy = True
        self.streaming = False
        self.phase = "thinking"
        self.started = time.monotonic()
        reader = writer = None
        try:
            reader, writer = await asyncio.open_unix_connection(self.client.socket_path)
            req: dict[str, Any] = {"command": "chat", "text": text, "session_id": self.session_id, "cwd": str(Path.cwd()), "effort": self.effort}
            if provider is not None:
                req["provider"] = provider
            writer.write((json.dumps(req) + "\n").encode())
            await writer.drain()
            while raw := await reader.readline():
                event = json.loads(raw)
                kind = event.get("type")
                if kind == "session":
                    self.session_id = event["session_id"]
                elif kind == "brain":
                    self.model_name = event.get("label", self.model_name)
                    if event.get("location") == "cloud":
                        self._append(f"☁ escalated to {event.get('label')}\n")
                elif kind == "warming":
                    self.phase = "warming cache (one-off)"
                    self._append("⏳ warming the prompt cache — one-off after a change\n")
                elif kind == "thinking":
                    self.phase = "thinking"
                    self.streaming = False
                    self.steps += 1
                elif kind == "token":
                    self.streaming = True
                    self.tokens += 1
                    self._append(event.get("text", ""))
                elif kind == "tool_start":
                    self.tools_used += 1
                    self.phase = f"running {event['name']}"
                    self.streaming = False
                    args = event.get("arguments") or {}
                    detail = ", ".join(f"{k}={str(v)[:32]}" for k, v in list(args.items())[:2])
                    self._append(f"\n◈ {event['name']} {detail}\n")
                elif kind == "tool_end":
                    ok = "✓" if event.get("ok") else "!"
                    self._append(f"  {ok} {event.get('name')} · {str(event.get('summary',''))[:90]}\n")
                    self.phase = "interpreting"
                elif kind == "error":
                    self._append(f"\n⚠ {event.get('error')}\n")
                elif kind == "done":
                    break
                self.app.invalidate()
        except asyncio.CancelledError:
            self._append("\n[cancelled]\n")
        except (ConnectionError, FileNotFoundError, OSError) as exc:
            self._append(f"\n⚠ daemon unavailable: {exc}\n")
        finally:
            if writer is not None:
                writer.close()
            self.busy = self.streaming = False
            self.phase = ""
            self._append("\n")
            self.app.invalidate()

    def _bindings(self) -> KeyBindings:
        kb = KeyBindings()

        @kb.add("c-q")
        @kb.add("c-d")
        def _(event):
            event.app.exit()

        @kb.add("c-l")
        def _(event):
            self.output.buffer.set_document(Document("", 0), bypass_readonly=True)

        @kb.add("f2")
        def _(event):
            self.show_panel = not self.show_panel

        @kb.add("c-c")
        def _(event):
            if self._active and not self._active.done():
                self._active.cancel()
            else:
                event.app.exit()

        return kb

    async def _tick(self) -> None:
        """Animate the spinner and refresh status so the interface always feels alive."""
        n = 0
        while True:
            self.spin += 1
            n += 1
            if n % 25 == 0:  # ~ every 2.5s
                try:
                    self.status = await self.client.request("status")
                    self.model_name = Path(str(self.status.get("model", ""))).stem or self.model_name
                except Exception:
                    pass
            if self.busy:
                self.app.invalidate()
            await asyncio.sleep(0.1)

    async def run(self) -> None:
        try:
            self.status = await self.client.request("status")
            self.model_name = Path(str(self.status.get("model", ""))).stem or self.model_name
        except Exception:
            pass
        self.app = Application(
            layout=self.layout,
            key_bindings=self._bindings(),
            style=STYLE,
            full_screen=True,
            mouse_support=True,
        )
        ticker = asyncio.create_task(self._tick())
        try:
            await self.app.run_async()
        finally:
            ticker.cancel()


def _you(text: str) -> str:
    return f"› {text}"


async def run_full_tui(client: RPCClient) -> bool:
    """Run the full-screen UI. Returns False if it could not start, so the caller can fall
    back to the line-based UI."""
    try:
        await KiloApp(client).run()
        return True
    except Exception:
        return False
