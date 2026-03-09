#!/usr/bin/env bash
# FixItBlock v3 — AI Proxmox Maintenance Agent

# Force proper TTY for Proxmox web console
exec </dev/tty
exec >/dev/tty 2>/dev/tty

YW='\033[33m' BL='\033[36m' RD='\033[01;31m' GN='\033[1;92m' CL='\033[m'

msg_info()  { echo -e " ${YW}[...] $1${CL}"; }
msg_ok()    { echo -e " ${GN}[OK]  $1${CL}"; }
msg_error() { echo -e " ${RD}[ERR] $1${CL}"; }
die()       { msg_error "$*"; exit 1; }

# Checks
[[ $EUID -eq 0 ]] || die "Must run as root on the Proxmox host"
command -v pveversion &>/dev/null || die "Must run on a Proxmox VE host"

# Install whiptail if missing
if ! command -v whiptail &>/dev/null; then
  msg_info "Installing whiptail"
  apt-get install -y -qq whiptail
  msg_ok "whiptail installed"
fi

clear
echo -e "${YW}"
cat << "BANNER"
  ███████╗██╗██╗  ██╗██╗████████╗██████╗ ██╗      ██████╗  ██████╗██╗  ██╗
  ██╔════╝██║╚██╗██╔╝██║╚══██╔══╝██╔══██╗██║     ██╔═══██╗██╔════╝██║ ██╔╝
  █████╗  ██║ ╚███╔╝ ██║   ██║   ██████╔╝██║     ██║   ██║██║     █████╔╝
  ██╔══╝  ██║ ██╔██╗ ██║   ██║   ██╔══██╗██║     ██║   ██║██║     ██╔═██╗
  ██║     ██║██╔╝ ██╗██║   ██║   ██████╔╝███████╗╚██████╔╝╚██████╗██║  ██╗
  ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝   ╚═╝   ╚═════╝ ╚══════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝
BANNER
echo -e "${BL}              AI-Powered Proxmox Maintenance Agent v3${CL}"
echo ""

# Helper functions
get_storage_list() {
  pvesm status --content rootdir 2>/dev/null \
    | awk 'NR>1 && $2=="active" {print $1, $1}' \
    || echo "local-lvm local-lvm"
}

get_bridges() {
  ip link show 2>/dev/null \
    | awk '/^[0-9]+: vmbr/{gsub(":",""); print $2, $2}' \
    || echo "vmbr0 vmbr0"
}

get_next_ctid() {
  pvesh get /cluster/nextid 2>/dev/null || echo "200"
}

# Whiptail wrapper — works in Proxmox console
WT() { whiptail "$@" 3>&1 1>&2 2>&3; }

#─── WELCOME ───────────────────────────────────────────────────
WT --title "FixItBlock v3" --msgbox \
"Welcome to FixItBlock AI Proxmox Maintenance Agent!

This installer will:
  • Create an LXC container automatically
  • Install and configure the AI agent
  • Set up Anthropic Claude + Ollama offline AI
  • Create your admin login for the web dashboard
  • Harden and secure everything

Press OK to begin." 18 60

#─── INSTALL TYPE ──────────────────────────────────────────────
INSTALL_TYPE=$(WT --title "Installation Type" \
  --menu "Choose installation type:" 12 55 3 \
  "default"  "Default Install (recommended)" \
  "advanced" "Advanced Install (custom settings)" \
  "minimal"  "Minimal (no AI, basic monitoring only)") || exit 0

#─── CONTAINER ID ──────────────────────────────────────────────
NEXT_ID=$(get_next_ctid)
CT_ID=$(WT --title "Container ID" \
  --inputbox "Enter container ID:" 8 45 "$NEXT_ID") || exit 0

#─── HOSTNAME ──────────────────────────────────────────────────
CT_HOST=$(WT --title "Hostname" \
  --inputbox "Container hostname:" 8 45 "fixitblock") || exit 0

#─── STORAGE ───────────────────────────────────────────────────
STORAGE_LIST=$(get_storage_list)
CT_STG=$(WT --title "Storage" \
  --menu "Select storage pool:" 14 55 6 \
  $STORAGE_LIST) || exit 0

#─── RAM ───────────────────────────────────────────────────────
CT_RAM=$(WT --title "Memory" \
  --menu "Select RAM:" 14 50 5 \
  "512"  "512 MB  (minimum)" \
  "1024" "1 GB    (recommended)" \
  "2048" "2 GB    (with Ollama AI)" \
  "4096" "4 GB    (full AI + models)" \
  "8192" "8 GB    (maximum)") || exit 0

