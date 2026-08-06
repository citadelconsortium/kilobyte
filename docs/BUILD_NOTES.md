# Build notes

What Kilobyte contains, what has been added, and the reasoning behind the decisions that
are not obvious from the code.

Framework version 0.1.0 · brain `kilobyte-qwen3-1.7b-q4_k_m.gguf` (Qwen3 1.7B Q4_K_M,
Apache-2.0) · SHA-256 `d2387ca2dbfee2ffabce7120d3770dadca0b293052bc2f0e138fdc940d9bc7b5`

## What is in it

**Brain.** One prebuilt GGUF, served by a single persistent `llama-server`. No training in
the installer or at runtime, no adapters, no model picker, no cloud fallback.

**Front ends.** An animated terminal TUI, and an optional Telegram bridge. Both talk to the
same daemon over a Unix socket and share one loaded model with separate conversations.

**Tools.** Ten, all verified end to end: `read_file`, `write_file`, `list_files`,
`search_files`, `run_command`, `system_info`, `web_search`, `web_fetch`, `remember`,
`recall`.

**Memory.** SQLite with bounded growth: sessions, messages, long-term facts and a tool
audit trail. Retention limits are enforced on write.

**Safety.** Path sandbox, shell-free command execution with risk classification,
interactive approval for anything above `safe`, private-address blocking for web fetches,
and a stricter read-only policy for anything arriving remotely.

**Operations.** systemd unit enabled for boot, `kilo doctor` health checks, `kilo status`,
`kilo resources`, `kilo model-info`, `kilo benchmark`, `kilo logs`, and a one-line
installer that provisions dependencies, the service user, the model and the service.

## Changes and why

### Replies were minutes long, or appeared to hang

Tool schemas were selected per request by keyword. Tools render into the prompt prefix, so
a varying set missed `llama-server`'s prompt cache on **every** message and reprocessed the
system prompt and schemas each turn.

Fixed by making the tool set fixed, trimming the system prompt from ~386 to ~139 tokens,
priming the cache at startup with the exact prompt and tool set real requests use, and
moving recalled memory out of the system message so the prefix stays byte-identical.

Measured after: **36 seconds** for a reply, with 940 of 956 prompt tokens served from
cache.

### One tool call could exceed the whole context

`max_tool_output` bounded results at 64 KB of characters. Dense output tokenises at about
two characters per token, so `ls -la /usr/lib` measured **33,496 tokens** against an
8192-token window — enough to displace the conversation and the system prompt entirely.

Added `context.py`, which compacts results deterministically before the model sees them.
Verified with `llama-server`'s tokeniser: the same result now measures **798 tokens**.

Conversation history is budgeted the same way, because a message count is not a bound on
context when a single turn can carry a tool result.

### Warmup cost was paid on every boot

`--slot-save-path` only enables the endpoints; nothing was saved. Each start reprocessed
the full prefix (~20 minutes on the development host) while holding the only inference
slot.

Warmup now restores a matching saved slot and otherwise processes the prefix once and
saves it. Restore measured at **0.23 seconds**. The filename hashes the prompt, tool
schemas, model path and context size, so a changed prompt or a move to a machine with
different memory re-warms correctly instead of restoring an unusable prefix. Stale slots
are pruned, as each is around 100 MB.

### Every shutdown ended in SIGKILL

Warmup, Telegram and inference perform blocking HTTP on worker threads. Cancelling their
tasks does not interrupt the threads, and `asyncio.run()` waits for the default executor
before returning, so a request in flight held shutdown open past systemd's stop timeout;
`llama-server` then crashed in `__cxa_finalize` on the way out.

`llama-server` is now stopped before the tasks are awaited, which fails those requests
immediately, and the slot save/restore timeout is well inside the stop window. Restart
went from a 60-second SIGKILL escalation to **2.5 seconds**, clean.

### A dropped client held the model hostage

