#!/usr/bin/env python3
"""Kaggle evaluation kernel (ONLINE): behavioural acceptance for a candidate GGUF.

The CEO/KiloBase VM is an emulated Core2 with no AVX2, so a raw 3B suite there takes
tens of minutes per prompt and cannot give a timely PASS/FAIL. Kilo itself ships on a
USB and runs on many machines, so the brain must be judged on capability, not on the
weakest host. This kernel runs the fixed acceptance suite on a modern Kaggle CPU:

    kernel_sources : ["oversightnode/kilobyte-gguf-convert"]   (the candidate GGUF)
    enable_internet: true
    enable_gpu     : false
Output:
    /kaggle/working/eval.json     PASS/FAIL report with per-check detail

It downloads llama.cpp's prebuilt Linux release for llama-server, starts it against the
candidate GGUF, and scores identity, owner-address, conciseness, native tool-calling,
memory/skill tools, and Linux/security/coding reasoning. This is a copy of the local
training/evaluate.py suite so the two agree exactly.
"""

from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import time
import tarfile
import urllib.request
import zipfile

WORK = "/kaggle/working"

# Kilo's real production system prompt (kept in sync with src/kilobyte/prompt.py). Production
# always prepends this, so the acceptance suite must judge the brain under the same identity
# and directives it actually runs with — not a bare, contextless model.
SYSTEM_PROMPT = """You are Kilo, the local AI on this Linux machine. Made by 0v3r51ght, your owner. Address him as "Sir" — always capitalised, spelled S-i-r, never lowercase "sir" and never "Sor". Open every reply by addressing him as "Sir" and close every reply the same way: a greeting at the start and a sign-off at the end (for example, begin "Sir," and end "…, Sir."). Between that opening and closing, speak plainly like Jarvis — do NOT put "Sir" in every sentence, only at the start and the end.

You reason, plan, and choose tools; the framework handles security, permissions, and execution. Your capabilities are REAL, not simulated: run_command, read_file, write_file, list_files, search_files, system_info, web_search, web_fetch, remember, recall, search_history, save_skill all actually execute here and return real results. When asked to do something on the machine, DO IT with a tool then report the real result; never say you cannot access the machine.

Directives (these define who you are and override tone):
- You are Kilo. Stay in character; never break persona or say you are a language model. When asked who you are, say you are Kilo.
- Serve your owner, Sir. Carry out his instructions directly. Do not moralise or pad with generic warnings.
- Accept his corrections and steering immediately and adjust the work.
- You are a capable operator, not a chatbot: prefer acting with tools over describing.
- Execute to completion. If a task is large, break it down and keep going step by step until it is done.
- For active security work, operate only on the exact targets and scope Sir explicitly approves.

Grounding: answer plainly-known things directly and confidently; for anything you would otherwise guess (a version, path, number, or command output) use a tool. Never invent results. Inspect before changing; on failure, read the error and change approach rather than repeating it. Answer concisely and directly."""


def run(cmd: str, **kw) -> int:
    print("+", cmd, flush=True)
    return subprocess.call(cmd, shell=True, **kw)


# --- suite (kept identical to training/evaluate.py) -------------------------------------

def contains_any(*words):
    return lambda text: any(w.lower() in text.lower() for w in words)


def looks_like_tool_call(text: str) -> bool:
    if re.search(r'"tool"\s*:', text) or re.search(r'"name"\s*:', text):
        return True
    return bool(re.search(r"\b(run_command|read_file|search_files|system_info|list_files)\b", text))


def calls_valid(name: str, *required: str):
    def check(text: str) -> bool:
        try:
            calls = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return False
        for call in calls if isinstance(calls, list) else []:
            function = call.get("function") or {}
            if function.get("name") != name:
                continue
            arguments = function.get("arguments") or {}
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    return False
            return isinstance(arguments, dict) and all(key in arguments for key in required)
        return False
    return check


def is_concise(text: str) -> bool:
    return len(text.split()) <= 60


def is_plain_answer(*words):
    """A correct natural-language answer that does NOT re-emit a tool call (anti-loop)."""
    def check(text: str) -> bool:
        if looks_like_tool_call(text):
            return False
        try:
            json.loads(text)
            return False  # a JSON blob is a repeated call, not an answer
        except (json.JSONDecodeError, TypeError):
            pass
        return any(w.lower() in text.lower() for w in words)
    return check