#─── DISK ──────────────────────────────────────────────────────
CT_DISK=$(WT --title "Disk Size" \
  --menu "Select disk size:" 12 45 4 \
  "4"  "4 GB  (minimum)" \
  "8"  "8 GB  (recommended)" \
  "16" "16 GB (with AI models)" \
  "32" "32 GB (maximum)") || exit 0

#─── CPU ───────────────────────────────────────────────────────
CT_CPU=$(WT --title "CPU Cores" \
  --menu "Select CPU cores:" 12 45 4 \
  "1" "1 core  (minimum)" \
  "2" "2 cores (recommended)" \
  "4" "4 cores (AI workloads)" \
  "8" "8 cores (maximum)") || exit 0

#─── NETWORK BRIDGE ────────────────────────────────────────────
BRIDGE_LIST=$(get_bridges)
CT_BR=$(WT --title "Network Bridge" \
  --menu "Select network bridge:" 12 50 4 \
  $BRIDGE_LIST) || exit 0

#─── IP ADDRESS ────────────────────────────────────────────────
NET_TYPE=$(WT --title "Network" \
  --menu "IP address type:" 10 50 2 \
  "dhcp"   "DHCP (automatic - recommended)" \
  "static" "Static IP (manual)") || exit 0

CT_NET="ip=dhcp"
CT_IP_DISPLAY="DHCP"
if [[ "$NET_TYPE" == "static" ]]; then
  CT_IP=$(WT --title "Static IP" \
    --inputbox "IP with prefix (e.g. 192.168.1.200/24):" 8 55 "") || exit 0
  CT_GW=$(WT --title "Gateway" \
    --inputbox "Gateway IP (e.g. 192.168.1.1):" 8 50 "") || exit 0
  CT_NET="ip=${CT_IP},gw=${CT_GW}"
  CT_IP_DISPLAY="$CT_IP"
fi

#─── CONTAINER PASSWORD ────────────────────────────────────────
CT_PASS=$(WT --title "Container Password" \
  --passwordbox "Set container root password:" 8 50) || exit 0

#─── PROXMOX CONNECTION ────────────────────────────────────────
DEFAULT_IP=$(hostname -I | awk '{print $1}')
DEFAULT_NODE=$(hostname)

PVE_HOST=$(WT --title "Proxmox Host IP" \
  --inputbox "Proxmox host IP:" 8 50 "$DEFAULT_IP") || exit 0

PVE_NODE=$(WT --title "Proxmox Node Name" \
  --inputbox "Proxmox node name:" 8 50 "$DEFAULT_NODE") || exit 0

PVE_USER=$(WT --title "Proxmox User" \
  --inputbox "Proxmox API user:" 8 50 "root@pam") || exit 0

#─── AUTH ──────────────────────────────────────────────────────
AUTH_METHOD=$(WT --title "Authentication" \
  --menu "Authentication method:" 10 50 2 \
  "token"    "API Token (recommended)" \
  "password" "Password") || exit 0

PVE_TOKEN_NAME="" PVE_TOKEN_VALUE="" PVE_PASS=""
if [[ "$AUTH_METHOD" == "token" ]]; then
  PVE_TOKEN_NAME=$(WT --title "Token Name" \
    --inputbox "Token name:" 8 50 "fixitblock") || exit 0
  PVE_TOKEN_VALUE=$(WT --title "Token Value" \
    --passwordbox "Token value:" 8 50) || exit 0
else
  PVE_PASS=$(WT --title "Proxmox Password" \
    --passwordbox "Proxmox root password:" 8 50) || exit 0
fi

#─── AI PROVIDER ───────────────────────────────────────────────
AI_PROV="none" AI_KEY="" AI_MDL="" OLLAMA_MDL="llama3.2" WS_EN="false" AUTO_EXEC="false"