Closing the agent generator did not close the inner `chat_stream` generator it was
iterating, so a disconnected client (Ctrl-C, a killed process) left the request to
`llama-server` open and its single slot held until the generation finished on its own —
observed at roughly eight minutes of wasted CPU. Fixed with `aclosing` in `agent.py` and an
explicit `aclose` in the RPC handler.

### The interface looked frozen and could not be interrupted

The spinner only redrew when an event arrived, so a slow step showed nothing for minutes,
and there was no way to stop a running generation.

The activity line is now timer-driven and reports the current action, how long that action
has been running, the running total and the model. Each tool prints its arguments when it
starts and its duration and result when it finishes, and the closing border reports total
time, time to first token, and which tools ran. SIGINT cancels the generation and returns
to the prompt without tearing down the session.

### Telegram could not be enabled, and failed silently

Writing the config after the daemon started did nothing: `run()` returned immediately and
the bridge stayed dead until a restart. Failures were swallowed by a bare `except`, leaving
the sender with silence indistinguishable from a hung bot.

The bridge now polls for its config, always replies (errors included), keeps a typing
indicator alive, edits a live progress message as work proceeds, publishes a command menu,
offers inline buttons for status/new/help, and rejects a placeholder token or non-integer
chat ids rather than treating them as configured.

### Skills and MCP

Added a skill registry: Kilo records a procedure once a multi-step task works, and
matching procedures are surfaced into context on later requests. On slow hardware this is
the cheaper side of the trade -- a few hundred tokens of known-good steps against several
planning rounds, each of which costs a full generation. Skills are keyed by name so
re-saving refines one in place, outcomes are tracked so reliable ones sort first, and
growth is bounded by dropping the least reliable and least recently used.

Added an MCP client (stdio transport, protocol 2025-06-18) so tools from external servers
can be offered alongside the built-in ones. Implemented against the published
specification: newline-delimited UTF-8 JSON-RPC with no embedded newlines, the
initialize/initialized handshake, paginated tools/list, and the documented shutdown
sequence of closing stdin then escalating. Servers are treated as untrusted -- tools are
namespaced, a tool without a usable object input schema is never shown to the model,
calling one needs permission, results go through the same compaction, a hung server hits a
request timeout rather than blocking the daemon, and a server that fails to start is
skipped. MCP tools are not offered over Telegram. Tested against a real server subprocess
over real pipes rather than a mock.

### Installer was not actually one line

`install.sh` required a manual `pacman` step and a pre-existing service user. Both are now
handled by the script, so the published one-liner needs nothing but running it.

## Known limits

**Hardware dominates.** Token generation is bounded by the host CPU. On a machine without
AVX2, llama.cpp falls back to its generic backend and generation runs near 0.55 tokens per
second; the development VM's host is a 2010 Core2 Duo. The cache work removes the repeated
prompt cost, not the per-token generation cost. Modern hardware loads an AVX2 or AVX-512
backend automatically, and GPU offload is detected and used when present.

**Model capability.** The brain is 1.7B parameters. It follows instructions, calls tools
correctly and completes multi-step tasks, but will not reason like a large model on long or
subtle chains. The orchestration layer is not the limiting factor.

**Model capability is the ceiling, not the framework.** Skills and MCP widen what Kilo can
reach, but a 1.7B brain still decides when to use them.

## Verification

60 automated tests covering resources, runtime, agent loop, tools, context compaction,
memory, skills, security, CLI, installation, Telegram and MCP (the last against a real
server subprocess).

A static analysis pass over the source found two genuine web-tool vulnerabilities, both
confirmed by experiment before being fixed: urllib follows redirects, so validating only
the requested URL left the private-address block bypassable by a public host answering 302
with a local address; and ElementTree expands internal entities, so a hostile search
provider could return a small document that expands to gigabytes on a machine with about
2 GB to spare. Both are now closed and covered by regression tests.

Verified on the target machine: all ten tools returning real data; the full agent loop
choosing a tool, reading its result and answering correctly; `kilo doctor` passing; cache
restore across restarts; clean service restart; and boot persistence enabled.
