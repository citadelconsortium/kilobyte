# Kilobyte Builder Notes / Handoff

This is the maintainer handoff for future agents. Read this before changing the
runtime, brain, installers, or TUI.

## Product contract

Kilobyte is a local-first terminal agent. The local GGUF is the default brain;
cloud escalation is explicit (`/cloud`) and never an automatic fallback. The
agent must address the operator as **Sir**, ground claims in tool evidence, and
continue tool work until the requested task is complete. Destructive actions and
external side effects remain approval-gated.

## Brain

- Release: `brain-1.1`, GitHub asset `kilobyte.gguf`
- Base: `unsloth/Qwen2.5-1.5B-Instruct`, QLoRA fine-tune, Q4_K_M
- SHA-256: `6cdcca6b3876fa07d841dfc718e10a10bd128d6602cd73a23a54109b4333b6b7`
- Size: 986,047,936 bytes; GGUF v3 / qwen2
- Release asset (not a Git blob); see `training/gguf/manifest.json`
- Candidate/current/previous promotion and rollback are implemented in `brains.py`.

## Agents and tools

Profiles: orchestrator, research, coding, security, systems, conversation, and
private. Built-in tools cover files, shell, web search/fetch, memory, skills,
references, system inspection, and MCP. The security profile is operator-targeted
and must retain the permission boundary; do not weaken it to claim “hacking power.”

Cloud providers are explicit-only and HTTPS-only. The catalog includes Hugging Face
Inference Providers (`https://router.huggingface.co/v1`) and account-scoped Cloudflare
Workers AI; `/model` fetches the selected provider's live model catalog. GitHub Models
was retired in July 2026 and is intentionally not advertised. Hermes Agent is a client,
not a separate inference endpoint. Groq uses `https://api.groq.com/openai/v1`; requests
include a project user-agent to avoid edge-signature blocking, and retired
`llama-3.3-70b-versatile` configs migrate to `llama-3.1-8b-instant` on read.

## UI / controls

`kilo` launches the bordered prompt-toolkit TUI; `kilo chat` is the streaming
CLI. `/commands` and `/help` list controls. `/botkey` securely updates the
Telegram token through the daemon RPC. Telegram progress edits every three
seconds and includes a bounded live token preview.
The live stats bar intentionally omits the user's request text so status indicators stay
compact; it shows phase, request count, tools, tokens, model, queue, and context instead.
Cloud context is shown only when the selected model API reports a verified limit.
The TUI retains background RPC/monitor tasks and shows their live count in the stats bar;
the daemon separately monitors and restarts a failed local runtime.

## Install / release

`scripts/install-online.sh` downloads this repository, runs the dependency/app
installer, then `install-model.sh` downloads and verifies brain-1.1 atomically.
The Framework repository is brain-free and has its own installer; it accepts a
user GGUF or cloud provider. Never commit the 986 MB GGUF to Git.

## Verification state

The Kilobase VM is an exact checkout of `origin/main`; daemon, model checksum,
socket, memory, and disk checks pass. The full suite is 107 tests and passes in
the VM. The VM is a Core 2 Duo/SSE4 guest with two CPUs; local inference can take
minutes or longer for a large grounded prompt. This is hardware-bound, not a
model checksum or daemon failure. Use AVX2+ hardware or explicit cloud escalation
for advanced coding/research workloads.

## Handoff rules

Run `PYTHONPATH=src python3 -m unittest discover -s tests -q` before release,
then `kilo doctor --verify-model`, `kilo brain status`, and `kilo status` on the
target machine. Keep docs, wiki, release checksum, installer URL, and this file
consistent whenever the brain or provider catalog changes.