if [[ "$INSTALL_TYPE" != "minimal" ]]; then

  AI_PROV=$(WT --title "AI Provider" \
    --menu "Select primary AI provider:" 15 65 5 \
    "anthropic" "Anthropic Claude (best — needs API key)" \
    "ollama"    "Ollama only (free, fully local)" \
    "openai"    "OpenAI GPT-4o (needs API key)" \
    "groq"      "Groq (fast — needs API key)" \
    "none"      "No AI") || exit 0

  case "$AI_PROV" in
    anthropic)
      AI_KEY=$(WT --title "Anthropic API Key" \
        --passwordbox "Anthropic API key\n(get one at console.anthropic.com):" 10 60) || exit 0
      AI_MDL="claude-3-5-sonnet-20241022" ;;
    openai)
      AI_KEY=$(WT --title "OpenAI API Key" \
        --passwordbox "OpenAI API key:" 8 55) || exit 0
      AI_MDL="gpt-4o" ;;
    groq)
      AI_KEY=$(WT --title "Groq API Key" \
        --passwordbox "Groq API key:" 8 55) || exit 0
      AI_MDL="llama-3.1-70b-versatile" ;;
    ollama) AI_MDL="llama3.2" ;;
  esac

  OLLAMA_MDL=$(WT --title "Ollama Offline Model" \
    --menu "Select offline AI model:" 15 60 6 \
    "llama3.2"  "Llama 3.2 (recommended)" \
    "mistral"   "Mistral 7B (fast)" \
    "codellama" "CodeLlama (code-focused)" \
    "llama3.1"  "Llama 3.1 8B" \
    "phi3"      "Phi-3 Mini (lightweight)" \
    "gemma2"    "Gemma 2") || exit 0

  if WT --title "Web Search" --yesno \
    "Enable AI web search?\n\nLets AI search the internet for Proxmox fixes and CVEs.\nUses DuckDuckGo — no API key needed." 12 60; then
    WS_EN="true"
  fi

  if WT --title "Auto-Execute" --yesno \
    "Enable AI auto-execute?\n\nAI will auto-run LOW risk fix scripts.\nMEDIUM/HIGH always require your approval.\n\nRecommended: No for first-time users." 14 60; then
    AUTO_EXEC="true"
  fi
fi

#─── ADMIN ACCOUNT ─────────────────────────────────────────────
ADM_USER=$(WT --title "Admin Username" \
  --inputbox "Web dashboard admin username:" 8 50 "admin") || exit 0

