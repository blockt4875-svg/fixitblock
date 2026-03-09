#!/usr/bin/env bash
# ╔══════════════════════════════════════════════════════════════╗
# ║   FixItBlock v3 — AI-Powered Proxmox Maintenance Agent      ║
# ╚══════════════════════════════════════════════════════════════╝
set -euo pipefail
R='\033[0;31m' O='\033[0;33m' G='\033[0;32m' C='\033[0;36m' W='\033[1;37m' N='\033[0m'
die(){ echo -e "${R}[ERROR] $*${N}" >&2; exit 1; }
info(){ echo -e "${C}[INFO]  $*${N}"; }
ok()  { echo -e "${G}[OK]    $*${N}"; }
warn(){ echo -e "${O}[WARN]  $*${N}"; }
ask() { echo -e "${W}[INPUT] $*${N}"; }

echo -e "${O}"
echo '  ███████╗██╗██╗  ██╗██╗████████╗██████╗ ██╗      ██████╗  ██████╗██╗  ██╗'
echo '  ██╔════╝██║╚██╗██╔╝██║╚══██╔══╝██╔══██╗██║     ██╔═══██╗██╔════╝██║ ██╔╝'
echo '  █████╗  ██║ ╚███╔╝ ██║   ██║   ██████╔╝██║     ██║   ██║██║     █████╔╝ '
echo '  ██╔══╝  ██║ ██╔██╗ ██║   ██║   ██╔══██╗██║     ██║   ██║██║     ██╔═██╗ '
echo '  ██║     ██║██╔╝ ██╗██║   ██║   ██████╔╝███████╗╚██████╔╝╚██████╗██║  ██╗'
echo '  ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝   ╚═╝   ╚═════╝ ╚══════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝'
echo -e "${C}                  AI-Powered Proxmox Maintenance Agent v3${N}"
echo ""

[[ $EUID -eq 0 ]] || die "Must run as root on the Proxmox host"
command -v pveversion &>/dev/null || die "Must run on a Proxmox VE host"

echo -e "${W}─── CONTAINER ─────────────────────────────────────────────────${N}"
ask "Container ID [200]:"; read -r CT_ID; CT_ID=${CT_ID:-200}
ask "Hostname [fixitblock]:"; read -r CT_HOST; CT_HOST=${CT_HOST:-fixitblock}
ask "Storage [local-lvm]:"; read -r CT_STG; CT_STG=${CT_STG:-local-lvm}
ask "RAM MB [512]:"; read -r CT_RAM; CT_RAM=${CT_RAM:-512}
ask "Disk GB [4]:"; read -r CT_DISK; CT_DISK=${CT_DISK:-4}
ask "CPU cores [1]:"; read -r CT_CPU; CT_CPU=${CT_CPU:-1}
ask "Network bridge [vmbr0]:"; read -r CT_BR; CT_BR=${CT_BR:-vmbr0}
ask "IP (x.x.x.x/24) or dhcp [dhcp]:"; read -r CT_IP; CT_IP=${CT_IP:-dhcp}
if [[ "$CT_IP" == "dhcp" ]]; then CT_NET="ip=dhcp"
else ask "Gateway:"; read -r CT_GW; CT_NET="ip=${CT_IP},gw=${CT_GW}"; fi
ask "Container root password:"; read -rs CT_PASS; echo
[[ -z "$CT_PASS" ]] && die "Password required"

echo -e "${W}─── PROXMOX ────────────────────────────────────────────────────${N}"
DEFAULT_IP=$(hostname -I | awk '{print $1}')
ask "Proxmox host IP [${DEFAULT_IP}]:"; read -r PVE_HOST; PVE_HOST=${PVE_HOST:-$DEFAULT_IP}
ask "Node name [$(hostname)]:"; read -r PVE_NODE; PVE_NODE=${PVE_NODE:-$(hostname)}
ask "API user [root@pam]:"; read -r PVE_USER; PVE_USER=${PVE_USER:-root@pam}
echo "Auth: 1=API Token  2=Password"; ask "Choice [1]:"; read -r AUTH; AUTH=${AUTH:-1}
PVE_TOKEN_NAME="" PVE_TOKEN_VALUE="" PVE_PASS=""
if [[ "$AUTH" == "1" ]]; then
  ask "Token name:"; read -r PVE_TOKEN_NAME
  ask "Token value:"; read -rs PVE_TOKEN_VALUE; echo
