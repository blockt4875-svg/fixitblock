# FixItBlock — Proxmox Maintenance Agent

> Automated issue detection and remediation for Proxmox VE.
> Runs as an LXC container. Connects via API + SSH. Fixes itself.

---

## ⚡ One-Line Install

Run this on your **Proxmox host shell**:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/fixitblock/fixitblock/main/install.sh)
```

The installer will:
1. Prompt for container settings (ID, storage, RAM, network)
2. Prompt for your Proxmox API token or password
3. Download an Ubuntu 22.04 LXC template (if not already present)
4. Create and configure the LXC container
5. Generate an SSH keypair and authorize it on the host
6. Install Python dependencies inside the container
7. Deploy FixItBlock and register it as a systemd service
8. Print your web UI URL

---

## Self-Hosting (No Public Repo Yet)

Place the `agent/` folder next to `install.sh`, then run:

```bash
chmod +x install.sh
./install.sh
```

The installer detects local files and uses `pct push` to copy them into the container automatically.

---

## What It Monitors & Fixes

| Category | Checks | Auto-Fix |
|---|---|---|
| **Disk & Storage** | Pool usage, filesystem fill, unused volumes | ✅ |
| **VMs & Containers** | Stopped VMs/LXC, high CPU/memory, node health | ✅ drop caches |
| **Backups** | Missing backup storage, stale backups | ✅ delete old |
| **Logs** | Oversized files, journal bloat, syslog errors | ✅ truncate/vacuum |
| **Snapshots** | Old VM snapshots past retention window | ✅ delete |

---

## Web UI

Open `http://<container-ip>:7070` in your browser.

- Live issue list with severity badges
- One-click fix per issue, or **Fix All**
- Sidebar filters by category and severity
- Sparkline scan history at the bottom
- Real-time scan progress bar

---

## CLI (inside the container)

```bash
# Enter the container
pct enter <CTID>

# Run commands
python3 /opt/fixitblock/cli.py scan               # scan only
python3 /opt/fixitblock/cli.py scan --fix         # scan + fix all
python3 /opt/fixitblock/cli.py scan --interactive # prompt per fix
python3 /opt/fixitblock/cli.py status             # config + live node stats
python3 /opt/fixitblock/cli.py watch              # live refresh loop
python3 /opt/fixitblock/cli.py history            # scan history table

# Or use the alias (added to .bashrc automatically)
fixitblock scan --fix
```

---

## Configuration

Config lives at `/opt/fixitblock/.env` inside the container.

```env
# Proxmox API
PROXMOX_HOST=192.168.1.100
PROXMOX_PORT=8006
PROXMOX_USER=root@pam
PROXMOX_NODE=pve
PROXMOX_TOKEN_NAME=fixitblock
PROXMOX_TOKEN_VALUE=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# SSH (for host-level commands)
SSH_HOST=192.168.1.100
SSH_USER=root
SSH_KEY_PATH=/root/.ssh/fixitblock_id

# Agent behavior
AUTO_FIX=false
SCAN_INTERVAL=300
WEB_PORT=7070

# Thresholds
DISK_WARN_PCT=75
DISK_CRIT_PCT=90
MEM_WARN_PCT=80
MEM_CRIT_PCT=95
CPU_WARN_PCT=85
LOG_MAX_DAYS=14
SNAP_MAX_DAYS=30
BACKUP_MAX_DAYS=7
```

After editing, restart the service:

```bash
pct exec <CTID> -- systemctl restart fixitblock
```

---

## Creating an API Token in Proxmox

1. Open the Proxmox web UI
2. Go to **Datacenter → Permissions → API Tokens → Add**
3. User: `root@pam`, Token ID: `fixitblock`
4. Uncheck "Privilege Separation" (or assign the permissions below manually)
5. Copy the token value into the installer or `.env`

Minimum permissions needed:

| Permission | Path |
|---|---|
| VM.Audit | / |
| Datastore.Audit | / |
| Datastore.AllocateSpace | / |
| VM.Snapshot.Rollback | / |
| Sys.Audit | / |

---

## Manual Docker Deploy (Alternative)

If you prefer Docker over LXC:

```bash
git clone https://github.com/fixitblock/fixitblock
cd fixitblock
# Edit docker-compose.yml with your credentials
docker compose up -d
open http://localhost:7070
```

---

## Project Structure

```
fixitblock/
├── install.sh                  ← One-line LXC installer (run on Proxmox host)
├── Dockerfile                  ← Docker alternative
├── docker-compose.yml
├── requirements.txt
├── README.md
└── agent/
    ├── proxmox_agent.py        ← Core engine (API, SSH, all checkers)
    ├── server.py               ← Flask web API
    ├── cli.py                  ← Terminal CLI
    └── static/
        └── index.html          ← Web dashboard
```
