#!/usr/bin/env bash
# FixItBlock v3 — AI Proxmox Maintenance Agent
# Whiptail GUI installer — community scripts style
set -euo pipefail

# ── Colors ──────────────────────────────────────────────────────
YW='\033[33m' BL='\033[36m' RD='\033[01;31m' GN='\033[1;92m'
CL='\033[m'   CM="${GN}✓${CL}" CROSS="${RD}✗${CL}" INFO="${YW}●${CL}"

header_info() {
  clear
  cat <<"EOF"
  ███████╗██╗██╗  ██╗██╗████████╗██████╗ ██╗      ██████╗  ██████╗██╗  ██╗
  ██╔════╝██║╚██╗██╔╝██║╚══██╔══╝██╔══██╗██║     ██╔═══██╗██╔════╝██║ ██╔╝
  █████╗  ██║ ╚███╔╝ ██║   ██║   ██████╔╝██║     ██║   ██║██║     █████╔╝
  ██╔══╝  ██║ ██╔██╗ ██║   ██║   ██╔══██╗██║     ██║   ██║██║     ██╔═██╗
  ██║     ██║██╔╝ ██╗██║   ██║   ██████╔╝███████╗╚██████╔╝╚██████╗██║  ██╗
  ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝   ╚═╝   ╚═════╝ ╚══════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝
EOF
  echo -e "\n${BL}         AI-Powered Proxmox Maintenance Agent v3${CL}\n"
}

msg_info()  { echo -e " ${INFO} ${YW}$1...${CL}"; }
msg_ok()    { echo -e " ${CM} ${GN}$1${CL}"; }
msg_error() { echo -e " ${CROSS} ${RD}$1${CL}"; }

die() { msg_error "$*"; exit 1; }

# ── Checks ──────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Must run as root on the Proxmox host"
command -v pveversion &>/dev/null || die "Must run on a Proxmox VE host"
command -v whiptail   &>/dev/null || apt-get install -y -qq whiptail

header_info

# ── Helper: auto-detect available storage pools ──────────────────
get_storage_list() {
  pvesm status --content rootdir 2>/dev/null | awk 'NR>1 && $2=="active" {print $1, $1}' || echo "local-lvm local-lvm"
}

get_storage_all() {
  pvesm status 2>/dev/null | awk 'NR>1 && $2=="active" {print $1, $1}' || echo "local local"
}

get_bridges() {
  ip link show | awk '/^[0-9]+: vmbr/{gsub(":",""); print $2, $2}' || echo "vmbr0 vmbr0"
}

get_next_ctid() {
  pvesh get /cluster/nextid 2>/dev/null || echo "200"
}

# ── WELCOME ──────────────────────────────────────────────────────
whiptail --title "FixItBlock v3 Installer" \
  --msgbox "Welcome to the FixItBlock AI Proxmox Maintenance Agent installer.\n\nThis will:\n  • Create an LXC container\n  • Install the AI agent\n  • Set up Anthropic Claude + Ollama offline AI\n  • Configure web dashboard with admin login\n  • Harden and secure the container\n\nPress OK to begin." \
  18 62

# ── INSTALL TYPE ─────────────────────────────────────────────────
INSTALL_TYPE=$(whiptail --title "Installation Type" \
  --menu "Choose installation type:" 12 55 3 \
  "default"   "Default Install (recommended)" \
  "advanced"  "Advanced Install (custom settings)" \
  "minimal"   "Minimal Install (no AI, basic only)" \
  3>&1 1>&2 2>&3) || exit 0

# ── CONTAINER ID ─────────────────────────────────────────────────
NEXT_ID=$(get_next_ctid)
CT_ID=$(whiptail --title "Container ID" \
  --inputbox "Enter container ID:" 8 45 "$NEXT_ID" \
  3>&1 1>&2 2>&3) || exit 0

