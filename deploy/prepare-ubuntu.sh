#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo 'Run with sudo bash deploy/prepare-ubuntu.sh'; exit 1; fi
apt-get update
apt-get install -y python3 python3-venv ca-certificates curl xz-utils
python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
if [[ -x /opt/x-engine-node/bin/node ]]; then
    /opt/x-engine-node/bin/node -e 'if(Number(process.versions.node.split(".")[0])<22)throw Error("Node 22+ required")'
    exit 0
fi
if [[ -e /opt/x-engine-node ]]; then echo '/opt/x-engine-node exists but has no usable runtime.'; exit 1; fi
case "$(uname -m)" in
    x86_64) node_arch=x64 ;;
    aarch64) node_arch=arm64 ;;
    *) echo 'Unsupported CPU architecture.'; exit 1 ;;
esac
node_version=22.23.2
node_archive="node-v${node_version}-linux-${node_arch}.tar.xz"
download_dir="$(mktemp -d /tmp/x-engine-node.XXXXXXXX)"
cd "$download_dir"
curl --fail --show-error --location --proto '=https' --tlsv1.2 -O "https://nodejs.org/dist/v${node_version}/${node_archive}"
curl --fail --show-error --location --proto '=https' --tlsv1.2 -O "https://nodejs.org/dist/v${node_version}/SHASUMS256.txt"
awk -v archive="$node_archive" '$2 == archive {print}' SHASUMS256.txt > selected-sha256.txt
test -s selected-sha256.txt
sha256sum --check selected-sha256.txt
install -d -m 755 /opt/x-engine-node
tar -xJf "$node_archive" --strip-components=1 -C /opt/x-engine-node
/opt/x-engine-node/bin/node --version
echo "Node installed for X Feed only. Download files remain in $download_dir."
