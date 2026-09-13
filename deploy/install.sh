#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo 'Run with sudo bash deploy/install.sh'; exit 1; fi
export PATH="/opt/x-engine-node/bin:/usr/local/bin:/usr/bin:/bin"
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
/opt/x-engine-node/bin/node -e 'if(Number(process.versions.node.split(".")[0])<22)throw Error("Run prepare-ubuntu.sh first")'
command -v npm >/dev/null
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -e /opt/x-engine ]]; then echo '/opt/x-engine already exists; refusing to replace an installation.'; exit 1; fi
id xengine >/dev/null 2>&1 || useradd --system --home-dir /var/lib/x-engine --shell /usr/sbin/nologin xengine
install -d -m 755 /opt/x-engine
install -d -o xengine -g xengine -m 700 /var/lib/x-engine
# Explicit source allowlist: no Windows environment, account files, or data.
cp -R "$source_dir/x_engine" /opt/x-engine/
install -m 644 "$source_dir/pyproject.toml" /opt/x-engine/pyproject.toml
install -d -m 755 /opt/x-engine/tools/rettiwt
for item in package.json package-lock.json bridge.mjs; do
    install -m 644 "$source_dir/tools/rettiwt/$item" "/opt/x-engine/tools/rettiwt/$item"
done
python3 -m venv /opt/x-engine/.venv
/opt/x-engine/.venv/bin/python -m pip install -e /opt/x-engine
cd /opt/x-engine/tools/rettiwt
npm ci --omit=dev --ignore-scripts --no-audit --no-fund
install -m 644 "$source_dir/deploy/x-engine-worker.service" /etc/systemd/system/
install -m 644 "$source_dir/deploy/x-engine-dashboard.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable x-engine-worker x-engine-dashboard
echo 'Installed. Restore your encrypted transfer bundle before starting the services.'
