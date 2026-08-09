# Kilobyte Builder Notes / Handoff

This is the maintainer handoff for future agents. Read this before changing the
runtime, brain, installers, or TUI.

## Product contract

Kilobyte is a local-first terminal agent. The local GGUF is the default brain;
cloud escalation is explicit (`/cloud`) and never an automatic fallback. The
agent must address the operator as **Sir**, ground claims in tool evidence, and
continue tool work until the requested task is complete. Destructive actions and
external side effects remain approval-gated.

The cloud catalog includes Ollama Cloud, Agnes AI, ModelScope, LLM7.io, OpenCode Zen,
and GLHF.chat in addition to the established providers. These integrations use their
documented OpenAI-compatible endpoints and Bearer authentication. Provider
base URLs and default models are defined in `src/kilobyte/providers.py` and model
catalogues are fetched live through `/model`. Defaults are Ollama Cloud
`https://ollama.com/v1` (`gpt-oss:120b`), Agnes AI
`https://apihub.agnes-ai.com/v1` (`agnes-2.0-flash`), ModelScope
`https://api-inference.modelscope.cn/v1` (`Qwen/Qwen3-32B`), LLM7.io
`https://api.llm7.io/v1` (`fast`), OpenCode Zen
`https://opencode.ai/zen/v1` (`big-pickle`), and GLHF.chat
`https://glhf.chat/api/openai/v1` (`hf:meta-llama/Llama-3.3-70B-Instruct`).

## Brain

- Release: `brain-1.1`, installed filename `kilobyte-1.5b-q4_k_m.gguf` (GitHub asset `kilobyte.gguf` retained for URL compatibility)
- Base: `unsloth/Qwen2.5-1.5B-Instruct`, QLoRA fine-tune, Q4_K_M
- SHA-256: `6cdcca6b3876fa07d841dfc718e10a10bd128d6602cd73a23a54109b4333b6b7`
- Size: 986,047,936 bytes; GGUF v3 / qwen2
- Release asset (not a Git blob); see `training/gguf/manifest.json`
- Candidate/current/previous promotion and rollback are implemented in `brains.py`.

## Agents and tools

Profiles: orchestrator, research, coding, security, systems, conversation, and
private. Built-in tools cover files, shell, web search/fetch, memory, skills,
references, system inspection, and MCP. The security profile is operator-targeted
and must retain the permission boundary: active work is limited to the exact target and
scope Sir supplies. It deliberately has no static hacking playbook. It recalls and saves
verified custom methods through `recall`/`save_skill`; never reintroduce the old seeded
`authorized-security-tool-learning` record.

Cloud providers are explicit-only and HTTPS-only. The catalog includes Hugging Face
Inference Providers (`https://router.huggingface.co/v1`) and account-scoped Cloudflare
Workers AI; `/model` fetches the selected provider's live model catalog. GitHub Models
was retired in July 2026 and is intentionally not advertised. Hermes Agent is a client,
not a separate inference endpoint. Groq uses `https://api.groq.com/openai/v1`; requests
include a project user-agent to avoid edge-signature blocking, and retired
`llama-3.3-70b-versatile` configs migrate to `llama-3.1-8b-instant` on read.

## UI / controls

`kilo` launches the bordered prompt-toolkit TUI; `kilo chat` is the streaming
CLI. `/commands` and `/help` list controls. `/botkey` securely updates the Telegram
token through the daemon RPC. Telegram publishes real command autocomplete and provides
persistent `/local`, `/cloud`, `/switch`, `/model`, and `/agent` routing per chat. Remote
tool access stays read-only. Progress animates every 1.2 seconds. A second persistent card
contains the bounded, redacted activity log and live token preview. Some compatible cloud
models emit `<tool_call>` XML in the content stream; `agent.py` recovers it only when the
tool exists in the already-filtered remote schema, emits `response_reset`, and dispatches
it normally. Never execute a recovered name outside that schema. `telegram_render.py`
converts common Markdown to Telegram-safe HTML for clean research answers.
It collapses excess blank lines outside code blocks and splits long HTML with balanced
tags. Context reporting is route-aware: local shows the adaptive llama.cpp window; cloud
shows the provider-advertised limit or `provider-managed` when its catalogue omits one.
The live stats bar intentionally omits the user's request text so status indicators stay
compact; it shows phase, request count, tools, tokens, model, queue, and context instead.
The animated context meter is local-only; cloud mode omits context from the status bar.
The TUI retains background RPC/monitor tasks and shows their live count in the stats bar;
the daemon separately monitors and restarts a failed local runtime.
Past-chat selectors include local date/time. OpenRouter free-model discovery accepts both
`:free` IDs and zero-priced catalogue entries.

## Install / release

`scripts/install-online.sh` downloads this repository, runs the dependency/app
installer, then `install-model.sh` downloads and verifies brain-1.1 atomically.
The Framework repository is brain-free and has its own installer; it accepts a user GGUF
or cloud provider. The 986 MB GGUF belongs in the verified `brain-1.1` GitHub release
asset, not duplicated as a normal source-tree blob.

## Verification state

The release gate currently covers 135 tests. It requires daemon, model checksum, socket, memory, disk, RPC disconnect,
Telegram routing, and provider-catalog checks to pass on Kilobase. The VM is a Core 2
Duo/SSE4 guest with two CPUs; local inference can take
minutes or longer for a large grounded prompt. This is hardware-bound, not a
model checksum or daemon failure. Use AVX2+ hardware or explicit cloud escalation
for advanced coding/research workloads.

## Handoff rules

Run `PYTHONPATH=src python3 -m unittest discover -s tests -q` before release,
then `kilo doctor --verify-model`, `kilo brain status`, and `kilo status` on the
target machine. Keep docs, wiki, release checksum, installer URL, and this file
consistent whenever the brain or provider catalog changes.
