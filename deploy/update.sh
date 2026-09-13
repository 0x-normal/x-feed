#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo 'Run with sudo bash deploy/update.sh'; exit 1; fi
export PATH="/opt/x-engine-node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x /opt/x-engine/.venv/bin/python ]]; then echo 'Run install.sh first.'; exit 1; fi
if [[ "$source_dir" == /opt/x-engine ]]; then echo 'Run from your separate Git checkout.'; exit 1; fi
systemctl stop x-engine-worker x-engine-dashboard
trap 'echo "Update failed; services remain stopped. Fix the error and rerun this script."' ERR
for item in "$source_dir"/x_engine/*.py "$source_dir"/x_engine/dashboard.html; do
    install -m 644 "$item" /opt/x-engine/x_engine/
done
install -m 644 "$source_dir/pyproject.toml" /opt/x-engine/pyproject.toml
for item in package.json package-lock.json bridge.mjs; do
    install -m 644 "$source_dir/tools/rettiwt/$item" "/opt/x-engine/tools/rettiwt/$item"
done
/opt/x-engine/.venv/bin/python -m pip install -e /opt/x-engine
cd /opt/x-engine/tools/rettiwt
npm ci --omit=dev --omit=optional --ignore-scripts --no-audit --no-fund
install -m 644 "$source_dir/deploy/x-engine-worker.service" /etc/systemd/system/
install -m 644 "$source_dir/deploy/x-engine-dashboard.service" /etc/systemd/system/
systemctl daemon-reload
systemctl start x-engine-worker x-engine-dashboard
systemctl is-active x-engine-worker x-engine-dashboard
echo 'Updated application code. Existing data and sessions were preserved.'
