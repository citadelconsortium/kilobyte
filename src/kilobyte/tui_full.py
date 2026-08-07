"""Full-screen Kilo terminal application.

A persistent layout that fills the window: a banner and live status bar at the top, a
scrollable conversation that grows as you talk, a runtime panel that can be toggled, and
an input box fixed at the bottom. Assistant output streams in character by character.

Built on prompt_toolkit because a fixed-region, always-visible layout with a live input
box is exactly what it is for; doing it in raw ANSI would be fragile. When prompt_toolkit
or a real terminal is unavailable, cli.py falls back to the streaming line-based UI, so
nothing here is a hard requirement.

The heavy work — inference — happens in the daemon over the Unix socket. This process only
renders and forwards keystrokes, so the interface stays responsive while a reply streams.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, VSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.filters import Condition
from prompt_toolkit.styles import Style

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
ACTIVITY = ("thinking", "reasoning", "planning", "working", "composing")

STYLE = Style.from_dict({
    "banner": "#5fd787 bold",
    "tagline": "#8a8a8a",
    "status": "#5fd787",
    "status.off": "#ffd75f",
    "sep": "#3a3a3a",
    "you": "#5fafff bold",
    "kilo": "#5fd787 bold",
    "tool": "#af87ff",
    "dim": "#8a8a8a",
    "warn": "#ffd75f",
    "err": "#ff5f5f",
    "input": "#5fafff",
    "panel.title": "#af87ff bold",
    "panel.key": "#8a8a8a",
})


class KiloApp:
    def __init__(self, client: RPCClient):
        self.client = client
        self.session_id: str | None = None
        self.model_name: str = "local brain"
        self.status: dict[str, Any] = {}

        # Conversation is a list of (style, text) fragments rendered into the output window.
        self.fragments: list[tuple[str, str]] = []
        self.streaming = False
        self.phase = ""
        self.started = 0.0
        self.spin = 0
        self.show_panel = False
        self.effort = "medium"
        self._active: asyncio.Task | None = None

        self.input = Buffer(multiline=False, accept_handler=self._on_submit)
        self._build_layout()

    # ---- layout -------------------------------------------------------------

    def _banner_text(self):
        online = bool(self.status.get("healthy"))
        prof = self.status.get("profile") or {}
        dot = ("class:status", "●  ") if online else ("class:status.off", "●  ")
        rows = []
        info = [
            [("class:banner", "KILOBYTE  "), dot, ("class:status" if online else "class:status.off", "online" if online else "offline")],
            [("class:tagline", "local-first · one model · no cloud by default")],
            [("class:dim", f"brain   {self.model_name}")],
            [("class:dim", f"context {prof.get('context_size','?')}   threads {prof.get('threads','?')}   gpu {prof.get('gpu_layers','?')}")],
            [("class:dim", "tools   files · shell · web · memory · skills")],
            [("class:tagline", "F2 runtime · Ctrl-L clear · Ctrl-C cancel · Ctrl-Q quit")],
        ]
        for i, art in enumerate(KILO_ART):
            line: list[tuple[str, str]] = [("class:banner", "  " + art + "   ")]
            line += info[i] if i < len(info) else []
            line.append(("", "\n"))
            rows += line
        return rows

    def _status_bar(self):
        if self.streaming or self.phase:
            glyph = SPINNER[self.spin % len(SPINNER)]
            phase = self.phase or ACTIVITY[(self.spin // 12) % len(ACTIVITY)]
            elapsed = time.monotonic() - self.started if self.started else 0
            return [("class:status", f" {glyph} "), ("class:kilo", phase), ("class:dim", f"  {elapsed:0.0f}s · {self.model_name} · effort {self.effort}  (ctrl-c to cancel)")]
        return [("class:dim", "  ready — type a message and press Enter")]

    def _panel_text(self):
        prof = self.status.get("profile") or {}
        mem = self.status.get("memory") or {}
        rows = [
            ("class:panel.title", " RUNTIME\n\n"),
            ("class:panel.key", " model    "), ("", f"{self.model_name}\n"),
            ("class:panel.key", " healthy  "), ("", f"{self.status.get('healthy')}\n"),
            ("class:panel.key", " uptime   "), ("", f"{self.status.get('uptime_seconds',0)}s\n"),
            ("class:panel.key", " context  "), ("", f"{prof.get('context_size','?')}\n"),
            ("class:panel.key", " threads  "), ("", f"{prof.get('threads','?')}\n"),
            ("class:panel.key", " gpu      "), ("", f"{prof.get('gpu_layers','?')} layers\n"),
            ("class:panel.key", " memory   "), ("", f"{prof.get('available_mb','?')} MiB free\n"),
            ("class:panel.key", " sessions "), ("", f"{mem.get('sessions','?')}\n"),
            ("class:panel.key", " messages "), ("", f"{mem.get('messages','?')}\n"),
            ("class:panel.key", " facts    "), ("", f"{mem.get('facts','?')}\n"),
        ]
        return rows

    def _build_layout(self) -> None:
        rule = Window(height=1, char="─", style="class:sep")
        output = Window(
            BufferControl(buffer=self._output_buffer(), focusable=False),
            wrap_lines=True, always_hide_cursor=True,
        )
        self.output_window = output
        panel = ConditionalContainer(
            content=VSplit([
                Window(width=1, char="│", style="class:sep"),
                Window(FormattedTextControl(self._panel_text), width=32),
            ]),
            filter=Condition(lambda: self.show_panel),
        )
        status = Window(FormattedTextControl(self._status_bar), height=1)
        input_win = VSplit([
            Window(FormattedTextControl([("class:you", " › ")]), width=3),
            Window(BufferControl(buffer=self.input), height=1),
        ])
        body = VSplit([HSplit([output], padding=0), panel])
        root = HSplit([
            Window(FormattedTextControl(self._banner_text), height=len(KILO_ART)),
            rule,
            body,
            Window(height=1, char="─", style="class:sep"),
            status,
            input_win,
        ])
        self.layout = Layout(root, focused_element=self.input)

    # The output buffer holds ANSI-coloured conversation text; we append to it and keep
    # the view scrolled to the bottom as tokens stream in.
    def _output_buffer(self) -> Buffer:
        self.output = Buffer(read_only=False, multiline=True)
        return self.output

    def _append(self, text: str) -> None:
        doc = self.output.text + text
        self.output.set_document_from_text(doc, bypass_readonly=True)
        # Keep the newest content in view.
        self.output.cursor_position = len(self.output.text)

    # ---- interaction --------------------------------------------------------

    def _on_submit(self, buff: Buffer) -> bool:
        text = buff.text.strip()
        if not text:
            return False
        self.input.reset()
        if text in {"/quit", "/exit", "/q", "quit", "exit"}:
            self.app.exit()
            return False
        if text == "/clear":
            self.output.set_document_from_text("", bypass_readonly=True)
            return False
        if text == "/new":
            self.session_id = None
            self._append("\n— new session; previous context cleared —\n")
            return False
        if text == "/help":
            self._append(
                "\ncommands:\n"
                "  /effort high|medium|low   depth vs speed of replies\n"
                "  /cloud <question>         send one message to a cloud model\n"
                "  /new                      start a fresh session\n"
                "  /clear                    clear the screen\n"
                "  /quit                     leave\n"
                "keys: F2 runtime · Ctrl-L clear · Ctrl-C cancel · Ctrl-Q quit\n"
            )
            return False
        if text.startswith("/effort"):
            parts = text.split()
            level = parts[1].lower() if len(parts) > 1 else ""
            if level in {"high", "medium", "low"}:
                self.effort = level
                self._append(f"\n— effort set to {level} —\n")
            else:
                self._append(f"\n— effort is {self.effort}; use /effort high|medium|low —\n")
            return False
        provider = None
        if text.startswith("/cloud"):
            rest = text[len("/cloud"):].strip()
            if not rest:
                self._append("\n— usage: /cloud <question> (sends one message to a cloud model) —\n")
                return False
            provider, text = "", rest
        self._append(f"\n› {text}\n\n")
        self._active = asyncio.create_task(self._ask(text, provider))
        return False

    async def _ask(self, text: str, provider: str | None = None) -> None:
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
                elif kind == "warming":
                    self.phase = "warming cache (one-off)"
                    self._append("⏳ first run after a change: warming the prompt cache\n")
                elif kind == "thinking":
                    self.phase = "thinking"
                    self.streaming = False
                elif kind == "token":
                    self.streaming = True
                    self._append(event.get("text", ""))
                elif kind == "tool_start":
                    self.phase = f"running {event['name']}"
                    self.streaming = False
                    args = event.get("arguments") or {}
                    detail = ", ".join(f"{k}={str(v)[:32]}" for k, v in list(args.items())[:2])
                    self._append(f"\n◈ {event['name']} {detail}\n")
                elif kind == "tool_end":
                    ok = "✓" if event.get("ok") else "!"
                    self._append(f"  {ok} {event.get('name')} · {event.get('summary','')[:100]}\n")
                    self.phase = "interpreting"
                elif kind == "error":
                    self._append(f"\n⚠ {event.get('error')}\n")
                elif kind == "done":
                    break
                self.app.invalidate()
        except asyncio.CancelledError:
            self._append("\n[cancelled]\n")
            raise
        except (ConnectionError, FileNotFoundError, OSError) as exc:
            self._append(f"\n⚠ daemon unavailable: {exc}\n")
        finally:
            if writer is not None:
                writer.close()
            self.streaming = False
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
            self.output.set_document_from_text("", bypass_readonly=True)

        @kb.add("f2")
        def _(event):
            self.show_panel = not self.show_panel

        @kb.add("c-c")
        def _(event):
            # Cancel the active generation; a second press with nothing running exits.
            if self._active and not self._active.done():
                self._active.cancel()
            else:
                event.app.exit()

        return kb

    async def _tick(self) -> None:
        """Drive the spinner and refresh status so the interface always feels alive."""
        while True:
            self.spin += 1
            if self.spin % 30 == 0:  # refresh runtime status a few times a second is wasteful; do it ~every 3s
                try:
                    self.status = await self.client.request("status")
                    self.model_name = Path(str(self.status.get("model", ""))).stem or self.model_name
                except Exception:
                    pass
            if self.streaming or self.phase:
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


async def run_full_tui(client: RPCClient) -> bool:
    """Run the full-screen UI. Returns False if it could not start, so the caller can
    fall back to the line-based UI."""
    try:
        await KiloApp(client).run()
        return True
    except Exception:
        return False
