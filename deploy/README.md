# Run X Feed on Ubuntu 24.04

Use a server running Ubuntu 24.04 with SSH access. Replace VPS_IP below with its address. Server installation and service startup must be verified on your VPS.

The worker and dashboard run as the restricted xengine user, start at boot, and restart after crashes. SQLite and session keys persist under /var/lib/x-engine/data. The dashboard stays private on the VPS loopback interface. Your PC can be turned off after migration; only viewing the dashboard needs a connection from your PC.

## 1. Clone and install application code

In your VPS terminal, install Git if needed, then clone the repository:

```bash
apt-get update
apt-get install -y git
cd /root
git clone https://github.com/0x-normal/x-feed.git x-feed
cd /root/x-feed
bash deploy/prepare-ubuntu.sh
bash deploy/install.sh
```

The x-feed repository is public, so cloning and pulling over HTTPS require no GitHub login on the VPS. Pushing from your PC still requires your GitHub credentials. Never put an access token in a clone URL or repository file.

Preparation installs Python/venv and a dedicated Node 22.23.2 runtime from nodejs.org with checksum verification. It does not replace the VPS's global Node installation. The installer refuses to overwrite an existing /opt/x-engine installation. GitHub contains application code only; your data must be transferred separately.

If installation fails before the services are installed, fix the reported error, pull the latest code, and run `bash deploy/install.sh --resume`. This checks that /opt/x-engine contains this application before continuing and preserves the data directory. The Ubuntu dependency install omits the optional Windows Node runtime because Linux already has its dedicated runtime.

### Future updates

Push your changes from your PC. Then run on the VPS:

```bash
cd /root/x-feed
git pull --ff-only
bash deploy/update.sh
```

The updater validates required package assets before stopping the services, then copies the application code and declared assets (including the SA catalog) into /opt/x-engine, installs dependencies, and restarts them. It waits for the dashboard API to respond and the SA catalog to load before reporting success. It preserves /var/lib/x-engine/data. An error after services are stopped leaves them stopped and prints a diagnostic command; fix that error before rerunning. Back up your data before applying schema-changing updates.

If an earlier SA update caused HTTP 502 with `127.0.0.1:8765: connect: connection refused`, pull the latest code and rerun `bash deploy/update.sh` using the commands above. The earlier updater omitted `x_engine/smart-accounts.csv.gz`; the fix copies it into the installed package. No database reset or session reimport is needed. If startup still fails, inspect the application services with `journalctl -u x-engine-dashboard -u x-engine-worker -n 60 --no-pager`. Restarting only `x-feed-web` cannot repair a stopped dashboard.

## 2. Transfer your existing data securely

The Windows vault cannot simply be copied to Linux because its key uses Windows DPAPI. The transfer command encrypts a consistent database backup plus its key with a passphrase. It preserves targets, posts, following baselines, working sessions, and all suspended-account exclusions.

Once the VPS installation is ready, run locally in PowerShell:

```powershell
.\stop.ps1
.\.venv\Scripts\python.exe -m x_engine.transfer export data/vps-transfer.xengine
scp data/vps-transfer.xengine root@VPS_IP:/root/
```

Alternatively, upload only data/vps-transfer.xengine to /root/ through Termius SFTP. Do not copy the raw Windows vault as a substitute.

Choose a unique passphrase of at least 16 characters at the prompt and retain it privately. Existing output files are never overwritten. Keep local collection stopped after export so the VPS starts from the latest saved baseline. If migration fails and you need local collection again, run start.ps1; create a fresh export before the next cutover.

On the VPS:

```bash
install -o xengine -g xengine -m 600 /root/vps-transfer.xengine /var/lib/x-engine/vps-transfer.xengine
cd /opt/x-engine
sudo -u xengine .venv/bin/python -m x_engine.transfer restore /var/lib/x-engine/vps-transfer.xengine --data-dir /var/lib/x-engine/data
systemctl start x-engine-worker x-engine-dashboard
systemctl status x-engine-worker x-engine-dashboard --no-pager
journalctl -u x-engine-worker -n 30 --no-pager
```

Enter the same passphrase at restore. Restore requires a new data directory and verifies the database and encrypted account records before writing it. The installer enables both services at boot. Do not run the local and VPS collectors simultaneously with the same copied sessions.

### Optional transfer without a passphrase

Add `--no-passphrase` to both the local export command and the VPS restore command to skip the passphrase prompts. This portable file contains the vault key alongside the database; anyone with the file can access its sessions. Transfer it privately through SFTP and do not publish it. The file remains excluded from Git. Existing destination directories and output files are never overwritten in either mode.

