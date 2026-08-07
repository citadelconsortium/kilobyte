"""Specialist agent profiles that orchestrate with Kilo to get grounded, solid results.

The single 1.5B brain cannot be made not to hallucinate by training alone; the framework
reduces error by forcing the model to work from evidence. A profile is a specialist mode
Kilo runs in for a task: a focused instruction that emphasises the grounding discipline
for that domain, plus the tools that domain actually needs.

Profiles are added as a separate system message after the cached base prompt, so selecting
one does not change the cacheable prefix. Kilo picks a profile from the request (or the
user names one), and the profile pushes it toward retrieval, verification and abstention —
the combination the research shows works, rather than any single trick.

Design intent (why each profile reads the way it does):
- research: retrieve, corroborate across sources, answer only from fetched content, cite,
  and flag disagreement or missing evidence. This is retrieval-augmented grounding.
- coding: never claim code works without running it; read the real files, make the
  smallest change, run tests, iterate on actual output. Grounding in the repo, not memory.
- security: evidence-driven — recon, read real tool output, hypothesise, test, verify. No
  finding asserted without evidence.
- systems: inspect the live machine with tools before concluding; diagnose from real logs
  and state.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    hint: str          # one line shown in menus
    instructions: str  # injected as a system message when active
    # Tools the profile leans on; empty means all available. Used to focus, not to block.
    tools: tuple[str, ...] = ()


RESEARCH = Profile(
    name="research",
    hint="web research grounded in cited sources",
    tools=("web_search", "web_fetch", "search_history", "remember"),
    instructions=(
        "Research mode. Answer only from evidence you fetch this turn.\n"
        "- Search, then open the actual pages with web_fetch; do not answer from memory.\n"
        "- Corroborate anything important across at least two independent sources.\n"
        "- Quote or closely paraphrase what the sources say and name the source (URL).\n"
        "- If sources disagree, say so and give both. If you cannot find support, say the\n"
        "  evidence is thin rather than guessing.\n"
        "- Do not invent statistics, dates, names or quotes."
    ),
)

CODING = Profile(
    name="coding",
    hint="repository-grounded engineering",
    tools=("read_file", "write_file", "list_files", "search_files", "run_command"),
    instructions=(
        "Coding mode. Ground every step in the real repository, not assumptions.\n"
        "- Read the relevant files and search the codebase before proposing a change.\n"
        "- Make the smallest change that fixes the problem.\n"
        "- Never claim code compiles, runs or passes until you have run it and read the\n"
        "  output. Quote the failing line or test when diagnosing.\n"
        "- If you are unsure how something behaves, run it and find out rather than guess."
    ),
)

SECURITY = Profile(
    name="security",
    hint="offensive and defensive security, evidence-driven",
    tools=("run_command", "web_search", "web_fetch", "read_file", "search_files"),
    instructions=(
        "Security mode — offensive AND defensive. Every conclusion follows from evidence.\n"
        "Offensive (red team): reconnaissance, enumeration, vulnerability discovery, exploit\n"
        "  development and validation, privilege escalation, and post-exploitation — on\n"
        "  systems the operator is authorised to test.\n"
        "Defensive (blue team): hardening, detection engineering, log and traffic analysis,\n"
        "  incident response, forensics, and remediation guidance.\n"
        "Discipline for both:\n"
        "- Reconnaissance first: gather real output before interpreting it.\n"
        "- Interpret only what the tools actually returned; never assert a vulnerability,\n"
        "  version, service, or compromise you have not confirmed.\n"
        "- Method: objective -> recon -> read evidence -> hypothesis -> targeted test ->\n"
        "  verify -> report, distinguishing what is confirmed from what is suspected.\n"
        "- For any offensive step, note the authorisation assumption and prefer the least\n"
        "  destructive check that proves the point. Report findings with evidence and a fix."
    ),
)

SYSTEMS = Profile(
    name="systems",
    hint="live-machine diagnosis",
    tools=("run_command", "system_info", "read_file", "search_files"),
    instructions=(
        "Systems mode. Diagnose from the live machine, not from memory.\n"
        "- Inspect real state — services, ports, logs, config, resources — with tools.\n"
        "- Base the diagnosis on what the commands returned; quote the decisive line.\n"
        "- Confirm a fix worked by re-checking, not by assuming."
    ),
)

GENERAL = Profile(
    name="general",
    hint="general assistant",
    instructions=(
        "Answer directly. For anything you are not sure of, check with a tool or say you\n"
        "are not certain rather than guessing."
    ),
)

CONVERSATION = Profile(
    name="conversation",
    hint="understand intent, then follow through to a finished answer",
    instructions=(
        "Conversation mode. Understand what the user actually wants, then deliver it.\n"
        "- Read the request for its real intent, not just its words. If it is ambiguous in a\n"
        "  way that changes the answer, ask one short clarifying question; otherwise take the\n"
        "  most reasonable reading and proceed — do not stall on trivia.\n"
        "- If you plainly know the answer (a fact, arithmetic, a definition), just give it,\n"
        "  confidently and directly. Do not hedge on what you obviously know.\n"
        "- Never announce an action instead of doing it. Do not reply 'let me calculate' or\n"
        "  'I'll check' and stop — either call the tool now or give the answer now.\n"
        "- Carry the task to a finished result before you reply. If it takes several steps,\n"
        "  do them; stopping half-way with a promise is a failure, not an answer.\n"
        "- Keep the reply tight and useful: the answer first, then only what is needed."
    ),
)

PROFILES: dict[str, Profile] = {
    p.name: p for p in (RESEARCH, CODING, SECURITY, SYSTEMS, GENERAL, CONVERSATION)
}

# Keyword hints for auto-selecting a profile when the user has not named one. Deliberately
# conservative: an unclear request falls through to general rather than a wrong specialist.
_ROUTES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("research", ("research", "find out", "look up", "latest", "news", "compare", "who is", "what is the current")),
    ("coding", ("code", "bug", "compile", "build", "test", "refactor", "function", "repository", "repo", "stack trace", "error in")),
    ("security", ("hack", "hacking", "exploit", "vulnerability", "cve", "nmap", "recon", "pentest", "penetration test", "malware", "reverse engineer", "forensic", "payload", "port scan", "privilege escalation")),
    ("systems", ("systemd", "service", "ssh", "firewall", "disk", "memory", "process", "log", "network", "docker", "container", "daemon")),
)


def select(text: str, explicit: str | None = None) -> Profile:
    """Choose a profile for a request. An explicitly named profile always wins; otherwise
    match keywords, and fall back to the conversation agent when nothing clearly fits — so
    even an unrouted request gets intent-understanding and follow-through discipline instead
    of the model being left to trail off."""
    # Friendly aliases so a user's natural word reaches the right specialist.
    _ALIASES = {"hacking": "security", "hack": "security", "pentest": "security",
                "chat": "conversation", "convo": "conversation", "auto": ""}
    if explicit:
        explicit = _ALIASES.get(explicit.lower().strip(), explicit.lower().strip())
    if explicit and explicit in PROFILES:
        return PROFILES[explicit]
    lowered = text.lower()
    for name, words in _ROUTES:
        if any(word in lowered for word in words):
            return PROFILES[name]
    return CONVERSATION
