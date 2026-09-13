#!/usr/bin/env bash
set -euo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
umask 077
if [[ $EUID -ne 0 ]]; then echo 'Run with sudo bash deploy/enable-web.sh VPS_IP'; exit 1; fi
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ $# -ne 1 ]]; then echo 'Usage: bash deploy/enable-web.sh PUBLIC_IPV4'; exit 1; fi
site_host="$(python3 - "$1" <<'PY'
import ipaddress, sys
try:
    value = ipaddress.IPv4Address(sys.argv[1])
    if not value.is_global or value.is_multicast:
        raise ValueError()
except ValueError:
    sys.exit('Use your VPS public IPv4 address, for example 43.106.141.82.')
print(value)
PY
)"
if [[ ! -x /opt/x-engine/.venv/bin/python ]]; then echo 'Install X Feed first.'; exit 1; fi
if [[ "$source_dir" == /opt/x-engine ]]; then echo 'Run from your Git checkout, such as /root/x-feed.'; exit 1; fi
if [[ -e /etc/systemd/system/x-feed-web.service || -e /etc/x-feed-web/Caddyfile ]]; then
    echo 'Website setup already exists. To retry after fixing firewall/network access:'
    echo 'systemctl restart x-feed-web'
    echo 'journalctl -u x-feed-web -n 40 --no-pager'
    exit 1
fi
systemctl is-active --quiet x-engine-dashboard || { echo 'Start x-engine-dashboard first.'; exit 1; }
for dependency in ss curl tar sha512sum; do
    command -v "$dependency" >/dev/null || { echo "Missing command: $dependency"; exit 1; }
done
occupied="$(ss -H -ltnp '( sport = :443 )')"
udp_occupied="$(ss -H -lunp '( sport = :443 )')"
if [[ -n "$occupied$udp_occupied" ]]; then
    echo 'Port 443 is already in use. Existing websites were left running.'
    printf '%s\n%s\n' "$occupied" "$udp_occupied"
    echo 'Share this output so X Feed can be added to the existing web server.'
    exit 1
fi
case "$(uname -m)" in
    x86_64)
        architecture=amd64
        checksum=8220d1f013b6f27510247b2360c9e0ca9f018feebd82515f07635318b34ff9777ccc8fd0b6e6f2486ce3a33fe389fbb7db12d05baa474f4587509fb4f5ebf1c9
        ;;
    aarch64|arm64)
        architecture=arm64
        checksum=d5a7c423853c24a799765e0e8210d5c7c22a8f56ed37a3cae2fb9f58be138853c02b4efd6b59d576e6d8c7c0d30b9c1592deeaa6a536ff69bcca23b8c1ea709c
        ;;
    *) echo 'Supported VPS architectures: x86_64 and arm64.'; exit 1 ;;
esac
stage="$(mktemp -d /tmp/x-feed-web.XXXXXXXX)"
echo "Preparing HTTPS configuration in $stage"
archive="caddy_2.11.4_linux_${architecture}.tar.gz"
curl --fail --location --retry 3 --connect-timeout 20 --max-time 300 \
    "https://github.com/caddyserver/caddy/releases/download/v2.11.4/$archive" -o "$stage/$archive"
printf '%s  %s\n' "$checksum" "$stage/$archive" | sha512sum --check --status
tar -xzf "$stage/$archive" -C "$stage" caddy
echo 'Choose the website password for username xfeed (12–72 ASCII characters).'
IFS= read -r -s -p 'Website password: ' website_password </dev/tty
printf '\n'
IFS= read -r -s -p 'Repeat password: ' website_repeat </dev/tty
printf '\n'
export LC_ALL=C
if [[ "$website_password" != "$website_repeat" || ${#website_password} -lt 12 || ${#website_password} -gt 72 || "$website_password" == *[!\ -~]* ]]; then
    unset website_password website_repeat
    echo 'Passwords must match and contain 12–72 ASCII characters. Rerun to try again.'
    exit 1
fi
password_hash="$(printf '%s\n' "$website_password" | "$stage/caddy" hash-password)"
unset website_password website_repeat
proxy_token="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
template="$(cat "$source_dir/deploy/Caddyfile.template")"
template="${template//__HOST__/$site_host}"
template="${template//__PASSWORD_HASH__/$password_hash}"
template="${template//__PROXY_TOKEN__/$proxy_token}"
printf '%s\n' "$template" > "$stage/Caddyfile"
"$stage/caddy" validate --config "$stage/Caddyfile" --adapter caddyfile

if ! id xfeedweb >/dev/null 2>&1; then
    useradd --system --user-group --home-dir /var/lib/x-feed-web --shell /usr/sbin/nologin xfeedweb
fi
install -d -o root -g root -m 755 /opt/x-feed-web
install -d -o root -g xfeedweb -m 750 /etc/x-feed-web
install -d -o xfeedweb -g xfeedweb -m 700 /var/lib/x-feed-web
install -o root -g root -m 755 "$stage/caddy" /opt/x-feed-web/caddy
install -o root -g xfeedweb -m 640 "$stage/Caddyfile" /etc/x-feed-web/Caddyfile
printf 'X_FEED_PUBLIC_ORIGIN=https://%s\nX_FEED_PROXY_TOKEN=%s\n' "$site_host" "$proxy_token" > /etc/x-feed-web/dashboard.env
chmod 600 /etc/x-feed-web/dashboard.env
unset proxy_token password_hash template
install -d -m 755 /etc/systemd/system/x-engine-dashboard.service.d
printf '[Service]\nEnvironmentFile=/etc/x-feed-web/dashboard.env\n' > /etc/systemd/system/x-engine-dashboard.service.d/website.conf
chmod 644 /etc/systemd/system/x-engine-dashboard.service.d/website.conf
install -m 644 "$source_dir/x_engine/dashboard.py" /opt/x-engine/x_engine/dashboard.py
install -m 644 "$source_dir/deploy/x-feed-web.service" /etc/systemd/system/x-feed-web.service
systemctl daemon-reload
systemctl restart x-engine-dashboard
systemctl is-active --quiet x-engine-dashboard
if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
    ufw allow 443/tcp
fi
systemctl enable --now x-feed-web
echo 'Checking HTTPS certificate and login protection (up to 60 seconds)...'
for attempt in {1..12}; do
    # No password is sent; success means the trusted HTTPS endpoint demands login.
    result="$(curl --silent --output /dev/null --write-out '%{http_code}' --connect-timeout 2 --max-time 3 \
        --resolve "$site_host:443:127.0.0.1" "https://$site_host/" || true)"
    if [[ "$result" == 401 ]]; then
        echo "Ready: https://$site_host/ — username: xfeed; use the password you just chose."
        exit 0
    fi
    sleep 2
done
echo 'Website service installed, but trusted HTTPS is not confirmed yet.'
echo 'Allow inbound TCP 443 in your VPS provider security group/firewall.'
echo 'Leave port 8765 private. Caddy retries certificate issuance automatically.'
echo 'Check: journalctl -u x-feed-web -n 40 --no-pager'
exit 1
