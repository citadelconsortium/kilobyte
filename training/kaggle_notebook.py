#!/usr/bin/env python3
"""Kilobyte training notebook — runs on a Kaggle GPU session.

This is the only stage that needs a GPU. It fine-tunes the base instruct model with QLoRA
on the Kilobyte dataset, merges the adapter, converts to GGUF and quantises to the
canonical quant, then load-tests the result. Everything CPU-side (dataset validation,
formatting) is done before this runs, so no GPU time is wasted here.

On Kaggle the dataset is attached at /kaggle/input/<dataset_slug>/ and outputs go to
/kaggle/working. Locally these default to ./data and ./output so the script is testable.

It reads config.json (see config.example.json). It never contains or prints credentials.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def log(msg: str) -> None:
    print(f"[kilobyte] {msg}", flush=True)


def load_config() -> dict:
    candidates = [Path("config.json"), Path(__file__).parent / "config.json"]
    # On Kaggle the config is bundled into the attached dataset input directory.
    import glob
    candidates += [Path(p) for p in glob.glob("/kaggle/input/*/config.json")]
    candidates.append(Path(__file__).parent / "config.example.json")
    for candidate in candidates:
        if candidate.is_file():
            return json.loads(candidate.read_text())
    raise SystemExit("config.json not found")


def resolve_paths(config: dict) -> tuple[Path, Path]:
    on_kaggle = Path("/kaggle/working").is_dir()
    if on_kaggle:
        slug = config["kaggle"]["dataset_slug"]
        data_dir = Path(f"/kaggle/input/{slug}")
        out_dir = Path("/kaggle/working/output")
    else:
        data_dir = Path("data")
        out_dir = Path("output")
    out_dir.mkdir(parents=True, exist_ok=True)
    return data_dir, out_dir


def to_chat(example: dict) -> dict:
    """Convert a spec conversation into the messages shape the chat template expects,
    flattening tool calls and results into assistant/tool turns."""
    messages = []
    for message in example["messages"]:
        role = message["role"]
        entry = {"role": role, "content": message.get("content", "")}
        if role == "assistant" and message.get("tool_calls"):
            calls = "\n".join(
                json.dumps({"tool": c["name"], "arguments": c["arguments"]}, ensure_ascii=False)
                for c in message["tool_calls"]
            )
            entry["content"] = (entry["content"] + "\n" + calls).strip()
        if role == "tool":
            entry = {"role": "tool", "content": message.get("content", "")}
        messages.append(entry)
    return {"messages": messages}


def train(config: dict, data_dir: Path, out_dir: Path) -> Path:
    # Imports are inside the function so the file can be inspected and its helpers tested
    # without the GPU training stack installed.
    from datasets import load_dataset
    from trl import SFTConfig, SFTTrainer
    from unsloth import FastLanguageModel
    from unsloth.chat_templates import get_chat_template

    tc = config["train"]
    log(f"loading base model {config['base_model']} in 4-bit")
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=config["base_model"],
        max_seq_length=config["max_seq_len"],
        load_in_4bit=tc["load_in_4bit"],
        dtype=None,
    )
    tokenizer = get_chat_template(tokenizer, chat_template="chatml")
    model = FastLanguageModel.get_peft_model(
        model,
        r=config["lora"]["r"],
        lora_alpha=config["lora"]["alpha"],
        lora_dropout=config["lora"]["dropout"],
        target_modules=config["lora"]["target_modules"],
        use_gradient_checkpointing="unsloth" if tc["gradient_checkpointing"] else False,
        random_state=tc["seed"],
    )

    train_file = str(data_dir / "kilobyte-sft.jsonl")
    log(f"loading dataset {train_file}")
    raw = load_dataset("json", data_files=train_file, split="train")
    raw = raw.map(to_chat)

    def render(batch):
        return {"text": [tokenizer.apply_chat_template(m, tokenize=False, add_generation_prompt=False) for m in batch["messages"]]}

    dataset = raw.map(render, batched=True, remove_columns=raw.column_names)

    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        args=SFTConfig(
            per_device_train_batch_size=tc["per_device_batch_size"],
            gradient_accumulation_steps=tc["grad_accum"],
            warmup_ratio=tc["warmup_ratio"],
            num_train_epochs=tc["epochs"],
            learning_rate=tc["learning_rate"],
            lr_scheduler_type=tc["lr_scheduler"],
            weight_decay=tc["weight_decay"],
            bf16=tc["bf16"],
            optim=tc["optim"],
            packing=tc["packing"],
            seed=tc["seed"],
            save_steps=tc["save_steps"],
            dataset_text_field="text",
            max_seq_length=config["max_seq_len"],
            output_dir=str(out_dir / "checkpoints"),
            report_to="none",
        ),
    )
    stats = trainer.train()
    (out_dir / "train_metrics.json").write_text(json.dumps(stats.metrics, indent=2, default=str))
    log(f"training done: {stats.metrics}")

    adapter = out_dir / "lora-adapter"
    model.save_pretrained(str(adapter))
    tokenizer.save_pretrained(str(adapter))

    merged = out_dir / "merged"
    log("merging adapter into base weights (16-bit)")
    model.save_pretrained_merged(str(merged), tokenizer, save_method="merged_16bit")
    return merged


def convert_and_quantise(config: dict, merged: Path, out_dir: Path) -> Path:
    """Convert merged HF weights to GGUF and quantise to the canonical quant.

    Uses llama.cpp's own converter and quantiser — the supported upstream path — rather
    than reimplementing conversion. The exact commands are recorded for reproducibility.
    """
    llama = Path(os.environ.get("LLAMA_CPP_DIR", "/kaggle/working/llama.cpp"))
    if not llama.is_dir():
        log("cloning llama.cpp for conversion")
        subprocess.run(["git", "clone", "--depth", "1", "https://github.com/ggml-org/llama.cpp", str(llama)], check=True)
        subprocess.run(["pip", "install", "-r", str(llama / "requirements.txt")], check=True)

    f16 = out_dir / "kilobyte-f16.gguf"
    convert_cmd = [sys.executable, str(llama / "convert_hf_to_gguf.py"), str(merged), "--outfile", str(f16), "--outtype", "f16"]
    log("converting to GGUF (f16)")
    subprocess.run(convert_cmd, check=True)

    # The quantiser binary must be built once in the session.
    quantiser = llama / "build" / "bin" / "llama-quantize"
    if not quantiser.is_file():
        log("building llama.cpp quantiser")
        subprocess.run(["cmake", "-S", str(llama), "-B", str(llama / "build")], check=True)
        subprocess.run(["cmake", "--build", str(llama / "build"), "--target", "llama-quantize", "-j"], check=True)

    out = out_dir / config["output_name"]
    quant_cmd = [str(quantiser), str(f16), str(out), config["quantisation"]]
    log(f"quantising to {config['quantisation']}")
    subprocess.run(quant_cmd, check=True)

    (out_dir / "conversion.json").write_text(json.dumps({
        "base_model": config["base_model"],
        "quantisation": config["quantisation"],
        "convert_command": " ".join(convert_cmd),
        "quantise_command": " ".join(quant_cmd),
    }, indent=2))
    return out


def install_deps() -> None:
    """Install the training stack on Kaggle before importing it.

    Kaggle's GPU image ships torch and transformers but not Unsloth/TRL, so the run must
    install them first. Skipped off Kaggle, where the environment is managed by the caller.
    """
    if not Path("/kaggle/working").is_dir():
        return
    log("installing training dependencies")
    packages = ["unsloth", "trl", "peft", "accelerate", "bitsandbytes", "datasets", "sentencepiece", "protobuf"]
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--upgrade", *packages], check=True)


def main() -> int:
    config = load_config()
    install_deps()
    data_dir, out_dir = resolve_paths(config)
    merged = train(config, data_dir, out_dir)
    candidate = convert_and_quantise(config, merged, out_dir)
    # A checksum for the audit trail and for staging on the Kilo host.
    import hashlib
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    (out_dir / "candidate.sha256").write_text(f"{digest}  {candidate.name}\n")
    log(f"candidate ready: {candidate} ({candidate.stat().st_size // (1024*1024)} MiB) sha256 {digest}")
    log("this is a CANDIDATE — evaluate it before promoting it to Kilo's brain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
