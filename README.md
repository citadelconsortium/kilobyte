# Kilobyte

Kilobyte is a local-first terminal AI built around exactly one prebuilt GGUF brain. A persistent
`llama-server` instance provides inference; a deterministic Python framework owns IPC, resources,
tool validation, permissions, machine/web access, bounded SQLite memory, Telegram policy, and UI.
There is no cloud fallback, training path, adapter, or model picker.

## Install on Arch Linux

```bash
sudo pacman -S --needed llama-cpp python curl sqlite ripgrep
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

`kilo`, `kilo chat`, `kilo status`, `kilo doctor`, `kilo resources`, `kilo model-info`,
`kilo benchmark`, `kilo version`, `kilo logs`, `kilo restart`, and `kilo stop`.

Telegram is disabled unless `/etc/kilobyte/telegram.json` exists with a bot token and an explicit
`allowed_chat_ids` list. Telegram uses the exact same persistent brain but a stricter read-only tool policy.

## Security

Paths are normalized and restricted to the service user's home and `/tmp`; commands never use a shell;
runtime, output, and file sizes are bounded; private-network web fetches are blocked; writes and elevated or
destructive commands require an interactive one-shot permission; Telegram cannot mutate or administer the host.
# One-line install (after the repository is published)

```sh
curl -fsSL https://raw.githubusercontent.com/0v3r51ght/kilobyte/main/scripts/install-online.sh | bash
```
