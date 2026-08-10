#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="${KILOBYTE_MODEL_DIR:-/var/lib/kilobyte/models}"
MODEL_FILE="$MODEL_DIR/kilobyte-4.1-3b-q4_k_m.gguf"
LEGACY_MODEL_FILE="$MODEL_DIR/kilobyte.gguf"
MODEL_URL="https://github.com/citadelconsortium/kilobyte/releases/download/brain-1.2/kilobyte-4.1-3b-q4_k_m.gguf"
EXPECTED="72ec67bc6f964ce97f966cc83719100da00e058468aa0a5258cd7286a56cc8d2"
OWNER="${KILOBYTE_USER:-kilobyte}"
GROUP="$(id -gn "$OWNER" 2>/dev/null || echo "$OWNER")"

install -d -m 0750 -o "$OWNER" -g "$GROUP" "$MODEL_DIR"
if [[ -f "$MODEL_FILE" ]] && echo "$EXPECTED  $MODEL_FILE" | sha256sum --check --status; then
    echo "Kilobyte model already installed and verified."
    exit 0
fi
if [[ -f "$LEGACY_MODEL_FILE" ]] && echo "$EXPECTED  $LEGACY_MODEL_FILE" | sha256sum --check --status; then
    mv -f "$LEGACY_MODEL_FILE" "$MODEL_FILE"
    chown "$OWNER:$GROUP" "$MODEL_FILE"
    echo "Migrated verified legacy model to descriptive filename: $MODEL_FILE"
    exit 0
fi

PART="$MODEL_FILE.part"
echo "Downloading the one Kilobyte brain (custom Q4_K_M, about 2.1 GB)..."
curl --fail --location --retry 8 --retry-all-errors --retry-delay 3 \
    --continue-at - --output "$PART" "$MODEL_URL"
echo "$EXPECTED  $PART" | sha256sum --check
chown "$OWNER:$GROUP" "$PART"
chmod 0640 "$PART"
mv -f "$PART" "$MODEL_FILE"
sync "$MODEL_FILE"
echo "Model installed atomically: $MODEL_FILE"
