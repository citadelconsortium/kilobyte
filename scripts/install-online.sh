#!/usr/bin/env bash
set -euo pipefail

# One-line installer entry point:
# curl -fsSL https://raw.githubusercontent.com/0v3r51ght/kilobyte/main/scripts/install-online.sh | bash
REPO_URL="${KILOBYTE_REPO_URL:-https://github.com/citadelconsortium/kilobyte}"
BRANCH="${KILOBYTE_BRANCH:-main}"
OWNER="${KILOBYTE_USER:-${SUDO_USER:-${USER:-kilobyte}}}"
WORK="$(mktemp -d -t kilobyte-install.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

command -v curl >/dev/null || { echo "curl is required" >&2; exit 1; }
command -v tar >/dev/null || { echo "tar is required" >&2; exit 1; }
curl --fail --location --retry 5 --output "$WORK/source.tar.gz" \
  "$REPO_URL/archive/refs/heads/$BRANCH.tar.gz"
tar -xzf "$WORK/source.tar.gz" -C "$WORK"
ROOT="$(find "$WORK" -mindepth 1 -maxdepth 1 -type d -name '*-'"$BRANCH" -print -quit)"
[[ -n "$ROOT" ]] || { echo "downloaded repository has no source directory" >&2; exit 1; }
sudo KILOBYTE_USER="$OWNER" "$ROOT/scripts/install.sh"
sudo KILOBYTE_USER="$OWNER" "$ROOT/scripts/install-model.sh"
sudo systemctl restart kilobyte.service
echo "Kilobyte installed. Run: kilo"
