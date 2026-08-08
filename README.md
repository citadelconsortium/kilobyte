<p align="center"><img src="assets/kilo-mascot.svg" width="132" alt="Kilo, the Kilobyte mascot"></p>

<h1 align="center">Kilobyte</h1>
<p align="center"><b>The local-first AI agent that actually does the work — not another chat wrapper.</b></p>

<p align="center"><i>Reads your files · runs your shell · researches the web · remembers across sessions · escalates to a frontier cloud model on demand — all from one prebuilt brain, fully offline-capable, in a TUI that looks like a pro tool.</i></p>

### Why Kilobyte beats the rest
- **It acts, it doesn’t just talk** — real tools (shell, files, web), and it works *until the task is done*, not until a step counter runs out.
- **Private by default** — your brain runs locally; nothing leaves the box unless you send it to /cloud on purpose.
- **Powerful on demand** — /cloud escalation hands any of 14 frontier providers your machine + your tools.
- **Grounded & honest** — an orchestrator commissions specialist agents (coding, security, research, systems, private) over an offline reference bank.
- **Production-ready** — one-line installer, checksum-verified brains with rollback, 100+ tests, systemd, Telegram.

---


Kilobyte is a local-first terminal AI built around exactly one prebuilt GGUF brain. A persistent
`llama-server` instance provides inference; a deterministic Python framework owns IPC, resources,
tool validation, permissions, machine/web access, bounded SQLite memory, Telegram policy, and UI.
The local brain is the default and never leaves the machine; optional, explicit cloud
escalation (/cloud) exists for when more power is wanted. The brain itself is produced by a
separate, reproducible training pipeline.

## Install

Kilobyte runs on any modern **Linux** distribution. The one-line installer targets
Arch (it uses `pacman`); on other distros install the handful of dependencies with your
package manager first (see below), then run the manual install.

```bash
curl -fsSL https://raw.githubusercontent.com/citadelconsortium/kilobyte/main/scripts/install-online.sh | bash
kilo
```

On Arch, the installer handles system dependencies (`llama-cpp`, `python`, `curl`, `sqlite`,
`ripgrep`), creates the service user if needed, installs the app, downloads and verifies the
model, and starts the service — nothing else is required.

On any other Linux, install those same packages with your distro's package manager
(`apt`, `dnf`, `zypper`, …) — anything providing `llama-server`, Python 3.11+, `curl`,
`sqlite3` and `ripgrep` — then use the manual install below. Everything above the OS package
layer is portable.

To install manually from a local checkout instead:

```bash
sudo ./scripts/install.sh
sudo KILOBYTE_USER=kilobyte ./scripts/install-model.sh
sudo systemctl start kilobyte
kilo doctor --verify-model
kilo benchmark
kilo
```

The model download is resumable, checksum-pinned, and atomically renamed only after verification.
Once installed, chat, memory, machine tools, and the UI operate offline. Web search/fetch and optional
Telegram naturally require a network.

## Commands

| Command | Purpose |
|---|---|
| `kilo` | Open the interactive TUI |
| `kilo chat "…"` | Send one prompt and stream the answer |
| `kilo status` | Daemon, model and resource status |
| `kilo doctor` | Health checks (`--verify-model` also checks the SHA-256) |
| `kilo resources` | Live resource profile |
| `kilo model-info` | The one installed brain |
| `kilo benchmark` | Measure a real inference |
| `kilo logs` | Service logs |
| `kilo start` / `stop` / `restart` | Service control |
| `kilo version` | Framework version |

### The interface

Running `kilo` opens a full-screen terminal app (built on `prompt_toolkit`): a banner with
live status on top, a scrollable conversation that streams token by token, a stats bar
showing the current action with live numeric counters (runtime, tools used, tokens), and an
input box at the bottom. **F2** toggles a runtime panel; **Ctrl-C** cancels the running
generation without leaving; **Ctrl-Q** quits. On a terminal without `prompt_toolkit` it falls
back to a streaming line-based UI automatically.

In-TUI commands:

| Command | Purpose |
|---|---|
| `/effort high\|medium\|low` | Trade reply depth and tool-step budget for speed |
| `/chats` · `/chat <n>` | List past sessions and resume one |
| `/cloud [question]` | Set up or use a cloud model — pick a provider, paste an API key |
| `/switch` | Flip between the cloud provider and local Kilo (Kilo is the default) |
| `/private [on\|off\|rotate]` | Mask web searches/fetches through Tor — hide IP, rotate exit |
| `/cancel` | Stop the running request and clear the queue |
| `/agent <name>` | Force a specialist mode (research, coding, security/hacking, systems, conversation, private) |
| `/new` · `/clear` · `/help` · `/quit` | Session and screen control |

