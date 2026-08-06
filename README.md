# Kilobyte

Kilobyte is a local-first terminal AI built around exactly one prebuilt GGUF brain. A persistent
`llama-server` instance provides inference; a deterministic Python framework owns IPC, resources,
tool validation, permissions, machine/web access, bounded SQLite memory, Telegram policy, and UI.
There is no cloud fallback, training path, adapter, or model picker.

## Install on Arch Linux

```bash
curl -fsSL https://raw.githubusercontent.com/citadelconsortium/kilobyte/main/scripts/install-online.sh | bash
kilo
```

The installer handles system dependencies (`llama-cpp`, `python`, `curl`, `sqlite`, `ripgrep`), creates the
service user if needed, installs the app, downloads and verifies the model, and starts the service. Nothing
else is required.

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

Inside the TUI: `/help`, `/status`, `/new`, `/clear`, `/exit`. Ctrl-C cancels the running
generation without leaving the session; a second one, or `/exit`, leaves.

The interface streams tokens live and shows what Kilo is doing — the current action, how long
it has been running, the model, each tool with its arguments and duration, and a closing summary
with total time, time to first token, and which tools ran.

## Tools

`read_file`, `write_file`, `list_files`, `search_files`, `run_command`, `system_info`,
`web_search`, `web_fetch`, `remember`, `recall`.

Results are compacted before reaching the model, so a large directory listing or command output
cannot displace the conversation.

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — how the pieces fit and why
- [Build notes](docs/BUILD_NOTES.md) — what is in it, what changed, measured results, known limits

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
