#!/usr/bin/env bash
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
    echo "Run with sudo: sudo ./scripts/install.sh" >&2
    exit 1
fi
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KILO_USER="${KILOBYTE_USER:-${SUDO_USER:-kilobyte}}"
if ! id "$KILO_USER" >/dev/null 2>&1; then
    echo "User does not exist: $KILO_USER" >&2
    exit 1
fi
command -v python >/dev/null
command -v llama-server >/dev/null || { echo "Install llama-cpp first." >&2; exit 1; }

echo "Installing Kilobyte application..."
install -d -m 0755 /opt/kilobyte/app /etc/kilobyte
install -d -m 0750 -o "$KILO_USER" -g "$KILO_USER" /var/lib/kilobyte /var/lib/kilobyte/models /var/log/kilobyte
cp -a "$ROOT/src" "$ROOT/pyproject.toml" /opt/kilobyte/app/
chown -R root:root /opt/kilobyte/app
find /opt/kilobyte/app -type d -exec chmod 0755 {} +
find /opt/kilobyte/app -type f -exec chmod 0644 {} +
install -m 0755 "$ROOT/scripts/kilo-wrapper" /usr/local/bin/kilo
install -m 0644 "$ROOT/systemd/kilobyte.service" /etc/systemd/system/kilobyte.service
if [[ ! -f /etc/kilobyte/policy.json ]]; then
    install -m 0600 -o "$KILO_USER" -g "$KILO_USER" "$ROOT/config/policy.json" /etc/kilobyte/policy.json
fi
systemctl daemon-reload
systemctl enable kilobyte.service
echo "Application installed. Next: sudo KILOBYTE_USER=$KILO_USER ./scripts/install-model.sh"