## Tools

`read_file`, `write_file`, `list_files`, `search_files`, `run_command`, `system_info`,
`web_search`, `web_fetch`, `remember`, `recall`, `save_skill`, `list_skills`,
`search_history`, `reference` (offline cheat-sheet bank). MCP servers can add more.

Results are compacted before reaching the model, so a large directory listing or command output
cannot displace the conversation.

## Grounding — how Kilo avoids nonsense

A small local model cannot be made not to hallucinate by weights alone, so the framework
forces it to work from evidence. The system prompt requires Kilo to get facts with a tool
rather than recall them, never to invent output or results, and to say it is not certain
rather than guess. Sampling runs at a low temperature to curb confident confabulation.

Specialist **agent profiles** sharpen this per domain — `research` (retrieve, corroborate
across sources, cite), `coding` (never claim code works without running it), `security` (the
hacking agent: **offensive and defensive**, with a recon→enumerate→exploit→privesc→report
playbook that acts on operator-given targets), `systems` (diagnose from the live machine),
`conversation` (the default: understand intent, then follow through), and `private` (web
work routed through Tor). Kilo picks a
profile from the request or you name one with `/agent`; the active profile shows in the
stats bar. Profiles are added after the cached base prompt, so switching one does not slow
the response.

Two framework guarantees back this up. **Follow-through:** if the model only announces an
action ("let me calculate…") without doing it, the loop nudges it once to actually finish
rather than saving the promise as the answer. **Recall:** relevant lines from earlier
sessions are surfaced automatically, so Kilo remembers what was discussed before instead of
relying on the small model to reach for the history tool.

## Skills

Once a multi-step task works, Kilo can record it as a reusable procedure. Matching
procedures are surfaced automatically on later requests, so the same work is repeated
rather than replanned -- a few hundred tokens of known-good steps instead of several
planning rounds. Outcomes are tracked, so procedures that keep working sort first, and
the registry is bounded.

## MCP servers

Tools from Model Context Protocol servers can be offered to Kilo alongside the built-in
ones. Copy `config/mcp.example.json` to `/etc/kilobyte/mcp.json`:

```json
{
  "servers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/home/kilobyte"]
    }
  }
}
```

Servers are launched as subprocesses over the stdio transport (MCP 2025-06-18) and their
tools appear as `mcp__<server>__<tool>`. They are treated as untrusted: a tool without a
usable input schema is not shown to the model, calling one requires permission like any
other outward action, results pass through the same compaction, a server that hangs hits
a request timeout instead of blocking Kilobyte, and one that fails to start is skipped
rather than taking the daemon down. MCP tools are never offered over Telegram.

## Cloud escalation (optional)

The brain is the local model. When a request needs more power than it has, one message
can be sent to a hosted model on purpose:

```
/cloud summarise this architecture and find the weak points
/cloud openrouter <question>          # pick a specific provider
```

Easiest: just type `/cloud` in the TUI, pick a provider from the list and paste your
API key — the base URL and a sensible default model are filled in for you, and the
provider is saved (0600) and made the default. `/switch` then flips the active brain
between that provider and local Kilo; Kilo is always the default.

Configure providers in `/etc/kilobyte/providers.json` (see `config/providers.example.json`):

```json
{
  "default": "openrouter",
  "providers": {
    "openrouter": {
      "base_url": "https://openrouter.ai/api/v1",
      "api_key": "sk-or-...",
      "model": "anthropic/claude-sonnet-4.5"
    }
  }
}
```

```bash
sudo install -m 0600 -o kilobyte -g kilobyte config/providers.example.json /etc/kilobyte/providers.json
sudo nano /etc/kilobyte/providers.json   # add your key
```

Escalated (cloud) models are given the **same tools** the local model has — terminal,
files, web, memory, reference — so a frontier model works *through* the framework rather
than guessing blind. `/model` lists the provider's models (free ones for OpenRouter) so you
can switch without hunting for names; the active model, token usage and context show live
in the stats bar and F2 panel.

The rules are deliberate: local is always the default, escalation happens only for a
message you prefixed with `/cloud` and lasts exactly that one message, there is no
automatic fallback when the local model is slow or fails, the reply is labelled with the
brain that produced it, and with no providers file there is no cloud path at all. Keys
live in a `0600` file, travel in a header over HTTPS only, and are never logged. Cloud
escalation is not available over Telegram.

