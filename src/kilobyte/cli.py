from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .config import MODEL_QUANTIZATION, MODEL_REPOSITORY, MODEL_SHA256, Settings
from .doctor import run_checks
from .errors import KilobyteError
from .resources import ResourceManager
from .rpc import RPCClient
from .tui import GREEN, RESET, TerminalUI, YELLOW


def json_print(value: Any) -> None:
    print(json.dumps(value, indent=2, ensure_ascii=False))


def runtime_summary(metadata: dict[str, Any]) -> dict[str, Any]:
    """Keep model-info useful without printing llama.cpp's multi-page template."""
    caps = metadata.get("chat_template_caps") or {}
    defaults = metadata.get("default_generation_settings") or {}
    return {
        "build": metadata.get("build_info"),
        "model": metadata.get("model_alias") or metadata.get("model_path"),
        "context_size": defaults.get("n_ctx"),
        "tool_calling": bool(caps.get("supports_tool_calls")),
        "sleeping": bool(metadata.get("is_sleeping", False)),
    }


def service_action(action: str) -> int:
    command = ["systemctl", action, "kilobyte.service"]
    if os.geteuid() != 0:
        command.insert(0, "sudo")
    return subprocess.run(command, check=False).returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kilo", description="Kilobyte local-first terminal AI")
    parser.add_argument("--version", action="version", version=f"Kilobyte {__version__}")
    sub = parser.add_subparsers(dest="command")
    chat = sub.add_parser("chat", help="send one prompt and stream the answer")
    chat.add_argument("text", nargs="+")
    sub.add_parser("status", help="show daemon, model and resource status")
    doctor = sub.add_parser("doctor", help="run installation and health checks")
    doctor.add_argument("--verify-model", action="store_true", help="read the full GGUF and verify SHA-256")
    sub.add_parser("resources", help="show the live resource profile")
    sub.add_parser("model-info", help="show the one installed brain")
    sub.add_parser("version", help="show Kilobyte and runtime versions")
    logs = sub.add_parser("logs", help="show service logs")
    logs.add_argument("-n", "--lines", type=int, default=100)
    for action in ("restart", "stop", "start"):
        sub.add_parser(action, help=f"{action} the Kilobyte service")
    benchmark = sub.add_parser("benchmark", help="measure a short real inference")
    benchmark.add_argument("--prompt", default="Reply with exactly: Kilobyte is ready.")
    return parser


async def async_main(args: argparse.Namespace, settings: Settings) -> int:
    client = RPCClient(settings.socket_path)
    if args.command is None:
        await TerminalUI(client).run()
        return 0
    if args.command == "chat":
        await TerminalUI(client).ask(" ".join(args.text))
    elif args.command == "status":
        json_print(await client.request("status"))
    elif args.command == "resources":
        try:
            json_print(await client.request("resources"))
        except (FileNotFoundError, ConnectionError):
            json_print(ResourceManager(settings).profile().to_dict())
    elif args.command == "model-info":
        data = {"repository": MODEL_REPOSITORY, "quantization": MODEL_QUANTIZATION, "path": str(settings.model_path), "sha256": MODEL_SHA256, "installed": settings.model_path.is_file()}
        try:
            data["runtime"] = runtime_summary(await client.request("model_info"))
        except (FileNotFoundError, ConnectionError):
            data["runtime"] = None
        json_print(data)
    elif args.command == "version":
        print(f"Kilobyte {__version__}")
        try:
            metadata = await client.request("model_info")
            if metadata:
                print(f"runtime model: {metadata.get('model_alias', metadata.get('model_path', 'loaded'))}")
        except (FileNotFoundError, ConnectionError):
            pass
    elif args.command == "doctor":
        checks = await asyncio.to_thread(run_checks, settings, args.verify_model)
        for check in checks:
            icon = f"{GREEN}PASS" if check.ok else f"{YELLOW}{'WARN' if check.warning else 'FAIL'}"
            print(f"{icon}{RESET}  {check.name:<20} {check.detail}")
        return 0 if all(item.ok or item.warning for item in checks) else 1
    elif args.command == "benchmark":
        started = time.monotonic()
        count = 0
        async for event in client.stream("chat", text=args.prompt, cwd=str(Path.cwd())):
            if event.get("type") == "token":
                piece = event.get("text", "")
                count += len(piece.split())
                print(piece, end="", flush=True)
        elapsed = time.monotonic() - started
        print(f"\n\n{count} approximate word-tokens in {elapsed:.2f}s ({count / max(elapsed, 0.001):.2f}/s end-to-end)")
    return 0


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = Settings()
    if args.command in {"start", "stop", "restart"}:
        raise SystemExit(service_action(args.command))
    if args.command == "logs":
        raise SystemExit(subprocess.run(["journalctl", "-u", "kilobyte.service", "-n", str(args.lines), "--no-pager"], check=False).returncode)
    try:
        raise SystemExit(asyncio.run(async_main(args, settings)))
    except (FileNotFoundError, ConnectionRefusedError):
        print(f"{YELLOW}Kilobyte daemon is not running.{RESET} Try: sudo systemctl start kilobyte", file=sys.stderr)
        raise SystemExit(2) from None
    except KilobyteError as exc:
        print(f"{YELLOW}Kilobyte error:{RESET} {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