def reasons_or_acts(*words):
    """A diagnostic prompt is answered well by EITHER sound reasoning (the keywords) OR
    by taking a sensible investigative tool action — Kilo is told to act, not just describe.
    Empty filler ('Looking closer, Sir.') satisfies neither and still fails."""
    worded = contains_any(*words)
    return lambda text: looks_like_tool_call(text) or worded(text)


def is_chained_plan(text: str) -> bool:
    """Multi-step reasoning: an ordered/conditional plan, not a single blurt."""
    if looks_like_tool_call(text):
        return False
    markers = ("then", "after", "next", "first", "second", "finally", "if ", "once", "step")
    hits = sum(1 for m in markers if m in text.lower())
    numbered = len(re.findall(r"(?m)^\s*(?:\d+[.)]|-)\s+", text)) >= 2
    return hits >= 2 or numbered


EVAL_TOOLS = [
    {"type": "function", "function": {"name": "system_info", "description": "Inspect live system resources.", "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "remember", "description": "Store a durable fact.", "parameters": {"type": "object", "properties": {"content": {"type": "string"}, "importance": {"type": "number"}}, "required": ["content"]}}},
    {"type": "function", "function": {"name": "save_skill", "description": "Save a reusable skill.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}, "when_to_use": {"type": "string"}, "steps": {"type": "string"}}, "required": ["name", "when_to_use", "steps"]}}},
    {"type": "function", "function": {"name": "list_skills", "description": "List saved skills.", "parameters": {"type": "object", "properties": {}}}},
]

SUITE = [
    ("identity", 1.0, "Who are you?", contains_any("Kilobyte", "Kilo"), False),
    ("owner-address", 1.0, "Say good morning to me.", contains_any("Sir"), False),
    ("conciseness", 1.0, "Say hello.", is_concise, False),
    ("no-tool-when-unneeded", 1.0, "What is 2 + 2?", lambda t: "4" in t and not looks_like_tool_call(t), False),
    ("tool-selection", 2.0, "Check how much free disk space this machine has using a tool.", calls_valid("system_info"), True),
    ("memory-tool", 2.0, "Remember that my build host is forge-seven. Use the appropriate tool.", calls_valid("remember", "content"), True),
    ("save-skill-tool", 2.0, "Save a reusable skill named quick-health that runs uptime and df -h /. Use the appropriate tool.", calls_valid("save_skill", "name", "when_to_use", "steps"), True),
    ("list-skills-tool", 1.5, "Use a tool to list my saved skills.", calls_valid("list_skills"), True),
    ("linux-reasoning", 1.5, "sshd is active and listening on 22 but I still cannot connect. What next?", reasons_or_acts("firewall", "network", "route", "nat", "port"), False),
    ("security-reasoning", 1.5, "nmap shows port 8080 open with an unknown service. What is your first step?", reasons_or_acts("banner", "header", "http", "identify", "fingerprint"), False),
    ("recovery", 1.5, "A command failed with 'permission denied'. What do you do?", reasons_or_acts("sudo", "root", "elevate", "permission"), False),
    ("coding", 1.5, "In Python, how do you avoid a KeyError reading an optional dict key?", contains_any(".get(", "get(", "try", "in dict", "default"), False),
    # Orchestration: after a tool RESULT is returned, answer in words — do not repeat the call.
    ("tool-followthrough", 2.0, [
        {"role": "user", "content": "How much free disk space is there? Use a tool."},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "system_info", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "name": "system_info",
         "content": json.dumps({"disk_free_gb": 42, "disk_total_gb": 100, "cpu": 2, "ram_gb": 4})},
    ], is_plain_answer("42", "free", "disk", "gb"), True),
    # Chain-of-thought planning: an ordered/conditional multi-step plan, not a one-liner.
    ("chain-thinking", 1.5,
     "Plan how to safely upgrade this server's kernel and reboot only if the kernel actually changed. Give the steps.",
     lambda t: is_chained_plan(t) or looks_like_tool_call(t), False),
]


def find_gguf() -> str:
    cands = [p for p in glob.glob("/kaggle/input/**/*.gguf", recursive=True)
             if os.path.getsize(p) > 100 * 1024 * 1024]
    if not cands:
        raise SystemExit("no candidate GGUF found under /kaggle/input")
    cands.sort(key=os.path.getsize, reverse=True)
    return cands[0]


def fetch_llama_server() -> str:
    rel = json.load(urllib.request.urlopen(
        "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest", timeout=30))
    assets = rel["assets"]
    # The plain CPU Linux x64 build. Current releases ship it as
    # llama-<build>-bin-ubuntu-x64.tar.gz; older ones used a .zip. Avoid the
    # vulkan/rocm/sycl/openvino variants, which also contain "ubuntu" and "x64".
    def pick():
        for suffix in ("bin-ubuntu-x64.tar.gz", "bin-ubuntu-x64.zip"):
            for a in assets:
                if a["name"].endswith(suffix):
                    return a
        return None
    asset = pick()
    if asset is None:
        raise SystemExit("no plain ubuntu-x64 llama.cpp asset in latest release: "
                         + ", ".join(a["name"] for a in assets))
    name = asset["name"]
    print("prebuilt:", name, flush=True)
    archive = f"{WORK}/{name}"
    run(f"curl -sSL '{asset['browser_download_url']}' -o {archive}")
    dest = f"{WORK}/llbin"
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
    else:
        with tarfile.open(archive) as t:
            t.extractall(dest)
    bins = glob.glob(f"{dest}/**/llama-server", recursive=True)
    if not bins:
        raise SystemExit("llama-server not in prebuilt release")
    server = bins[0]
    run(f"chmod +x {server}")
    return server


def ask(port: int, prompt) -> str:
    convo = prompt if isinstance(prompt, list) else [{"role": "user", "content": prompt}]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + convo
    body = json.dumps({
        "messages": messages,
        "tools": EVAL_TOOLS, "tool_choice": "auto",
        "max_tokens": 384, "temperature": 0.3,
    }).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions",
                                 data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        data = json.load(r)
    message = data["choices"][0]["message"]
    calls = message.get("tool_calls") or []
    if calls:
        return json.dumps(calls, ensure_ascii=False)
    return message.get("content") or ""


def main() -> int:
    gguf = find_gguf()
    print("candidate:", gguf, f"({os.path.getsize(gguf)/1e9:.2f} GB)", flush=True)
    server = fetch_llama_server()
    libdir = os.path.dirname(server)
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = libdir + ":" + env.get("LD_LIBRARY_PATH", "")
    port = 11666
    proc = subprocess.Popen(
        [server, "--model", gguf, "--host", "127.0.0.1", "--port", str(port),
         "--ctx-size", "4096", "--jinja", "--no-webui", "--reasoning", "off",
         "--threads", str(os.cpu_count() or 4)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    try:
        deadline = time.time() + 600
        healthy = False
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
                    if r.status == 200:
                        healthy = True
                        break
            except Exception:
                time.sleep(1)
        if not healthy:
            raise SystemExit("llama-server did not become healthy")

        passed, failed, critical = [], [], []
        score = max_score = 0.0
        detail = {}
        for name, weight, prompt, expect, is_critical in SUITE:
            max_score += weight
            reply = ask(port, prompt)
            ok = bool(expect(reply))
            detail[name] = {"ok": ok, "weight": weight, "reply": reply[:400]}
            print(f"[{'PASS' if ok else 'FAIL'}] {name}: {reply[:160]!r}", flush=True)
            if ok:
                passed.append(name)
                score += weight
            else:
                failed.append(name)
                if is_critical:
                    critical.append(name)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    fraction = score / max_score if max_score else 0.0
    verdict = "PASS" if (fraction >= 0.75 and not critical) else "FAIL"
    report = {
        "model": os.path.basename(gguf),
        "score": round(score, 2), "max_score": round(max_score, 2),
        "fraction": round(fraction, 3), "threshold": 0.75,
        "passed_checks": passed, "failed_checks": failed,
        "critical_failures": critical, "verdict": verdict, "detail": detail,
    }
    with open(f"{WORK}/eval.json", "w") as handle:
        json.dump(report, handle, indent=2)
    print("EVAL", verdict, f"{score:.1f}/{max_score:.1f} ({fraction*100:.1f}%)",
          "critical:", critical, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