else
  ask "Proxmox password:"; read -rs PVE_PASS; echo
fi

echo -e "${W}─── AI ─────────────────────────────────────────────────────────${N}"
echo "  1=Ollama(local,free)  2=OpenAI  3=Anthropic  4=Groq  5=Skip"
ask "Choice [1]:"; read -r AIC; AIC=${AIC:-1}
AI_PROV="ollama" AI_KEY="" AI_MDL="llama3"
case "$AIC" in
  2) AI_PROV="openai";    ask "OpenAI key:";    read -rs AI_KEY; echo; AI_MDL="gpt-4o" ;;
  3) AI_PROV="anthropic"; ask "Anthropic key:"; read -rs AI_KEY; echo; AI_MDL="claude-3-5-sonnet-20241022" ;;
  4) AI_PROV="groq";      ask "Groq key:";      read -rs AI_KEY; echo; AI_MDL="llama-3.1-70b-versatile" ;;
  5) AI_PROV="none"; AI_MDL="" ;;
esac
ask "Enable AI web search? [Y/n]:"; read -r WS; WS=${WS:-Y}
WS_EN="true"; [[ "$WS" =~ ^[Nn] ]] && WS_EN="false"
ask "Brave Search API key (optional, blank=DuckDuckGo):"; read -rs BRAVE; echo
ask "SerpAPI key (optional):"; read -rs SERP; echo

