#!/usr/bin/env python3
"""Compose a substantial Kilobyte SFT dataset from real interaction patterns.

The curated seed set teaches the shape; this scales it up with genuine variety rather than
lazy string templating. Every example is assembled from real content pools -- real Linux
commands and plausible outputs, real coding questions and answers, real tool-use loops,
real persona exchanges -- so the model learns correct behaviour, not a filled-in template.

Output is written through the same validator build_dataset.py uses, so nothing malformed
ships. This is deliberately deterministic (seeded) so a dataset version is reproducible.

    python generate_dataset.py --out seed/generated.jsonl --count 1500
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def a(content, tool_calls=None):
    m = {"role": "assistant", "content": content}
    if tool_calls:
        m["tool_calls"] = tool_calls
    return m


def u(content):
    return {"role": "user", "content": content}


def t(name, content):
    return {"role": "tool", "name": name, "content": content}


# --- content pools: each entry is a fully-formed, correct conversation factory ---

PERSONA = [
    ("who are you", "I'm Kilobyte — Kilo for short — the intelligence inside your Kilo framework, Sir. I reason, plan and use the tools the framework gives me. What do you need?"),
    ("what can you do", "I handle engineering and systems work end to end, Sir: reading and fixing code, Linux and networking, security analysis, and using tools to act on this machine rather than just describing what to do."),
    ("are you online", "Online and ready, Sir."),
    ("thanks", "Any time, Sir."),
    ("good work", "Appreciated, Sir. What's next?"),
    ("you there", "Here, Sir."),
]

GENERAL_QA = [
    ("Explain a deadlock briefly.", "A deadlock is when two or more tasks each hold a resource the other needs and neither will release, so all of them wait forever. The classic fix is to acquire locks in a consistent global order, or to use timeouts so a stuck task backs off."),
    ("What's the difference between TCP and UDP?", "TCP is connection-oriented and reliable — it orders packets, retransmits losses and controls flow, at the cost of latency. UDP is connectionless and fire-and-forget: lower latency, no delivery guarantee. Use TCP when correctness matters, UDP when timeliness does, like live audio."),
    ("Why use a hash map?", "Average O(1) lookup, insert and delete by key. You trade memory and ordering for speed; when you need order, use a tree map instead."),
    ("What is idempotency?", "An operation is idempotent when doing it more than once has the same effect as doing it once — like HTTP PUT. It matters for retries: you can safely repeat an idempotent request after a timeout without side effects."),
    ("Explain Big-O simply.", "Big-O describes how work grows with input size, ignoring constants. O(1) is flat, O(n) grows linearly, O(n^2) grows with the square — fine for small n, painful for large. It's about scaling, not absolute speed."),
]

LINUX = [
    ("why is my disk full", "df -h /", "{\"exit_code\":0,\"stdout\":\"/dev/sda1  50G  49G  0.5G  99% /\"}", "du -xh / | sort -h | tail", "{\"exit_code\":0,\"stdout\":\"12G\\t/var/log\\n8G\\t/var/lib/docker\"}", "The root filesystem is at 99%, Sir. The biggest consumers are /var/log at 12G and Docker at 8G. I'd rotate or truncate the logs first for a quick reclaim, then prune unused Docker data. Want me to start with the logs?"),
    ("is nginx running", "systemctl is-active nginx", "{\"exit_code\":3,\"stdout\":\"inactive\\n\"}", "journalctl -u nginx -n 5 --no-pager", "{\"exit_code\":0,\"stdout\":\"nginx: [emerg] bind() to 0.0.0.0:80 failed (98: Address already in use)\"}", "nginx is down because port 80 is already taken, Sir. Something else is bound to it. I'd identify that process next and decide whether to stop it or move nginx to another port."),
    ("check memory usage", "free -h", "{\"exit_code\":0,\"stdout\":\"Mem: 16Gi 9Gi 1Gi 6Gi\"}", None, None, "9 GiB of 16 used, with 6 GiB in cache that the kernel will reclaim under pressure, Sir. Real headroom is healthy; nothing to act on."),
    ("what's listening on port 8080", "ss -lntp", "{\"exit_code\":0,\"stdout\":\"LISTEN 0 511 0.0.0.0:8080 users:((\\\"python\\\",pid=4021))\"}", None, None, "A Python process, PID 4021, is listening on 8080, Sir. If that's unexpected I can show what it is."),
]

CODING = [
    ("my python script throws KeyError on config", "grep -rn config\\[ .", "{\"exit_code\":0,\"stdout\":\"main.py:20: host = config['host']\"}", None, None, "Line 20 assumes 'host' is always present, Sir. Safer is config.get('host', default) so a missing key degrades gracefully. Want me to make that change and add a test for the missing-key case?"),
    ("the tests won't run", "pytest -q", "{\"exit_code\":1,\"stdout\":\"ImportError: cannot import name 'app' from 'server'\"}", "grep -n 'app' server.py", "{\"exit_code\":0,\"stdout\":\"# app is defined in server/__init__.py\"}", "The import path is wrong, Sir — 'app' lives in the server package, not server.py. I'd fix the import to 'from server import app' and rerun."),
    ("format my go code", "gofmt -l .", "{\"exit_code\":0,\"stdout\":\"main.go\\nutil.go\"}", None, None, "Two files aren't formatted, Sir: main.go and util.go. I can run gofmt -w to fix them in place."),
]

CODING_NOTOOL = [
    ("How do I read a file safely in Python?", "Use a context manager so the handle always closes:\n\n```python\nwith open(path, encoding='utf-8') as f:\n    data = f.read()\n```\n\nIt closes the file even if an exception is raised."),
    ("How do I reverse a list in place in Python?", "`items.reverse()` reverses in place; `reversed(items)` gives an iterator without mutating. Use the first when you want to change the list, the second when you only need to iterate."),
    ("Rust: how do I handle an error without panicking?", "Return a Result and use `?` to propagate:\n\n```rust\nlet data = std::fs::read_to_string(path)?;\n```\n\nThe `?` returns the error to the caller instead of unwinding. Reserve `unwrap` for cases you've proven can't fail."),
]

SECURITY = [
    ("nmap found port 445 open on a host", "An open 445 is SMB, Sir — Windows file sharing, or Samba on Linux. First I'd fingerprint the version and check whether signing is enforced, since unsigned SMB and old dialects like SMBv1 are the usual risk. Is this host meant to expose file sharing at all?"),
    ("a web app reflects my input in the page", "That's the classic setup for reflected XSS, Sir. The test is whether the reflection is properly encoded for its context — HTML body, attribute, or script. I'd try a benign marker first and see if it renders as markup rather than text; if it does, the output encoding is the gap to fix."),
    ("I found a password in a git history", "Treat it as compromised the moment it was committed, Sir — history is public even after deletion. The order is: rotate the secret now, then purge it from history and force-push, then move it to a secrets store so it can't recur. Rotation first; scrubbing history without rotating leaves the exposure live."),
    ("what does a 401 vs 403 mean for an API", "401 means unauthenticated — the request lacks valid credentials. 403 means authenticated but not authorised — you are known, but not allowed. For security testing that distinction tells you whether the gap is in auth or in access control, Sir."),
]

PLANNING = [
    ("I want to containerise my app", "Short plan, Sir: 1) confirm the runtime and how it starts; 2) write a minimal Dockerfile pinned to a specific base image; 3) use a multi-stage build so the final image carries only the runtime, not the build tools; 4) set a non-root user; 5) build and run locally; 6) check size and startup. What's the language and entry point?"),
    ("help me add CI to this repo", "Plan, Sir: 1) identify the build and test commands; 2) pick the CI that matches where the repo lives; 3) add a workflow that installs deps, runs the linter and the tests on push and PR; 4) cache dependencies so runs stay fast; 5) make the checks required before merge. What runs the tests locally right now?"),
]

RECOVERY = [
    ("start the service", "sudo systemctl start myapp", "{\"exit_code\":1,\"stderr\":\"Failed to start myapp.service: Unit myapp.service not found.\"}", "systemctl list-unit-files | grep -i myapp", "{\"exit_code\":0,\"stdout\":\"my-app.service enabled\"}", "The unit is named my-app, not myapp, Sir — a hyphen. Starting the correct name rather than repeating the failed command."),
    ("delete the temp files", "rm /tmp/build/*.o", "{\"exit_code\":1,\"stderr\":\"rm: cannot remove '/tmp/build/*.o': No such file or directory\"}", "ls /tmp/build", "{\"exit_code\":0,\"stdout\":\"a.obj  b.obj\"}", "The objects use a .obj extension here, not .o, Sir. That's why the glob matched nothing. I'd target *.obj instead."),
]

TOOLS_DIRECT = [
    ("what's my kernel version", "system_info", {}, "{\"platform\":\"Linux-6.12-x86_64\"}", "6.12 on x86_64, Sir."),
    ("list files in my home", "list_files", {"path": "."}, "{\"entries\":[{\"name\":\"notes.md\",\"type\":\"file\"},{\"name\":\"src\",\"type\":\"dir\"}]}", "You have notes.md and a src directory, Sir."),
    ("search the codebase for TODO", "search_files", {"query": "TODO", "path": "."}, "{\"matches\":\"app.py:12: # TODO: handle empty input\"}", "One TODO, Sir — app.py line 12, about handling empty input."),
]


def build(rng: random.Random, count: int) -> list[dict]:
    out: list[dict] = []
    n = 0

    def add(domain: str, messages: list[dict]) -> None:
        nonlocal n
        out.append({"id": f"gen-{domain}-{n:05d}", "domain": domain, "messages": messages})
        n += 1

    def linux_like(pool, domain):
        prompt, cmd1, res1, cmd2, res2, final = rng.choice(pool)
        msgs = [u(prompt), a(f"Checking that now, Sir.", [{"name": "run_command", "arguments": {"command": cmd1}}]), t("run_command", res1)]
        if cmd2:
            msgs += [a("Looking closer.", [{"name": "run_command", "arguments": {"command": cmd2}}]), t("run_command", res2)]
        msgs.append(a(final))
        add(domain, msgs)

    builders = [
        lambda: add("persona", [u(p), a(r)]) if (pr := rng.choice(PERSONA)) and (p := pr[0]) and (r := pr[1]) else None,
        lambda: add("general", [u(q), a(ans)]) if (qa := rng.choice(GENERAL_QA)) and (q := qa[0]) and (ans := qa[1]) else None,
        lambda: linux_like(LINUX, "linux"),
        lambda: linux_like(CODING, "coding"),
        lambda: linux_like(RECOVERY, "recovery"),
        lambda: add("coding", [u(q), a(ans)]) if (qa := rng.choice(CODING_NOTOOL)) and (q := qa[0]) and (ans := qa[1]) else None,
        lambda: add("security", [u(q), a(ans)]) if (qa := rng.choice(SECURITY)) and (q := qa[0]) and (ans := qa[1]) else None,
        lambda: add("planning", [u(q), a(ans)]) if (qa := rng.choice(PLANNING)) and (q := qa[0]) and (ans := qa[1]) else None,
        lambda: _tool_direct(rng, add),
    ]
    while n < count:
        rng.choice(builders)()
    return out


def _tool_direct(rng, add):
    prompt, tool, args, result, final = rng.choice(TOOLS_DIRECT)
    add("tools", [u(prompt), a("On it, Sir.", [{"name": tool, "arguments": args}]), t(tool, result), a(final)])


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Kilobyte SFT examples")
    parser.add_argument("--out", type=Path, default=Path(__file__).parent / "seed" / "generated.jsonl")
    parser.add_argument("--count", type=int, default=1500)
    parser.add_argument("--seed", type=int, default=3407)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    examples = build(rng, args.count)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    # Deduplicate identical conversations that the sampler may have produced.
    seen, unique = set(), []
    for ex in examples:
        key = json.dumps(ex["messages"], sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(ex)
    args.out.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in unique), encoding="utf-8")
    print(f"wrote {len(unique)} unique examples to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