## 3. Open your private feed

With the local dashboard stopped, open a PowerShell terminal:

```powershell
ssh -N -L 127.0.0.1:8765:127.0.0.1:8765 root@VPS_IP
```

Then open http://127.0.0.1:8765 in your browser. Keep that terminal connected while viewing. Closing the tunnel only disconnects viewing; the VPS collector continues. Port 8765 does not need to be opened in the VPS firewall.

### Normal website access without an SSH tunnel

On the VPS, after restoring data and starting the dashboard:

```bash
cd /root/x-feed
git pull --ff-only
bash deploy/enable-web.sh 43.106.141.82
```

Use your own public IPv4 address if different. Choose a website password at the hidden prompt (12–72 ASCII characters). The browser username is `xfeed`; this password is separate from SSH and the X accounts. Allow inbound **TCP 443** in your hosting provider's security group. The installer also allows this port if UFW is already active; it does not enable or reset a firewall. Keep port 8765 private.

Once the installer reports `Ready`, open **https://43.106.141.82/** in Chrome and enter the website login. You can bookmark it and close Termius. The website and collector start at boot. If HTTPS is still pending, check the provider's firewall and run:

```bash
journalctl -u x-feed-web -n 40 --no-pager
systemctl restart x-feed-web
```

The installer uses a dedicated, checksum-pinned Caddy 2.11.4 binary, service user, and `x-feed-web.service`. It stops before installation if port 443 is occupied, so existing websites can be integrated separately. It only restarts the dashboard, preserving collector operation and stored data. It does not start the Windows collector. An existing website installation is not overwritten by rerunning the installer.

Caddy requests a trusted Let's Encrypt IP certificate using the `shortlived` profile and renews it automatically. TCP 443 must remain reachable for certificate validation and browser access. Certificate validation uses TLS-ALPN on port 443; HTTP challenges and automatic HTTP redirects are disabled, leaving port 80 available for another app. Always open the full `https://` URL; plain `http://` may lead to that other app. HTTPS certificate issuance must still succeed from your VPS; a local configuration test cannot verify your provider's routing or firewall. [Let's Encrypt IP certificates](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability/), [Caddy TLS configuration](https://caddyserver.com/docs/caddyfile/directives/tls).

All feed pages, exports, and API routes require browser authentication at the proxy. Caddy stores a password hash and attaches a private proxy token to backend requests. The dashboard stays bound to loopback and checks the configured HTTPS origin on mutations. Configuration and the token live in `/etc/x-feed-web`, certificates in `/var/lib/x-feed-web`; none belongs in Git. Future `deploy/update.sh` runs preserve the website configuration and dashboard environment drop-in.

To disable website access while continuing collection:

```bash
systemctl disable --now x-feed-web
```

To change the website password, run `/opt/x-feed-web/caddy hash-password` interactively, replace only the hash on the `xfeed` line in `/etc/x-feed-web/Caddyfile`, validate with `/opt/x-feed-web/caddy validate --config /etc/x-feed-web/Caddyfile --adapter caddyfile`, then restart `x-feed-web`. Do not share the configuration file: it also contains the private proxy token.

## Operations

```bash
systemctl is-enabled x-engine-worker x-engine-dashboard
systemctl is-active x-engine-worker x-engine-dashboard
journalctl -u x-engine-worker -f
systemctl restart x-engine-worker
```

The host service remaining online does not guarantee uninterrupted X access: expired cookies and X rate limits still affect collection. The dashboard shows session health and collection errors.

For an encrypted backup, stop the worker, run the export command as xengine with a new output filename and `--data-dir /var/lib/x-engine/data`, then restart the worker. Copy encrypted backups to another machine and keep their passphrases separately. The data directory contains the Linux vault key; restrict its access and include it in protected backups if backing up the raw directory instead.

## Validation status

The transfer round trip, credential preservation, baseline preservation, exclusions, wrong-passphrase rejection, and overwrite protections were tested locally with synthetic data. Ubuntu service startup, runtime installation, live VPS authentication, and remote restore still require successful SSH access.

Public website tests cover proxy-token enforcement, exact HTTPS origin checks, and authenticated feed access through the real Caddy proxy on a temporary local listener. Set `X_FEED_TEST_CADDY` to a Caddy 2.11.4 executable when running `tests/test_dashboard_web.py` to include this integration test. TLS configuration is validated without requesting a certificate during tests; live issuance and systemd behavior need verification on the VPS.

References: [Node release](https://nodejs.org/en/download/archive/v22.23.2), [systemd services](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html), [SSH forwarding](https://man.openbsd.org/ssh).