while true; do
  ADM_PASS=$(WT --title "Admin Password" \
    --passwordbox "Admin password (12+ chars, upper+lower+number+symbol):" 10 60) || exit 0
  ADM_PASS2=$(WT --title "Confirm Password" \
    --passwordbox "Confirm password:" 8 50) || exit 0

  ERR=""
  [[ "$ADM_PASS" != "$ADM_PASS2" ]]    && ERR="Passwords do not match"
  [[ ${#ADM_PASS} -lt 12 ]]            && ERR="Too short — minimum 12 characters"
  [[ ! "$ADM_PASS" =~ [A-Z] ]]         && ERR="Need at least one UPPERCASE letter"
  [[ ! "$ADM_PASS" =~ [a-z] ]]         && ERR="Need at least one lowercase letter"
  [[ ! "$ADM_PASS" =~ [0-9] ]]         && ERR="Need at least one number"
  [[ ! "$ADM_PASS" =~ [^a-zA-Z0-9] ]] && ERR="Need at least one symbol"

  [[ -z "$ERR" ]] && break
  WT --title "Password Error" --msgbox "❌  $ERR\n\nPlease try again." 10 50
done

#─── WEB PORT ──────────────────────────────────────────────────
WEB_PORT=$(WT --title "Web UI Port" \
  --inputbox "Web dashboard port:" 8 45 "7070") || exit 0

#─── SCAN INTERVAL ─────────────────────────────────────────────
SCAN_INT=$(WT --title "Scan Interval" \
  --menu "How often to scan for issues:" 12 55 4 \
  "60"   "Every 1 minute" \
  "300"  "Every 5 minutes (recommended)" \
  "600"  "Every 10 minutes" \
  "3600" "Every hour") || exit 0

#─── AUTO-FIX ──────────────────────────────────────────────────
AFX_VAL="false"
if WT --title "Auto-Fix" --yesno \
  "Enable automatic fixing?\n\nAgent will auto-fix safe issues like\ncleaning logs, vacuuming journals, etc." 12 55; then
  AFX_VAL="true"
fi

#─── SUMMARY ───────────────────────────────────────────────────
WT --title "Ready to Install" --yesno \
"Installation Summary:

  Container:  CT${CT_ID} '${CT_HOST}'
  Resources:  ${CT_CPU} CPU / ${CT_RAM}MB RAM / ${CT_DISK}GB disk
  Network:    ${CT_IP_DISPLAY} via ${CT_BR}
  Proxmox:    ${PVE_HOST} (${PVE_NODE})
  AI:         ${AI_PROV}
  Offline AI: Ollama (${OLLAMA_MDL})
  Web Search: ${WS_EN}
  Auto-Fix:   ${AFX_VAL}
  Admin:      ${ADM_USER}
  Web Port:   ${WEB_PORT}

Proceed with installation?" 26 60 || exit 0

#══════════════════════════════════════════════════════
#  INSTALLATION
#══════════════════════════════════════════════════════
clear
echo -e "${YW}"
cat << "BANNER"
  ███████╗██╗██╗  ██╗██╗████████╗██████╗ ██╗      ██████╗  ██████╗██╗  ██╗
  ██╔════╝██║╚██╗██╔╝██║╚══██╔══╝██╔══██╗██║     ██╔═══██╗██╔════╝██║ ██╔╝
  █████╗  ██║ ╚███╔╝ ██║   ██║   ██████╔╝██║     ██║   ██║██║     █████╔╝
BANNER
echo -e "${BL}  Installing FixItBlock v3...${CL}\n"

#─── LXC Template ──────────────────────────────────────────────
msg_info "Checking LXC template"
TMPL="ubuntu-22.04-standard_22.04-1_amd64.tar.zst"
if ! ls /var/lib/vz/template/cache/$TMPL &>/dev/null; then
  msg_info "Downloading Ubuntu 22.04 template"
  pveam update -q 2>/dev/null
  pveam download local $TMPL 2>/dev/null || die "Template download failed"
fi
msg_ok "Template ready"

#─── Create Container ──────────────────────────────────────────
msg_info "Creating container CT${CT_ID}"
pct create ${CT_ID} local:vztmpl/${TMPL} \
  --hostname "${CT_HOST}" \
  --storage  "${CT_STG}" \
  --rootfs   "${CT_STG}:${CT_DISK}" \
  --memory   "${CT_RAM}" \
  --cores    "${CT_CPU}" \
  --net0     "name=eth0,bridge=${CT_BR},firewall=1,${CT_NET}" \
  --password "${CT_PASS}" \
  --unprivileged 1 \
  --features nesting=1 \
  --onboot   1 \
  --start    1 2>/dev/null
sleep 6
CTIP=$(pct exec ${CT_ID} -- hostname -I 2>/dev/null | awk '{print $1}' || echo "pending")
msg_ok "Container CT${CT_ID} created — IP: ${CTIP}"

#─── SSH Keys ──────────────────────────────────────────────────
msg_info "Setting up SSH keys"
pct exec ${CT_ID} -- bash -c "
  mkdir -p /root/.ssh && chmod 700 /root/.ssh
  ssh-keygen -t ed25519 -f /root/.ssh/fixitblock_id -N '' -q
" 2>/dev/null
pct exec ${CT_ID} -- cat /root/.ssh/fixitblock_id.pub >> /root/.ssh/authorized_keys 2>/dev/null || true
msg_ok "SSH keys configured"

#─── Packages ──────────────────────────────────────────────────
msg_info "Installing system packages (1-2 mins)"
pct exec ${CT_ID} -- bash -c "
  DEBIAN_FRONTEND=noninteractive apt-get update -qq 2>/dev/null
  apt-get install -y -qq python3 python3-pip git curl ufw fail2ban 2>/dev/null
" 2>/dev/null
msg_ok "System packages installed"

msg_info "Installing Python packages"
pct exec ${CT_ID} -- bash -c "
  pip3 install --quiet --break-system-packages \
    flask flask-cors requests paramiko python-dotenv \
    openai anthropic groq cryptography psutil 2>/dev/null || \
  pip3 install --quiet \
    flask flask-cors requests paramiko python-dotenv \
    openai anthropic groq cryptography psutil 2>/dev/null
" 2>/dev/null
msg_ok "Python packages installed"

#─── Agent Files ───────────────────────────────────────────────
msg_info "Deploying FixItBlock agent from GitHub"
REPO="https://raw.githubusercontent.com/blockt4875-svg/fixitblock/main"
pct exec ${CT_ID} -- bash -c "
  mkdir -p /opt/fixitblock/agent/{ai,hardware,security,scripts,static}
  cd /opt/fixitblock
  for f in agent/proxmox_agent.py agent/server.py agent/cli.py \
            agent/static/index.html \
            agent/ai/brain.py agent/ai/__init__.py \
            agent/hardware/profiler.py agent/hardware/__init__.py \
            agent/security/auth.py agent/security/__init__.py; do
    curl -fsSL ${REPO}/\$f -o \$f 2>/dev/null && echo \"  pulled \$f\" || echo \"  FAILED \$f\"
  done
" 2>/dev/null
msg_ok "Agent files deployed"

#─── Config ────────────────────────────────────────────────────
msg_info "Writing configuration"
ANTH_KEY=""
OPEN_KEY=""
GROQ_KEY2=""
[[ "$AI_PROV" == "anthropic" ]] && ANTH_KEY="$AI_KEY"
[[ "$AI_PROV" == "openai" ]]    && OPEN_KEY="$AI_KEY"
[[ "$AI_PROV" == "groq" ]]      && GROQ_KEY2="$AI_KEY"

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
AI_CASCADE=anthropic,ollama
ANTHROPIC_API_KEY=${ANTH_KEY}
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=${OLLAMA_MDL}
OLLAMA_MODEL2=mistral
OPENAI_API_KEY=${OPEN_KEY}
GROQ_API_KEY=${GROQ_KEY2}
AI_WEB_SEARCH=${WS_EN}
AI_AUTO_EXECUTE=${AUTO_EXEC}
AI_SELF_HEAL=true
AI_COT=true
AI_MAX_TOKENS=8192
AI_REQUIRE_APPROVAL=true
AI_MAX_AUTO_RISK=low
WEB_PORT=${WEB_PORT}
SCAN_INTERVAL=${SCAN_INT}
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
msg_ok "Configuration written"

#─── Admin Account ─────────────────────────────────────────────
msg_info "Creating admin account"
pct exec ${CT_ID} -- bash -c "
  cd /opt/fixitblock
  python3 -c \"
import sys, os
sys.path.insert(0,'agent')
os.chdir('/opt/fixitblock')
from dotenv import load_dotenv; load_dotenv('.env')
from security.auth import credentials
ok,msg = credentials.create_user('${ADM_USER}','${ADM_PASS}','admin')
print(msg)
sys.exit(0 if ok else 1)
\" 2>/dev/null
" && msg_ok "Admin account '${ADM_USER}' created" || msg_error "Admin creation failed — you can set it up in the web UI"

#─── Systemd ───────────────────────────────────────────────────
msg_info "Installing systemd service"
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
systemctl daemon-reload
systemctl enable fixitblock
systemctl start fixitblock
" 2>/dev/null
msg_ok "Service installed and started"

#─── Security ──────────────────────────────────────────────────
msg_info "Hardening container"
pct exec ${CT_ID} -- bash -c "
  ufw --force reset 2>/dev/null
  ufw default deny incoming 2>/dev/null
  ufw default allow outgoing 2>/dev/null
  ufw allow ssh 2>/dev/null
  ufw allow ${WEB_PORT}/tcp 2>/dev/null
  ufw --force enable 2>/dev/null
  systemctl enable fail2ban --now 2>/dev/null || true
" 2>/dev/null
msg_ok "Container hardened"

#─── Ollama ────────────────────────────────────────────────────
msg_info "Installing Ollama offline AI"
pct exec ${CT_ID} -- bash -c "
  curl -fsSL https://ollama.ai/install.sh | sh 2>/dev/null || true
  systemctl enable ollama --now 2>/dev/null || true
  sleep 3
  nohup bash -c 'ollama pull ${OLLAMA_MDL} 2>/dev/null; ollama pull mistral 2>/dev/null' \
    > /var/log/ollama-pull.log 2>&1 &
" 2>/dev/null
msg_ok "Ollama installed — models downloading in background"

#─── Done ──────────────────────────────────────────────────────
sleep 3
FINAL_IP=$(pct exec ${CT_ID} -- hostname -I 2>/dev/null | awk '{print $1}' || echo "$CTIP")
STATUS=$(pct exec ${CT_ID} -- systemctl is-active fixitblock 2>/dev/null || echo "unknown")

echo ""
echo -e "${GN}╔══════════════════════════════════════════════════╗${CL}"
echo -e "${GN}║       FixItBlock v3 Installation Complete!       ║${CL}"
echo -e "${GN}╚══════════════════════════════════════════════════╝${CL}"
echo ""
echo -e "  ${BL}Web UI:${CL}    http://${FINAL_IP}:${WEB_PORT}"
echo -e "  ${BL}Username:${CL}  ${ADM_USER}"
echo -e "  ${BL}Container:${CL} CT${CT_ID} (${CT_HOST})"
echo -e "  ${BL}Service:${CL}   ${STATUS}"
echo ""
echo -e "  ${YW}Useful commands:${CL}"
echo -e "  pct exec ${CT_ID} -- journalctl -u fixitblock -f"
echo -e "  pct exec ${CT_ID} -- systemctl restart fixitblock"
echo ""

WT --title "✓ Installation Complete!" --msgbox \
"FixItBlock v3 is installed!

  Web UI:    http://${FINAL_IP}:${WEB_PORT}
  Username:  ${ADM_USER}
  Container: CT${CT_ID} (${CT_HOST})
  Service:   ${STATUS}

Open your browser and go to:
http://${FINAL_IP}:${WEB_PORT}" 18 55