# Validate ID is not taken
while pct status $CT_ID &>/dev/null 2>&1; do
  CT_ID=$(whiptail --title "Container ID" \
    --inputbox "ID $CT_ID is already in use. Enter a different ID:" 8 50 "$((CT_ID+1))" \
    3>&1 1>&2 2>&3) || exit 0
done

# ── HOSTNAME ─────────────────────────────────────────────────────
CT_HOST=$(whiptail --title "Hostname" \
  --inputbox "Container hostname:" 8 45 "fixitblock" \
  3>&1 1>&2 2>&3) || exit 0

# ── STORAGE — auto-detect ─────────────────────────────────────────
STORAGE_LIST=$(get_storage_list)
CT_STG=$(whiptail --title "Storage" \
  --menu "Select storage pool for container disk:" 14 55 6 \
  $STORAGE_LIST \
  3>&1 1>&2 2>&3) || exit 0

# ── RAM ──────────────────────────────────────────────────────────
CT_RAM=$(whiptail --title "Memory" \
  --menu "Select RAM allocation:" 14 50 5 \
  "512"  "512 MB  (minimum)" \
  "1024" "1 GB    (recommended)" \
  "2048" "2 GB    (with Ollama AI)" \
  "4096" "4 GB    (full AI + models)" \
  "8192" "8 GB    (maximum performance)" \
  3>&1 1>&2 2>&3) || exit 0

# ── DISK ─────────────────────────────────────────────────────────
CT_DISK=$(whiptail --title "Disk Size" \
  --menu "Select disk size:" 12 45 4 \
  "4"  "4 GB  (minimum)" \
  "8"  "8 GB  (recommended)" \
  "16" "16 GB (with Ollama models)" \
  "32" "32 GB (maximum)" \
  3>&1 1>&2 2>&3) || exit 0

# ── CPU ──────────────────────────────────────────────────────────
CT_CPU=$(whiptail --title "CPU Cores" \
  --menu "Select CPU cores:" 12 45 4 \
  "1" "1 core  (minimum)" \
  "2" "2 cores (recommended)" \
  "4" "4 cores (with AI workloads)" \
  "8" "8 cores (maximum)" \
  3>&1 1>&2 2>&3) || exit 0

# ── NETWORK BRIDGE — auto-detect ─────────────────────────────────
BRIDGE_LIST=$(get_bridges)
CT_BR=$(whiptail --title "Network Bridge" \
  --menu "Select network bridge:" 12 50 4 \
  $BRIDGE_LIST \
  3>&1 1>&2 2>&3) || exit 0

# ── IP ADDRESS ───────────────────────────────────────────────────
NET_TYPE=$(whiptail --title "Network" \
  --menu "IP address configuration:" 10 50 2 \
  "dhcp"   "DHCP (automatic, recommended)" \
  "static" "Static IP (manual)" \
  3>&1 1>&2 2>&3) || exit 0

CT_NET="ip=dhcp"
CT_IP_DISPLAY="DHCP"
if [[ "$NET_TYPE" == "static" ]]; then
  CT_IP=$(whiptail --title "Static IP" \
    --inputbox "Enter IP address with prefix (e.g. 192.168.1.200/24):" 8 55 "" \
    3>&1 1>&2 2>&3) || exit 0
  CT_GW=$(whiptail --title "Gateway" \
    --inputbox "Enter gateway IP (e.g. 192.168.1.1):" 8 50 "" \
    3>&1 1>&2 2>&3) || exit 0
  CT_NET="ip=${CT_IP},gw=${CT_GW}"
  CT_IP_DISPLAY="$CT_IP"
fi

# ── CONTAINER PASSWORD ───────────────────────────────────────────
CT_PASS=$(whiptail --title "Container Password" \
  --passwordbox "Set container root password:" 8 50 \
  3>&1 1>&2 2>&3) || exit 0

# ── PROXMOX CONNECTION — auto-detect ─────────────────────────────
DEFAULT_IP=$(hostname -I | awk '{print $1}')
DEFAULT_NODE=$(hostname)