## Private mode (Tor)

`/private on` routes every `web_search` and `web_fetch` through **Tor**, so lookups leave
the machine from a Tor exit IP instead of yours, and DNS is resolved through Tor too (no
local leak). `/private rotate` pulls a fresh circuit (new exit IP); `/private off` returns
to direct requests. The active state shows as `🛡 private` in the stats bar.

It is **fail-closed**: if Tor is unreachable, a private request is **refused, never sent
unmasked** — privacy is never silently dropped. Requires Tor on the host:

```bash
sudo pacman -S tor python-pysocks     # or your distro's equivalent
sudo systemctl enable --now tor
sudo usermod -aG tor kilobyte          # so the daemon can rotate circuits; restart kilobyte after
```

Without Tor installed, `/private` still exists but every masked request is refused (by
design) rather than exposing your IP. Private mode is terminal-only, never over Telegram.

## Requests queue automatically

Send several messages in a row and Kilo handles them **one at a time** — there is a single
inference slot, so requests are queued (`⧉ queued N` in the stats bar, `queued — N ahead`
under each) rather than run concurrently and clobber each other. `/cancel` stops the
current request and clears the queue.

## The brain

**There is exactly one Kilobyte brain, trained once.** It is a single canonical
`kilobyte.gguf` — **Qwen2.5-1.5B fine-tuned for the Kilo persona and tool-call format,
quantised to Q4_K_M** — published on GitHub Releases
([`brain-1.0`](https://github.com/citadelconsortium/kilobyte/releases/tag/brain-1.0),
sha256 `54df7f01…bcb3506`) and mirrored on Kaggle Models. **Installing Kilo never trains
anything** — the installer only *downloads* that one brain and verifies its SHA-256.

The brain gives Kilo its identity, reliable tool-call format, and offline grounding; a
1.5B model's raw capability is bounded, so the framework (grounding, the orchestrator, the
offline reference bank, and cloud escalation) is what makes it powerful.

Once installed, a brain can still be upgraded deliberately. A newly built brain is a
**candidate** and never overwrites the running one until it passes evaluation and is
explicitly promoted; the previous brain is always kept for rollback.

```bash
kilo brain status                          # current / candidate / previous
kilo brain stage output/kilobyte-candidate.gguf
kilo brain promote                         # current → previous, candidate → current
kilo brain rollback                        # restore previous after a bad promotion
```

Training is a **maintainer-only** activity, not part of using or installing Kilo. It is a
separate, reproducible pipeline in [`training/`](training/README.md): build and validate the
dataset on CPU, fine-tune with QLoRA on Kaggle's GPU, convert and quantise to GGUF, evaluate
against a fixed suite, then stage and promote to produce a new canonical brain. End users
never run it — they just receive the finished `kilobyte.gguf`.

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — how the pieces fit and why
- [Build notes](docs/BUILD_NOTES.md) — what is in it, what changed, measured results, known limits
- [Training pipeline](training/README.md) — how kilobyte.gguf is built
- [Dataset spec](training/dataset_spec.md) — the SFT data format and distribution

## Telegram

Telegram is disabled until `/etc/kilobyte/telegram.json` exists with a real bot token and a non-empty
`allowed_chat_ids`. Any of those missing keeps it off, and the reason is written to the log.

```bash
# 1. create a bot with @BotFather and copy the token
# 2. get your numeric chat id (message @userinfobot)
sudo install -m 0600 -o kilobyte -g kilobyte /dev/null /etc/kilobyte/telegram.json
sudo tee /etc/kilobyte/telegram.json >/dev/null <<'JSON'
{ "token": "123456:ABC...", "allowed_chat_ids": [123456789] }
JSON
```

The bridge picks the file up within 30 seconds; no restart needed. Messages from any chat not in the
list are ignored and logged.

In the chat you get `/start`, `/status`, `/new` and `/help` in Telegram's command menu, plus inline
buttons for status, a new conversation and help. While Kilo works it keeps a typing indicator alive
and edits a live progress line (`◈ running web_search…`), then replaces it with the answer and the
tools that were used. Errors come back as a message — never silence.

Telegram talks to the same persistent brain as the terminal but under a read-only policy: no
terminal, file writes, privileges, services, packages, or process control.

## Security

Paths are normalized and restricted to the service user's home and `/tmp`; commands never use a shell;
runtime, output, and file sizes are bounded; private-network web fetches are blocked; writes and elevated or
destructive commands require an interactive one-shot permission; Telegram cannot mutate or administer the host.
