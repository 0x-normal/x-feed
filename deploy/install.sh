#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo 'Run with sudo bash deploy/install.sh'; exit 1; fi
export PATH="/opt/x-engine-node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
/opt/x-engine-node/bin/node -e 'if(Number(process.versions.node.split(".")[0])<22)throw Error("Run prepare-ubuntu.sh first")'
command -v npm >/dev/null
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -gt 1 || ( $# -eq 1 && "$1" != --resume ) ]]; then echo 'Usage: bash deploy/install.sh [--resume]'; exit 1; fi
if [[ -e /opt/x-engine ]]; then
    if [[ "${1:-}" != --resume ]]; then echo '/opt/x-engine already exists. To finish a failed installation, use --resume.'; exit 1; fi
    python3 -c 'import pathlib,tomllib; p=pathlib.Path("/opt/x-engine"); assert tomllib.loads((p/"pyproject.toml").read_text())["project"]["name"] == "x-engine" and (p/"x_engine/cli.py").is_file(), "Existing directory is not an X Feed installation"'
    if [[ -e /etc/systemd/system/x-engine-worker.service || -e /etc/systemd/system/x-engine-dashboard.service ]]; then
        echo 'Services are already installed. Use deploy/update.sh for an existing deployment.'; exit 1
    fi
fi
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
npm ci --omit=dev --omit=optional --ignore-scripts --no-audit --no-fund
install -m 644 "$source_dir/deploy/x-engine-worker.service" /etc/systemd/system/
install -m 644 "$source_dir/deploy/x-engine-dashboard.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable x-engine-worker x-engine-dashboard
echo 'Installed. Restore your encrypted transfer bundle before starting the services.'
