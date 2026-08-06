# Kilobyte architecture

Kilobyte is a local-first terminal AI built around exactly one prebuilt GGUF brain. The
model reasons and chooses tools; deterministic Python owns everything that must not be
left to a language model.

```
        kilo (TUI)          Telegram
             │                  │
             └────── IPC ───────┘
                     │
             Kilobyte daemon
                     │
   ┌────────┬────────┼────────┬─────────┐
   │        │        │        │         │
 agent   tools   memory  resources  security
   │
llama-server ── kilobyte-brain.gguf
```

## Principles

**The model is not the security boundary.** It may request any action; `security.py`
decides what runs. Model output is treated as untrusted input to privileged code.

**One brain, one process.** A single `llama-server` holds the model. Every front end
connects to it over a Unix socket. Launching `kilo` twice does not load the model twice.

**The framework carries the load.** Every token the model does not have to read is
prompt-processing time not spent. Tool results are compacted, history is budgeted, and
the prompt prefix is kept stable and cached so the model only processes what is new.

**No cloud fallback.** There is no provider abstraction and no remote inference path. If
the local model cannot run, Kilobyte says so rather than silently substituting a service.

## Modules

| Module | Responsibility |
|---|---|
| `cli.py` | `kilo` entry point and subcommands |
| `tui.py` | Animated terminal interface, streaming, cancellation |
| `daemon.py` | Process lifecycle, startup order, clean shutdown |
| `rpc.py` | Unix-socket JSON protocol between front ends and daemon |
| `agent.py` | The agent loop: prompt assembly, tool dispatch, continuation |
| `runtime.py` | Owns the single `llama-server`; KV cache save/restore |
| `tools.py` | Tool registry, schemas, implementations |
| `context.py` | Deterministic compaction of tool results |
| `memory.py` | Bounded SQLite sessions, messages, facts, audit |
| `resources.py` | Hardware detection and runtime tuning |
| `security.py` | Path policy, command policy, permission manager |
| `telegram.py` | Optional remote front end, read-only policy |
| `doctor.py` | Health checks and remediation hints |
| `prompt.py` | System prompt and remote suffix |
| `config.py` | Settings, paths, model identity |

## The agent loop

```
user message
      ↓
system prompt (stable, cached) + recalled memory + budgeted history
      ↓
llama-server, with the full tool schema
      ↓
text  ──────────────────────────────────→ answer
  or
tool call → validate → authorise → execute → compact result
      ↓
back to the model, repeat (bounded by max_agent_steps)
```

Repeated identical calls are detected and blocked, and tools are withdrawn for the next
step, so a small model cannot spin on the same action.

## Why the prompt prefix is stable

Tools are rendered into the prompt prefix. Selecting them per request changes that
prefix, which misses `llama-server`'s cache and reprocesses the whole system prompt on
every message. Measured on a CPU-only host that is the difference between a 36-second
reply and roughly twenty minutes.

For the same reason the system message is never mutated: recalled memory is added as a
separate message after it, so the cached prefix still matches.

The warmed prefix is saved to disk and restored at startup. The cache filename hashes the
system prompt, the tool schemas, the model path and the context size, so changing any of
them re-warms rather than restoring a prefix that could not be reused.

## Context budgeting

Tool output is the largest unpredictable input. Dense output tokenises at roughly two
characters per token: a single `ls -la /usr/lib` measured **33,496 tokens** against an
8192-token window.

`context.py` compacts results before the model sees them: large text fields are shortened
middle-out so the head, the tail and the surrounding structure (exit codes, paths) all
survive; entry lists are capped to what the budget affords; and the model is told what was
removed so it can request a narrower slice. History is budgeted the same way, since a
message count is not a bound on context when one turn can carry a tool result.

## Security model

- Paths resolve through `PathPolicy` and must land inside the service user's home or `/tmp`.
- Commands never use a shell. Explicit `program + argv` only; shell operators are rejected.
- Commands are classified `safe` / `write` / `elevated` / `destructive`. Anything above
  `safe` needs an interactive one-shot approval.
- Remote (Telegram) requests are read-only: no terminal, writes, privileges, services,
  packages or process control, enforced in both the schema and the executor.
- Web fetches resolve DNS and refuse non-global addresses, so the model cannot reach
  private network services.
- Output, runtime and file sizes are bounded. Every tool call is audited to SQLite.

## Resources

`resources.py` reads total and available memory, cgroup limits, CPU flags and GPU presence
on each start, reserves headroom for the operating system, and derives context size,
thread count, batch size and GPU offload. The same GGUF is used on every machine; only the
runtime configuration changes.

llama.cpp ships per-microarchitecture CPU backends and loads the best one at runtime, so a
host with AVX2 or AVX-512 is dramatically faster than one without. `kilo doctor` warns when
a machine lacks AVX2 and will fall back to the generic backend.
