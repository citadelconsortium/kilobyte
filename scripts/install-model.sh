#!/usr/bin/env bash
set -euo pipefail

MODEL_DIR="${KILOBYTE_MODEL_DIR:-/var/lib/kilobyte/models}"
MODEL_FILE="$MODEL_DIR/kilobyte.gguf"
MODEL_URL="https://github.com/citadelconsortium/kilobyte/releases/download/brain-1.1/kilobyte.gguf"
EXPECTED="6cdcca6b3876fa07d841dfc718e10a10bd128d6602cd73a23a54109b4333b6b7"
OWNER="${KILOBYTE_USER:-kilobyte}"
GROUP="$(id -gn "$OWNER" 2>/dev/null || echo "$OWNER")"

install -d -m 0750 -o "$OWNER" -g "$GROUP" "$MODEL_DIR"
if [[ -f "$MODEL_FILE" ]] && echo "$EXPECTED  $MODEL_FILE" | sha256sum --check --status; then
    echo "Kilobyte model already installed and verified."
    exit 0
fi

PART="$MODEL_FILE.part"
echo "Downloading the one Kilobyte brain (custom Q4_K_M, about 0.94 GB)..."
curl --fail --location --retry 8 --retry-all-errors --retry-delay 3 \
    --continue-at - --output "$PART" "$MODEL_URL"
echo "$EXPECTED  $PART" | sha256sum --check
chown "$OWNER:$GROUP" "$PART"
chmod 0640 "$PART"
mv -f "$PART" "$MODEL_FILE"
sync "$MODEL_FILE"
echo "Model installed atomically: $MODEL_FILE"
