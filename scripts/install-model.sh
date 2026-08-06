#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="${KILOBYTE_MODEL_DIR:-/var/lib/kilobyte/models}"
MODEL_FILE="$MODEL_DIR/kilobyte-qwen3-1.7b-q4_k_m.gguf"
MODEL_URL="https://huggingface.co/ggml-org/Qwen3-1.7B-GGUF/resolve/main/Qwen3-1.7B-Q4_K_M.gguf?download=true"
EXPECTED="d2387ca2dbfee2ffabce7120d3770dadca0b293052bc2f0e138fdc940d9bc7b5"
OWNER="${KILOBYTE_USER:-kilobyte}"

install -d -m 0750 -o "$OWNER" -g "$OWNER" "$MODEL_DIR"
if [[ -f "$MODEL_FILE" ]] && echo "$EXPECTED  $MODEL_FILE" | sha256sum --check --status; then
    echo "Kilobyte model already installed and verified."
    exit 0
fi

PART="$MODEL_FILE.part"
echo "Downloading the one Kilobyte brain (Qwen3 1.7B Q4_K_M, about 1.28 GB)..."
curl --fail --location --retry 8 --retry-all-errors --retry-delay 3 \
    --continue-at - --output "$PART" "$MODEL_URL"
echo "$EXPECTED  $PART" | sha256sum --check
chown "$OWNER:$OWNER" "$PART"
chmod 0640 "$PART"
mv -f "$PART" "$MODEL_FILE"
sync "$MODEL_FILE"
echo "Model installed atomically: $MODEL_FILE"
