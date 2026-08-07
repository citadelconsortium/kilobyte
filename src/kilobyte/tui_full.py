"""Full-screen Kilo terminal application.

A persistent layout that fills the window: a banner on top, a live stats bar, a scrollable
conversation that streams character by character, a runtime panel toggled with F2, and an
input box fixed at the bottom. The stats bar shows what Kilo is doing plus live numeric
counters — elapsed runtime, tools used, and tokens produced.

Everything visible animates so the interface always reads as alive: a light sweeps across
the wordmark, the status dot breathes, the activity glyph and word rotate with trailing
dots while Kilo works, and an idle wave drifts when it is not. There is no step counter —
a raw number that usually only reached "1" read as frozen.

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

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"       # default "thinking" spinner
MOON = "◐◓◑◒"                    # a tool is running: a turning quarter
ELLIPSIS = ("·  ", "·· ", "···", " ··", "  ·")  # "interpreting" drift
PULSE = "▁▂▃▄▅▆▇█▇▆▅▄▃▂"
ACTIVITY = ("thinking", "reasoning", "planning", "working", "composing", "considering")

# Which glyph set animates for a given activity phase. Falls back to the braille spinner.
def _phase_frames(phase: str) -> str:
    if phase.startswith(("running", "warming")):
        return MOON
    return SPINNER


STYLE = Style.from_dict({
    "banner": "#3fa869 bold",
    "banner.hi": "#d7ffd7 bold",   # the bright band that sweeps across the wordmark
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
        self.show_panel = False
        self.effort = "medium"
        self.agent_name = ""          # profile active this turn (auto-selected or forced)
        self.forced_profile = ""      # set by /agent; overrides auto-selection
        self._sessions: list[dict[str, Any]] = []
        self._active: asyncio.Task | None = None
        # Cloud escalation state. Local Kilo is always the default; /switch flips the
        # active brain to the last-configured cloud provider and back.
        self.cloud_active = False
        self.cloud_provider = ""
        self._pending: dict[str, Any] | None = None   # awaited inline input (selector / key)
        self._catalog: dict[str, Any] = {}
        self._cloud_options: list[tuple[str, dict[str, Any]]] = []
        # Strong refs to spawned background tasks. Without this, asyncio can garbage-
        # collect a task that is awaiting (e.g. an RPC round-trip) and silently cancel
        # it mid-run — which is why the /cloud setup appeared to do nothing.
        self._bg_tasks: set[asyncio.Task] = set()

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

    def _shimmer(self, art: str, row: int):
        """Split one wordmark line into segments with a bright band that sweeps across,
        giving the logo a diagonal light-sweep. Cheap: a handful of short segments."""
        span = len(art) + 14  # sweep a little past both edges so there is a brief pause
        head = self.spin % span
        out: list[tuple[str, str]] = [("class:banner", "  ")]
        for col, ch in enumerate(art):
            # Diagonal: the band leads on lower rows, so the highlight tilts as it moves.
            lit = ch != " " and abs(col - (head - row)) <= 1
            out.append(("class:banner.hi" if lit else "class:banner", ch))
        out.append(("class:banner", "   "))
        return out

    def _banner_text(self):
        online = bool(self.status.get("healthy"))
        prof = self.status.get("profile") or {}
        # A breathing dot animates even when idle, so the header never looks frozen.
        pulse = PULSE[self.spin % len(PULSE)]
        dot = f"{pulse} online" if online else "○ offline"
        info = [
            [("class:banner.hi", "KILOBYTE  "), ("class:on" if online else "class:off", dot)],
            [("class:tagline", "local-first · one model · no cloud by default")],
            [("class:dim", f"brain   {self.model_name}")],
            [("class:dim", f"context {prof.get('context_size','?')}   threads {prof.get('threads','?')}   gpu {prof.get('gpu_layers','?')}")],
            [("class:dim", "tools   files · shell · web · memory · skills")],
            [("class:tagline", "made by 0v3r51ght  ·  /help · F2 runtime · Ctrl-Q quit")],
        ]
        rows: list[tuple[str, str]] = []
        for i, art in enumerate(KILO_ART):
            rows += self._shimmer(art, i)
            rows += info[i] if i < len(info) else []
            rows.append(("", "\n"))
        return rows

    def _stats_bar(self):
        elapsed = (time.monotonic() - self.started) if (self.busy and self.started) else 0
        if self.busy:
            phase = self.phase or ACTIVITY[(self.spin // 10) % len(ACTIVITY)]
            if self.streaming:
                # A blinking caret shows tokens are actively arriving.
                caret = "▌" if (self.spin // 3) % 2 else " "
                head = [("class:stat", " ▌ "), ("class:kilo", "responding"), ("class:stat", caret)]
            else:
                frames = _phase_frames(phase)
                glyph = frames[self.spin % len(frames)]
                dots = ELLIPSIS[(self.spin // 3) % len(ELLIPSIS)]
                head = [("class:stat", f" {glyph} "), ("class:kilo", phase), ("class:dim", f" {dots}")]
        else:
            # A gentle wave drifts while idle so the bar is never static.
            wave = "".join(PULSE[(self.spin + i) % len(PULSE)] for i in range(3))
            head = [("class:stat", f" {wave} "), ("class:dim", "ready")]
        bar = head + [
            ("class:stat.k", "   ⏱ "), ("class:stat", f"{elapsed:0.0f}s"),
            ("class:stat.k", "   🔧 tools "), ("class:stat", f"{self.tools_used}"),
            ("class:stat.k", "   ⇥ tokens "), ("class:stat", f"{self.tokens}"),
            ("class:stat.k", "   effort "), ("class:stat", f"{self.effort}"),
            ("class:stat.k", "   ⬡ "),
            ("class:kilo", f"cloud·{self.cloud_provider}" if self.cloud_active else "kilo"),
        ]
        if self.agent_name:
            bar += [("class:stat.k", "   ◆ "), ("class:kilo", self.agent_name)]
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
            ("class:panel.key", " tools    "), ("", f"{self.tools_used}\n\n"),
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

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

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
        # An awaited answer (cloud provider pick or API key) is consumed here rather than
        # being sent to the model. Returning False also wipes the key from the input line.
        if self._pending is not None:
            self._spawn(self._resume_pending(text))
            return False
        if self._handle_command(text):
            return False
        self._append(f"\n{_you(text)}\n\n")
        self.tokens = self.tools_used = 0
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
                "  /agent <name>|off         force research|coding|security|systems, or auto\n"
                "  /chats                    list past sessions to resume\n"
                "  /chat <n>                 open a past session by number\n"
                "  /cloud [question]         set up / use a cloud model (key selector)\n"
                "  /switch                   flip between cloud and local Kilo (Kilo default)\n"
                "  /new · /clear · /quit\n"
                "keys: F2 runtime panel · Ctrl-C cancel · Ctrl-Q quit\n"
            )
            return True
        if text == "/chats":
            self._spawn(self._list_chats())
            return True
        if text.startswith("/chat "):
            self._spawn(self._open_chat(text.split(maxsplit=1)[1].strip()))
            return True
        if text.startswith("/agent"):
            parts = text.split()
            name = parts[1].lower() if len(parts) > 1 else ""
            name = {"hacking": "security", "hack": "security", "pentest": "security",
                    "chat": "conversation", "convo": "conversation"}.get(name, name)
            valid = {"research", "coding", "security", "systems", "general", "conversation"}
            if name in {"", "off", "auto"}:
                self.forced_profile = ""
                self._append("\n— agent auto-selection restored —\n")
            elif name in valid:
                self.forced_profile = name
                self.agent_name = name
                self._append(f"\n— forced {name} agent (use /agent off to auto-select) —\n")
            else:
                self._append(f"\n— unknown agent; choose {', '.join(sorted(valid))} —\n")
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
            # No provider yet: run the pick-and-key setup, carrying any question along.
            if not self.cloud_provider:
                self._spawn(self._cloud_setup(pending_question=rest or None))
                return True
            if not rest:
                self._append(
                    f"\n— cloud provider: {self.cloud_provider}. /switch to route here, "
                    f"or /cloud <question> for one message —\n"
                )
                return True
            self._append(f"\n{_you(rest)}\n\n")
            self.tokens = self.tools_used = 0
            self._active = asyncio.create_task(self._ask(rest, provider=self.cloud_provider))
            return True
        if text == "/switch":
            if not self.cloud_provider:
                self._append("\n— no cloud provider yet; run /cloud to set one up —\n")
                return True
            self.cloud_active = not self.cloud_active
            where = f"cloud · {self.cloud_provider}" if self.cloud_active else "local · Kilo"
            self._append(f"\n— switched to {where} —\n")
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

    async def _cloud_setup(self, pending_question: str | None = None) -> None:
        """Show the provider catalog and await a pick. Users only ever supply an API key:
        the base URL and default model come from the catalog."""
        try:
            data = await self.client.request("providers_catalog")
        except (ConnectionError, FileNotFoundError, OSError) as exc:
            self._append(f"\n⚠ could not load providers: {exc}\n")
            return
        self._catalog = data
        self._cloud_options = list((data.get("known") or {}).items())
        configured = set(data.get("configured", []))
        lines = ["\n☁ choose a cloud provider — type its number, then paste your API key:"]
        for i, (name, meta) in enumerate(self._cloud_options, 1):
            mark = "  ✓ configured" if name in configured else ""
            lines.append(f"  {i:>2}. {meta['label']:<12} {meta.get('model','')}{mark}")
        lines.append("  (type the number or name · blank line cancels)")
        self._append("\n".join(lines) + "\n")
        self._pending = {"kind": "cloud_pick", "question": pending_question}

    def _run_cloud(self, name: str, question: str | None) -> None:
        """Activate a configured provider and, if a question was queued, send it now."""
        self.cloud_provider = name
        self.cloud_active = True
        self._append(f"\n— routing to cloud · {name} (use /switch for local Kilo) —\n")
        if question:
            self._append(f"\n{_you(question)}\n\n")
            self.tokens = self.tools_used = 0
            self._active = asyncio.create_task(self._ask(question, provider=name))

    async def _resume_pending(self, text: str) -> None:
        pending = self._pending or {}
        self._pending = None
        kind = pending.get("kind")
        if kind == "cloud_pick":
            name = None
            names = [n for n, _ in self._cloud_options]
            if text.isdigit() and 1 <= int(text) <= len(names):
                name = names[int(text) - 1]
            elif text.strip().lower() in names:
                name = text.strip().lower()
            if not name:
                self._append("\n— cancelled cloud setup —\n")
                return
            if name in set(self._catalog.get("configured", [])):
                self._run_cloud(name, pending.get("question"))
                return
            self._append(f"\n☁ paste your {name} API key and press Enter:\n")
            self._pending = {"kind": "cloud_key", "name": name, "question": pending.get("question")}
            return
        if kind == "cloud_key":
            name = pending["name"]
            try:
                res = await self.client.request("configure_provider", name=name, api_key=text)
            except (ConnectionError, FileNotFoundError, OSError) as exc:
                self._append(f"\n⚠ could not save key: {exc}\n")
                return
            if not res.get("ok"):
                self._append(f"\n⚠ {res.get('error', 'could not configure provider')}\n")
                return
            self._append(f"\n✓ {res.get('label', name)} configured.\n")
            self._run_cloud(name, pending.get("question"))

    async def _ask(self, text: str, provider: str | None = None) -> None:
        # A plain message follows the active brain: local Kilo by default, the last cloud
        # provider after /switch.
        if provider is None and self.cloud_active and self.cloud_provider:
            provider = self.cloud_provider
        self.busy = True
        self.streaming = False
        self.agent_name = ""
        self.phase = "thinking"
        self.started = time.monotonic()
        reader = writer = None
        try:
            reader, writer = await asyncio.open_unix_connection(self.client.socket_path)
            req: dict[str, Any] = {"command": "chat", "text": text, "session_id": self.session_id, "cwd": str(Path.cwd()), "effort": self.effort}
            if self.forced_profile:
                req["agent_profile"] = self.forced_profile
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
                elif kind == "agent":
                    self.agent_name = event.get("profile", "")
                    self._append(f"◆ {event.get('profile')} agent — {event.get('hint','')}\n")
                elif kind == "warming":
                    self.phase = "warming cache (one-off)"
                    self._append("⏳ warming the prompt cache — one-off after a change\n")
                elif kind == "thinking":
                    self.phase = "thinking"
                    self.streaming = False
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
            # Always invalidate so the header dot and idle wave keep moving; the rate is
            # modest, so this is cheap even while nothing is happening.
            self.app.invalidate()
            await asyncio.sleep(0.12)

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