PVE_HOST=$(whiptail --title "Proxmox Host" \
  --inputbox "Proxmox host IP:" 8 50 "$DEFAULT_IP" \
  3>&1 1>&2 2>&3) || exit 0

PVE_NODE=$(whiptail --title "Proxmox Node" \
  --inputbox "Proxmox node name:" 8 50 "$DEFAULT_NODE" \
  3>&1 1>&2 2>&3) || exit 0

PVE_USER=$(whiptail --title "Proxmox User" \
  --inputbox "Proxmox API user:" 8 50 "root@pam" \
  3>&1 1>&2 2>&3) || exit 0

# ── AUTH METHOD ──────────────────────────────────────────────────
AUTH_METHOD=$(whiptail --title "Authentication" \
  --menu "Proxmox authentication method:" 10 50 2 \
  "token"    "API Token (recommended)" \
  "password" "Password" \
  3>&1 1>&2 2>&3) || exit 0

PVE_TOKEN_NAME="" PVE_TOKEN_VALUE="" PVE_PASS=""
if [[ "$AUTH_METHOD" == "token" ]]; then
  PVE_TOKEN_NAME=$(whiptail --title "API Token Name" \
    --inputbox "Token name (e.g. fixitblock):" 8 50 "fixitblock" \
    3>&1 1>&2 2>&3) || exit 0
  PVE_TOKEN_VALUE=$(whiptail --title "API Token Value" \
    --passwordbox "Token value:" 8 50 \
    3>&1 1>&2 2>&3) || exit 0
else
  PVE_PASS=$(whiptail --title "Proxmox Password" \
    --passwordbox "Proxmox password:" 8 50 \
    3>&1 1>&2 2>&3) || exit 0
fi

# ── AI PROVIDER ──────────────────────────────────────────────────
if [[ "$INSTALL_TYPE" != "minimal" ]]; then
  AI_PROV=$(whiptail --title "AI Provider" \
    --menu "Select primary AI provider:\n(Ollama offline fallback is always installed)" 15 65 5 \
    "anthropic" "Anthropic Claude (best reasoning, needs API key)" \
    "ollama"    "Ollama only (fully local, no API key needed)" \
    "openai"    "OpenAI GPT-4o (needs API key)" \
    "groq"      "Groq (fast inference, needs API key)" \
    "none"      "No AI (basic monitoring only)" \
    3>&1 1>&2 2>&3) || exit 0

  AI_KEY=""
  AI_MDL=""
  case "$AI_PROV" in
    anthropic)
      AI_KEY=$(whiptail --title "Anthropic API Key" \
        --passwordbox "Enter your Anthropic API key\n(get one at console.anthropic.com):" 10 60 \
        3>&1 1>&2 2>&3) || exit 0
      AI_MDL="claude-3-5-sonnet-20241022" ;;
    openai)
      AI_KEY=$(whiptail --title "OpenAI API Key" \
        --passwordbox "Enter your OpenAI API key:" 8 55 \
        3>&1 1>&2 2>&3) || exit 0
      AI_MDL="gpt-4o" ;;
    groq)
      AI_KEY=$(whiptail --title "Groq API Key" \
        --passwordbox "Enter your Groq API key:" 8 55 \
        3>&1 1>&2 2>&3) || exit 0
      AI_MDL="llama-3.1-70b-versatile" ;;
    ollama) AI_MDL="llama3.2" ;;
    none)   AI_MDL="" ;;
  esac

  # Ollama model selection (always shown since Ollama is always installed)
  OLLAMA_MDL=$(whiptail --title "Ollama Offline Model" \
    --menu "Select primary offline AI model\n(will be pulled automatically):" 15 60 6 \
    "llama3.2"   "Llama 3.2 (recommended, best balance)" \
    "mistral"    "Mistral 7B (fast, good quality)" \
    "codellama"  "CodeLlama (code-focused)" \
    "llama3.1"   "Llama 3.1 8B (latest)" \
    "phi3"       "Phi-3 Mini (lightweight)" \
    "gemma2"     "Gemma 2 (Google)" \
    3>&1 1>&2 2>&3) || exit 0

  # Web search
  if whiptail --title "Web Search" --yesno "Enable AI web search?\n\nAllows the AI to search the internet for Proxmox fixes, scripts, and CVEs.\n(Uses DuckDuckGo — no API key needed)" 12 60; then
    WS_EN="true"
  else
    WS_EN="false"
  fi

  # Auto-execute
  if whiptail --title "Auto-Execute" --yesno "Enable AI auto-execute?\n\nAllows the AI to automatically run LOW-risk fix scripts without manual approval.\n\nMEDIUM and HIGH risk always require approval.\n\n(Recommended: No for first-time users)" 16 65; then
    AUTO_EXEC="true"
  else
    AUTO_EXEC="false"
  fi
