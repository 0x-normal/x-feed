#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo 'Run with sudo bash deploy/update.sh'; exit 1; fi
export PATH="/opt/x-engine-node/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -x /opt/x-engine/.venv/bin/python ]]; then echo 'Run install.sh first.'; exit 1; fi
if [[ "$source_dir" == /opt/x-engine ]]; then echo 'Run from your separate Git checkout.'; exit 1; fi
/opt/x-engine/.venv/bin/python "$source_dir/deploy/copy_package.py" "$source_dir" /opt/x-engine --check
systemctl stop x-engine-worker x-engine-dashboard
update_failed() {
    trap - ERR
    systemctl stop x-engine-worker x-engine-dashboard || true
    echo 'Update failed; services stopped. Fix the error and rerun this script.' >&2
    echo 'Diagnostics: journalctl -u x-engine-dashboard -u x-engine-worker -n 60 --no-pager' >&2
}
trap update_failed ERR
/opt/x-engine/.venv/bin/python "$source_dir/deploy/copy_package.py" "$source_dir" /opt/x-engine
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
echo 'Waiting for the dashboard API and Smart Account catalog (up to 60 seconds)...'
/opt/x-engine/.venv/bin/python "$source_dir/deploy/check_health.py"
systemctl is-active --quiet x-engine-worker
systemctl is-active --quiet x-engine-dashboard
echo 'Updated application code. Existing data and sessions were preserved.'
