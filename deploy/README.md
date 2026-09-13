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

The updater stops the services, copies the application code into /opt/x-engine, installs dependencies, and restarts them. It preserves /var/lib/x-engine/data. A failed update leaves services stopped and prints an error; fix that error before rerunning. Back up your data before applying schema-changing updates.

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

Then open http://127.0.0.1:8765 in your browser. Keep that terminal connected while viewing. Closing the tunnel only disconnects viewing; the VPS collector continues. Port 8765 does not need to be opened in the VPS firewall. The current dashboard has no internet-facing login layer, so it should remain behind SSH.

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

References: [Node release](https://nodejs.org/en/download/archive/v22.23.2), [systemd services](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html), [SSH forwarding](https://man.openbsd.org/ssh).
