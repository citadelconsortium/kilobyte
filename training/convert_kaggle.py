#!/usr/bin/env python3
"""Kaggle conversion notebook: turn the trained merged HF weights into a quantised GGUF.

Runs as a separate Kaggle kernel (internet ON) that mounts the training kernel's output
(the merged weights) as an input, builds llama.cpp's converter + quantiser, and produces
the one canonical brain ``kilobyte.gguf`` (Q4_K_M) in /kaggle/working.

Kernel metadata for this file:
    kernel_sources : ["oversightnode/kilobyte-train"]   (mounts the merged weights)
    enable_internet: true                                (to fetch llama.cpp + deps)
    enable_gpu     : false                               (conversion is CPU work)
Outputs:
    /kaggle/working/kilobyte.gguf     the quantised brain, ready to download
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys

LLAMA = "/kaggle/working/llama.cpp"
OUT = "/kaggle/working/kilobyte.gguf"
F16 = "/kaggle/working/kilobyte-f16.gguf"
QUANT = "Q4_K_M"


def run(cmd: str) -> None:
    print("+", cmd, flush=True)
    subprocess.check_call(cmd, shell=True)


def find_merged() -> str:
    """Locate the merged HF weights among the mounted inputs (a dir with config.json and a
    real, multi-hundred-MB safetensors — not the tiny LoRA adapter)."""
    for cfg in glob.glob("/kaggle/input/**/config.json", recursive=True):
        d = os.path.dirname(cfg)
        weights = glob.glob(os.path.join(d, "*.safetensors")) + glob.glob(os.path.join(d, "*.bin"))
        if weights and any(os.path.getsize(w) > 200 * 1024 * 1024 for w in weights):
            return d
    # Fall back to any 'merged' dir even if size heuristics fail.
    for cfg in glob.glob("/kaggle/input/**/merged/config.json", recursive=True):
        return os.path.dirname(cfg)
    raise SystemExit("merged weights not found under /kaggle/input")


def main() -> int:
    merged = find_merged()
    print("merged weights at:", merged, flush=True)
    for w in glob.glob(os.path.join(merged, "*.safetensors")):
        print(f"  weight {os.path.getsize(w) / 1e9:.2f} GB  {os.path.basename(w)}", flush=True)

    if not os.path.isdir(LLAMA):
        run(f"git clone --depth 1 https://github.com/ggml-org/llama.cpp {LLAMA}")
    run(f"pip install -q gguf sentencepiece protobuf")
    # Build only the quantiser (fast); the converter is a pure-python script.
    run(f"cmake -S {LLAMA} -B {LLAMA}/build -DGGML_NATIVE=OFF -DLLAMA_CURL=OFF -DBUILD_SHARED_LIBS=OFF")
    run(f"cmake --build {LLAMA}/build --target llama-quantize -j4")

    run(f"python {LLAMA}/convert_hf_to_gguf.py {merged} --outfile {F16} --outtype f16")
    quant_bin = f"{LLAMA}/build/bin/llama-quantize"
    if not os.path.exists(quant_bin):
        quant_bin = f"{LLAMA}/build/llama-quantize"
    run(f"{quant_bin} {F16} {OUT} {QUANT}")
    os.remove(F16)  # keep only the final quantised brain to shrink the download
    size = os.path.getsize(OUT)
    print(f"GGUF READY: {OUT}  {size / 1e9:.2f} GB", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
