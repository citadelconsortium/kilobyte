#!/usr/bin/env python3
"""Submit and retrieve the behavioural acceptance suite for the candidate GGUF on Kaggle.

The candidate GGUF is judged on a modern Kaggle CPU (the Core2 VM is too slow to score a
3B in reasonable time). The eval kernel sources the conversion kernel's output, starts
llama-server against the GGUF, runs the fixed suite, and writes eval.json.
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from kaggle_run import authenticate, download, wait


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", default="oversightnode")
    parser.add_argument("--convert-kernel", default="oversightnode/kilobyte-gguf-convert")
    parser.add_argument("--slug", default="kilobyte-eval")
    parser.add_argument("--out", type=Path, default=Path("output/eval"))
    parser.add_argument("--no-wait", action="store_true")
    args = parser.parse_args()
    api = authenticate()
    ref = f"{args.username}/{args.slug}"
    with tempfile.TemporaryDirectory() as raw:
        staging = Path(raw)
        shutil.copy2(Path(__file__).with_name("eval_kaggle.py"), staging / "kilobyte-eval.py")
        (staging / "kernel-metadata.json").write_text(json.dumps({
            "id": ref, "title": "Kilobyte Eval", "code_file": "kilobyte-eval.py",
            "language": "python", "kernel_type": "script", "enable_gpu": False,
            "enable_internet": True, "dataset_sources": [], "model_sources": [],
            "competition_sources": [], "kernel_sources": [args.convert_kernel],
        }, indent=2))
        api.kernels_push(str(staging))
    print(f"pushed eval notebook {ref} from {args.convert_kernel}")
    if args.no_wait:
        return 0
    state = wait(api, ref)
    if "complete" not in state.lower():
        print(f"eval did not complete cleanly: {state}")
        return 1
    download(api, ref, args.out)
    report = args.out / "eval.json"
    if report.exists():
        print(report.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