else
  AI_PROV="none"; AI_KEY=""; AI_MDL=""; OLLAMA_MDL="llama3.2"; WS_EN="false"; AUTO_EXEC="false"
fi

# ── ADMIN ACCOUNT ────────────────────────────────────────────────
ADM_USER=$(whiptail --title "Admin Username" \
  --inputbox "Create admin username for web dashboard:" 8 50 "admin" \
  3>&1 1>&2 2>&3) || exit 0

# Password with strength check
while true; do
  ADM_PASS=$(whiptail --title "Admin Password" \
    --passwordbox "Create admin password:\n(Min 12 chars, must include uppercase, lowercase, number, symbol)" 10 65 \
    3>&1 1>&2 2>&3) || exit 0
  ADM_PASS2=$(whiptail --title "Confirm Password" \
    --passwordbox "Confirm admin password:" 8 50 \
    3>&1 1>&2 2>&3) || exit 0

  ERR=""
  [[ "$ADM_PASS" != "$ADM_PASS2" ]]         && ERR="Passwords do not match"
  [[ ${#ADM_PASS} -lt 12 ]]                 && ERR="Password too short (minimum 12 characters)"
  [[ ! "$ADM_PASS" =~ [A-Z] ]]              && ERR="Password needs at least one UPPERCASE letter"
  [[ ! "$ADM_PASS" =~ [a-z] ]]              && ERR="Password needs at least one lowercase letter"
  [[ ! "$ADM_PASS" =~ [0-9] ]]              && ERR="Password needs at least one number"
  [[ ! "$ADM_PASS" =~ [^a-zA-Z0-9] ]]      && ERR="Password needs at least one symbol (!@#\$...)"

  [[ -z "$ERR" ]] && break
  whiptail --title "Password Error" --msgbox "❌ $ERR\n\nPlease try again." 10 55
done

# ── WEB PORT ─────────────────────────────────────────────────────
WEB_PORT=$(whiptail --title "Web UI Port" \
  --inputbox "Web dashboard port:" 8 45 "7070" \
  3>&1 1>&2 2>&3) || exit 0

# ── SCAN INTERVAL ────────────────────────────────────────────────
SCAN_INT=$(whiptail --title "Scan Interval" \
  --menu "How often should the agent scan for issues?" 12 55 4 \
  "60"  "Every 1 minute  (aggressive)" \
  "300" "Every 5 minutes (recommended)" \
  "600" "Every 10 minutes (light)" \
  "3600" "Every hour (minimal)" \
  3>&1 1>&2 2>&3) || exit 0

# ── AUTO-FIX ─────────────────────────────────────────────────────
if whiptail --title "Auto-Fix" --yesno "Enable automatic fixing of detected issues?\n\nThe agent will automatically fix safe issues like\ncleaning old logs, vacuuming journals, etc.\n\n(Recommended: Yes)" 14 60; then
  AFX_VAL="true"
else
  AFX_VAL="false"
fi

# ── SUMMARY ──────────────────────────────────────────────────────
whiptail --title "Installation Summary" --yesno \
"Ready to install FixItBlock v3!\n
Container:   CT${CT_ID} '${CT_HOST}'
Resources:   ${CT_CPU} CPU · ${CT_RAM}MB RAM · ${CT_DISK}GB disk
Network:     ${CT_IP_DISPLAY} via ${CT_BR}
Proxmox:     ${PVE_HOST} (${PVE_NODE})
AI Provider: ${AI_PROV}${AI_MDL:+ — ${AI_MDL}}
Offline AI:  Ollama (${OLLAMA_MDL})
Web Search:  ${WS_EN}
Auto-Fix:    ${AFX_VAL}
Admin User:  ${ADM_USER}
Web UI Port: ${WEB_PORT}

Proceed with installation?" \
24 62 || exit 0

# ════════════════════════════════════════════════════════════════
#  INSTALLATION
# ════════════════════════════════════════════════════════════════
header_info

# ── LXC Template ─────────────────────────────────────────────────
msg_info "Checking LXC template"
TMPL="ubuntu-22.04-standard_22.04-1_amd64.tar.zst"
if ! ls /var/lib/vz/template/cache/$TMPL &>/dev/null; then
  msg_info "Downloading Ubuntu 22.04 template"
  pveam update -q
  pveam download local $TMPL || die "Template download failed"
fi
msg_ok "Template ready"

# ── Create Container ─────────────────────────────────────────────
msg_info "Creating LXC container CT${CT_ID}"
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
  --start    1 &>/dev/null
sleep 5
CTIP=$(pct exec ${CT_ID} -- hostname -I 2>/dev/null | awk '{print $1}' || echo "<pending>")
msg_ok "Container CT${CT_ID} created — IP: ${CTIP}"

# ── SSH Keys ─────────────────────────────────────────────────────
msg_info "Generating SSH keypair"
pct exec ${CT_ID} -- bash -c "mkdir -p /root/.ssh && chmod 700 /root/.ssh && ssh-keygen -t ed25519 -f /root/.ssh/fixitblock_id -N '' -q" &>/dev/null
pct exec ${CT_ID} -- cat /root/.ssh/fixitblock_id.pub >> /root/.ssh/authorized_keys 2>/dev/null || true
msg_ok "SSH key configured"

# ── Packages ─────────────────────────────────────────────────────
msg_info "Installing packages (this takes 1-2 minutes)"
pct exec ${CT_ID} -- bash -c "
  DEBIAN_FRONTEND=noninteractive apt-get update -qq 2>/dev/null
  apt-get install -y -qq python3 python3-pip git curl ufw fail2ban 2>/dev/null
" &>/dev/null
msg_ok "System packages installed"

msg_info "Installing Python packages"
pct exec ${CT_ID} -- bash -c "
  pip3 install --quiet --break-system-packages \
    flask flask-cors requests paramiko python-dotenv \
    openai anthropic groq cryptography psutil 2>/dev/null || \
  pip3 install --quiet \
    flask flask-cors requests paramiko python-dotenv \
    openai anthropic groq cryptography psutil 2>/dev/null
" &>/dev/null
msg_ok "Python packages installed"

# ── Agent Files ──────────────────────────────────────────────────
msg_info "Deploying FixItBlock agent"
REPO="https://raw.githubusercontent.com/blockt4875-svg/fixitblock/main"
pct exec ${CT_ID} -- bash -c "mkdir -p /opt/fixitblock/agent/{ai,hardware,security,scripts,static}" &>/dev/null

if curl -fsSL --max-time 8 "${REPO}/README.md" &>/dev/null; then
  pct exec ${CT_ID} -- bash -c "
    cd /opt/fixitblock
    FILES='agent/proxmox_agent.py agent/server.py agent/cli.py agent/static/index.html
           agent/ai/brain.py agent/ai/__init__.py
           agent/hardware/profiler.py agent/hardware/__init__.py
           agent/security/auth.py agent/security/__init__.py'
    for f in \$FILES; do
      curl -fsSL ${REPO}/\$f -o \$f 2>/dev/null
    done
  " &>/dev/null
  msg_ok "Agent files deployed from GitHub"
else
  msg_error "Cannot reach GitHub — check internet connection"
  exit 1
fi

# ── Write Config ─────────────────────────────────────────────────
msg_info "Writing configuration"
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
ANTHROPIC_API_KEY=$([ "${AI_PROV}" == "anthropic" ] && echo "${AI_KEY}" || echo "")
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=${OLLAMA_MDL}
OLLAMA_MODEL2=mistral
OLLAMA_MODEL3=codellama
OPENAI_API_KEY=$([ "${AI_PROV}" == "openai" ] && echo "${AI_KEY}" || echo "")
GROQ_API_KEY=$([ "${AI_PROV}" == "groq" ] && echo "${AI_KEY}" || echo "")
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
chmod 600 /opt/fixitblock/.env" &>/dev/null
msg_ok "Configuration written"

# ── Admin Account ────────────────────────────────────────────────
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
sys.exit(0 if ok else 1)
\" 2>/dev/null
" && msg_ok "Admin account '${ADM_USER}' created" || msg_error "Admin account creation failed"

# ── Systemd Service ──────────────────────────────────────────────
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
systemctl daemon-reload && systemctl enable fixitblock && systemctl start fixitblock" &>/dev/null
msg_ok "Service installed and started"

# ── Security Hardening ───────────────────────────────────────────
msg_info "Applying security hardening"
pct exec ${CT_ID} -- bash -c "
  sed -i 's/PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config 2>/dev/null || true
  ufw --force reset &>/dev/null
  ufw default deny incoming &>/dev/null
  ufw default allow outgoing &>/dev/null
  ufw allow ssh &>/dev/null
  ufw allow ${WEB_PORT}/tcp &>/dev/null
  ufw --force enable &>/dev/null
  systemctl enable fail2ban --now &>/dev/null || true
  systemctl restart sshd &>/dev/null || true
" &>/dev/null
msg_ok "Security hardening applied"

# ── Ollama ───────────────────────────────────────────────────────
msg_info "Installing Ollama offline AI"
pct exec ${CT_ID} -- bash -c "
  curl -fsSL https://ollama.ai/install.sh | sh &>/dev/null || true
  systemctl enable ollama --now &>/dev/null || true
  sleep 3
" &>/dev/null
msg_ok "Ollama installed"

msg_info "Pulling AI models in background (${OLLAMA_MDL} + mistral)"
pct exec ${CT_ID} -- bash -c "
  nohup bash -c 'ollama pull ${OLLAMA_MDL} && ollama pull mistral' &>/var/log/ollama-pull.log &
" &>/dev/null
msg_ok "Model download started (check progress: pct exec ${CT_ID} -- tail -f /var/log/ollama-pull.log)"

# ── Done ─────────────────────────────────────────────────────────
sleep 3
STATUS=$(pct exec ${CT_ID} -- systemctl is-active fixitblock 2>/dev/null || echo "unknown")
FINAL_IP=$(pct exec ${CT_ID} -- hostname -I 2>/dev/null | awk '{print $1}' || echo "${CTIP}")

whiptail --title "✓ Installation Complete!" --msgbox \
"FixItBlock v3 is installed and running!

  Web UI:     http://${FINAL_IP}:${WEB_PORT}
  Username:   ${ADM_USER}
  Container:  CT${CT_ID} (${CT_HOST})
  AI:         ${AI_PROV} + Ollama offline
  Service:    ${STATUS}

Management commands:
  pct exec ${CT_ID} -- journalctl -u fixitblock -f
  pct exec ${CT_ID} -- systemctl restart fixitblock
  pct exec ${CT_ID} -- nano /opt/fixitblock/.env

Open your browser and go to:
http://${FINAL_IP}:${WEB_PORT}" \
26 62

echo ""
msg_ok "FixItBlock v3 installed!"
echo -e " ${BL}➜ Web UI: http://${FINAL_IP}:${WEB_PORT}${CL}"
echo ""