echo -e "${W}─── ADMIN ACCOUNT ──────────────────────────────────────────────${N}"
ask "Admin username [admin]:"; read -r ADM_USER; ADM_USER=${ADM_USER:-admin}
while true; do
  ask "Admin password (12+ chars, upper+lower+number+symbol):"; read -rs ADM_PASS; echo
  ask "Confirm:"; read -rs ADM_PASS2; echo
  [[ "$ADM_PASS" == "$ADM_PASS2" ]] || { warn "No match"; continue; }
  [[ ${#ADM_PASS} -ge 12 ]] || { warn "Too short"; continue; }
  [[ "$ADM_PASS" =~ [A-Z] ]] || { warn "Need uppercase"; continue; }
  [[ "$ADM_PASS" =~ [a-z] ]] || { warn "Need lowercase"; continue; }
  [[ "$ADM_PASS" =~ [0-9] ]] || { warn "Need number"; continue; }
  [[ "$ADM_PASS" =~ [^a-zA-Z0-9] ]] || { warn "Need symbol"; continue; }
  break
done

echo -e "${W}─── AGENT SETTINGS ─────────────────────────────────────────────${N}"
ask "Web UI port [7070]:"; read -r PORT; PORT=${PORT:-7070}
ask "Scan interval seconds [300]:"; read -r SINT; SINT=${SINT:-300}
ask "Enable auto-fix? [y/N]:"; read -r AFX; AFX=${AFX:-N}
AFX_VAL="false"; [[ "$AFX" =~ ^[Yy] ]] && AFX_VAL="true"

echo ""
echo -e "${G}Creating CT${CT_ID} '${CT_HOST}' — ${CT_CPU}CPU ${CT_RAM}MB ${CT_DISK}GB${N}"
ask "Proceed? [Y/n]:"; read -r GO; GO=${GO:-Y}
[[ "$GO" =~ ^[Nn] ]] && exit 0

# Template
info "Checking LXC template..."
TMPL="ubuntu-22.04-standard_22.04-1_amd64.tar.zst"
if ! ls /var/lib/vz/template/cache/$TMPL &>/dev/null; then
  info "Downloading Ubuntu 22.04..."; pveam update -q; pveam download local $TMPL
fi

# Create container
info "Creating container..."
pct create ${CT_ID} local:vztmpl/${TMPL} \
  --hostname "${CT_HOST}" --storage "${CT_STG}" --rootfs "${CT_STG}:${CT_DISK}" \
  --memory "${CT_RAM}" --cores "${CT_CPU}" \
  --net0 "name=eth0,bridge=${CT_BR},firewall=1,${CT_NET}" \
  --password "${CT_PASS}" --unprivileged 1 --features nesting=1 --onboot 1 --start 1
sleep 5
CTIP=$(pct exec ${CT_ID} -- hostname -I 2>/dev/null | awk '{print $1}' || echo "<ip>")
ok "Container running — IP: ${CTIP}"

# SSH keys
info "Setting up SSH keys..."
pct exec ${CT_ID} -- bash -c "mkdir -p /root/.ssh && chmod 700 /root/.ssh && ssh-keygen -t ed25519 -f /root/.ssh/fixitblock_id -N '' -q && cat /root/.ssh/fixitblock_id.pub" > /tmp/fib_pub.txt
mkdir -p /root/.ssh && touch /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys
cat /tmp/fib_pub.txt >> /root/.ssh/authorized_keys
ok "SSH key authorized on host"

# Packages
info "Installing packages (this takes 1-2 minutes)..."
pct exec ${CT_ID} -- bash -c "
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  apt-get install -y -qq python3 python3-pip git curl ufw fail2ban 2>/dev/null
  pip3 install --quiet --break-system-packages flask flask-cors requests paramiko python-dotenv openai anthropic groq cryptography psutil 2>/dev/null || \
  pip3 install --quiet flask flask-cors requests paramiko python-dotenv openai anthropic groq cryptography psutil
"
ok "Packages installed"

# Agent files
info "Deploying agent files..."
REPO="https://raw.githubusercontent.com/blockt4875-svg/fixitblock/main"
pct exec ${CT_ID} -- bash -c "mkdir -p /opt/fixitblock/agent/{ai,hardware,security,scripts,static}"

if curl -fsSL --max-time 8 "${REPO}/README.md" &>/dev/null; then
  pct exec ${CT_ID} -- bash -c "
    cd /opt/fixitblock
    for f in agent/proxmox_agent.py agent/server.py agent/cli.py agent/static/index.html \
              agent/ai/brain.py agent/ai/__init__.py \
              agent/hardware/profiler.py agent/hardware/__init__.py \
              agent/security/auth.py agent/security/__init__.py; do
      curl -fsSL ${REPO}/\$f -o \$f 2>/dev/null && echo \"  pulled: \$f\"
    done
  "
else
  warn "Cannot reach GitHub — push files manually to /opt/fixitblock/ in the container"
fi
ok "Agent deployed"

# Config
info "Writing .env config..."
pct exec ${CT_ID} -- bash -c "cat > /opt/fixitblock/.env << ENVEOF
PROXMOX_HOST=${PVE_HOST}
PROXMOX_PORT=8006
PROXMOX_USER=${PVE_USER}
PROXMOX_NODE=${PVE_NODE}
PROXMOX_TOKEN_NAME=${PVE_TOKEN_NAME}
PROXMOX_TOKEN_VALUE=${PVE_TOKEN_VALUE}
PROXMOX_PASSWORD=${PVE_PASS}
SSH_HOST=${PVE_HOST}
SSH_PORT=22
SSH_USER=root
SSH_KEY_PATH=/root/.ssh/fixitblock_id
AI_PROVIDER=${AI_PROV}
# Cascade: try Anthropic first, fall back to Ollama offline if unavailable
AI_CASCADE=anthropic,ollama
# Anthropic (primary - best reasoning)
ANTHROPIC_API_KEY=$([ "${AI_PROV}" == "anthropic" ] && echo "${AI_KEY}" || echo "")
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
# Ollama (offline fallback - no internet needed)
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=${AI_MDL:-llama3.2}
OLLAMA_MODEL2=mistral
OLLAMA_MODEL3=codellama
# Other providers (optional)
OPENAI_API_KEY=$([ "${AI_PROV}" == "openai" ] && echo "${AI_KEY}" || echo "")
GROQ_API_KEY=$([ "${AI_PROV}" == "groq" ] && echo "${AI_KEY}" || echo "")
# AI behaviour
AI_WEB_SEARCH=${WS_EN}
AI_AUTO_EXECUTE=false
AI_SELF_HEAL=true
AI_COT=true
AI_MAX_TOKENS=8192
AI_REQUIRE_APPROVAL=true
AI_MAX_AUTO_RISK=low
# Search APIs (optional - DuckDuckGo used if blank)
BRAVE_SEARCH_KEY=${BRAVE}
SERPAPI_KEY=${SERP}
WEB_PORT=${PORT}
SCAN_INTERVAL=${SINT}
AUTO_FIX=${AFX_VAL}
DISK_WARN_PCT=75
DISK_CRIT_PCT=90
MEM_WARN_PCT=80
MEM_CRIT_PCT=95
LOG_MAX_DAYS=14
SNAP_MAX_DAYS=30
BACKUP_MAX_DAYS=7
SESSION_TTL_HOURS=8
MAX_LOGIN_ATTEMPTS=5
LOCKOUT_MINUTES=15
ENVEOF
chmod 600 /opt/fixitblock/.env"
ok "Config written"

# Admin account
info "Creating admin account..."
pct exec ${CT_ID} -- bash -c "
  cd /opt/fixitblock
  python3 -c \"
import sys, os
sys.path.insert(0,'agent')
os.chdir('/opt/fixitblock')
from dotenv import load_dotenv; load_dotenv('.env')
from security.auth import credentials
ok,msg = credentials.create_user('${ADM_USER}','${ADM_PASS}','admin')
print(msg); sys.exit(0 if ok else 1)
\"
"
ok "Admin '${ADM_USER}' created"

# Systemd
info "Installing service..."
pct exec ${CT_ID} -- bash -c "
cat > /etc/systemd/system/fixitblock.service << SVC
[Unit]
Description=FixItBlock AI Proxmox Maintenance Agent
After=network.target
[Service]
Type=simple
User=root
WorkingDirectory=/opt/fixitblock
EnvironmentFile=/opt/fixitblock/.env
ExecStart=/usr/bin/python3 agent/server.py
Restart=on-failure
RestartSec=10
StandardOutput=append:/var/log/fixitblock.log
StandardError=append:/var/log/fixitblock.log
[Install]
WantedBy=multi-user.target
SVC
systemctl daemon-reload && systemctl enable fixitblock && systemctl start fixitblock
"
ok "Service installed"

# Harden
info "Hardening container..."
pct exec ${CT_ID} -- bash -c "
  sed -i 's/PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
  ufw --force reset && ufw default deny incoming && ufw default allow outgoing
  ufw allow ssh && ufw allow ${PORT}/tcp && ufw --force enable
  systemctl enable fail2ban --now 2>/dev/null || true
  systemctl restart sshd 2>/dev/null || true
"
ok "Hardened"


# Always install Ollama as offline AI fallback (regardless of primary provider)
info "Installing Ollama offline AI (fallback when internet is unavailable)..."
pct exec ${CT_ID} -- bash -c "curl -fsSL https://ollama.ai/install.sh | sh 2>/dev/null || true; systemctl enable ollama --now 2>/dev/null || true; sleep 3" || warn "Ollama install failed"

# Pull models in background - llama3.2 + mistral always, codellama if ollama is primary
PULL_MODELS="llama3.2 mistral"
[[ "$AI_PROV" == "ollama" ]] && PULL_MODELS="llama3.2 mistral codellama"
info "Pulling Ollama models in background: $PULL_MODELS (takes several minutes)"
pct exec ${CT_ID} -- bash -c "for M in $PULL_MODELS; do ollama pull $M &; done" &

sleep 3
STATUS=$(pct exec ${CT_ID} -- systemctl is-active fixitblock 2>/dev/null || echo "unknown")

echo ""
echo -e "${W}╔═══════════════════════════════════════════════════════╗${N}"
echo -e "${W}║${G}    FixItBlock v3 Installation Complete! ✓            ${W}║${N}"
echo -e "${W}╚═══════════════════════════════════════════════════════╝${N}"
echo ""
echo -e "  ${W}Web UI:${N}    ${C}http://${CTIP}:${PORT}${N}"
echo -e "  ${W}Login:${N}     ${C}${ADM_USER}${N} / (your password)"
echo -e "  ${W}AI:${N}        ${C}${AI_PROV}${AI_MDL:+ — ${AI_MDL}}${N}"
echo -e "  ${W}Service:${N}   ${C}${STATUS}${N}"
echo ""
echo -e "  ${C}pct exec ${CT_ID} -- journalctl -u fixitblock -f${N}    # logs"
echo -e "  ${C}pct exec ${CT_ID} -- nano /opt/fixitblock/.env${N}      # config"
echo ""
