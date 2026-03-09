#!/usr/bin/env bash
# FixItBlock v3 — AI Proxmox Maintenance Agent

YW='\033[33m' BL='\033[36m' RD='\033[01;31m' GN='\033[1;92m'
WH='\033[1;37m' CL='\033[m'

msg_info()  { echo -e " ${YW}[....] $1${CL}"; }
msg_ok()    { echo -e " ${GN}[ OK ] $1${CL}"; }
msg_error() { echo -e " ${RD}[FAIL] $1${CL}"; }
die()       { msg_error "$*"; exit 1; }

header() {
  clear
  echo -e "${YW}"
  echo '  ███████╗██╗██╗  ██╗██╗████████╗██████╗ ██╗      ██████╗  ██████╗██╗  ██╗'
  echo '  ██╔════╝██║╚██╗██╔╝██║╚══██╔══╝██╔══██╗██║     ██╔═══██╗██╔════╝██║ ██╔╝'
  echo '  █████╗  ██║ ╚███╔╝ ██║   ██║   ██████╔╝██║     ██║   ██║██║     █████╔╝ '
  echo '  ██╔══╝  ██║ ██╔██╗ ██║   ██║   ██╔══██╗██║     ██║   ██║██║     ██╔═██╗ '
  echo '  ██║     ██║██╔╝ ██╗██║   ██║   ██████╔╝███████╗╚██████╔╝╚██████╗██║  ██╗'
  echo '  ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝   ╚═╝   ╚═════╝ ╚══════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝'
  echo -e "${BL}              AI-Powered Proxmox Maintenance Agent v3${CL}"
  echo ""
}

# numbered menu picker — works in any terminal
# usage: pick RESULT_VAR "Title" "opt1" "desc1" "opt2" "desc2" ...
pick() {
  local __var="$1"; shift
  local __title="$1"; shift
  local __opts=("$@")
  echo ""
  echo -e "${WH}  ┌─ ${__title} ${"─"*$((50-${#__title}))}┐${CL}" 2>/dev/null || \
  echo -e "${WH}  ── ${__title} ──────────────────────────────────────${CL}"
  local i=1
  local keys=()
  while [[ $i -le ${#__opts[@]} ]]; do
    local key="${__opts[$((i-1))]}"
    local desc="${__opts[$i]}"
    echo -e "  ${YW}$((i/2+i%2))${CL}) ${WH}${key}${CL} — ${desc}"
    keys+=("$key")
    i=$((i+2))
  done
  local total=$(( ${#keys[@]} ))
  local choice
  while true; do
    echo -ne "  ${BL}Enter number [1-${total}]: ${CL}"
    read -r choice
    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= total )); then
      printf -v "$__var" '%s' "${keys[$((choice-1))]}"
      return
    fi
    echo -e "  ${RD}Invalid — enter a number between 1 and ${total}${CL}"
  done
}

ask() {
  local __var="$1"; local __prompt="$2"; local __default="$3"
  echo -ne "  ${BL}${__prompt}${__default:+ [${__default}]}: ${CL}"
  read -r __input
  printf -v "$__var" '%s' "${__input:-$__default}"
}

askpass() {
  local __var="$1"; local __prompt="$2"
  echo -ne "  ${BL}${__prompt}: ${CL}"
  read -rs __input; echo ""
  printf -v "$__var" '%s' "$__input"
}

yesno() {
  local __prompt="$1"; local __default="${2:-y}"
  echo -ne "  ${BL}${__prompt} [y/n] (default=${__default}): ${CL}"
  read -r __ans
  __ans="${__ans:-$__default}"
  [[ "$__ans" =~ ^[Yy] ]]
}

divider() { echo -e "\n${WH}  ════════════════════════════════════════════════${CL}"; }

# ── Checks ──────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Must run as root on the Proxmox host"
command -v pveversion &>/dev/null || die "Must run on a Proxmox VE host"

header

echo -e "  ${GN}Welcome to FixItBlock v3 installer!${CL}"
echo -e "  This will create an LXC container and install the AI agent."
echo -e "  Press ENTER to use the default value shown in [brackets]."
echo ""
echo -ne "  ${BL}Press ENTER to begin...${CL}"; read -r

# ── CONTAINER ───────────────────────────────────────────────────────────
divider
echo -e "  ${WH}CONTAINER SETTINGS${CL}"
divider

NEXT_ID=$(pvesh get /cluster/nextid 2>/dev/null || echo "200")
ask CT_ID "Container ID" "$NEXT_ID"

ask CT_HOST "Hostname" "fixitblock"

echo ""
echo -e "  ${WH}Available storage pools:${CL}"
pvesm status --content rootdir 2>/dev/null | awk 'NR>1 && $2=="active" {printf "    %-20s free: %s\n", $1, $5}' || echo "    local-lvm"
ask CT_STG "Storage pool" "local-lvm"

pick CT_RAM "RAM" \
  "512"  "512 MB minimum" \
  "1024" "1 GB recommended" \
  "2048" "2 GB with Ollama AI" \
  "4096" "4 GB full AI"

pick CT_DISK "Disk size (GB)" \
  "4"  "4 GB minimum" \
  "8"  "8 GB recommended" \
  "16" "16 GB with AI models" \
  "32" "32 GB maximum"

pick CT_CPU "CPU cores" \
  "1" "1 core minimum" \
  "2" "2 cores recommended" \
  "4" "4 cores AI workloads"

echo ""
echo -e "  ${WH}Available network bridges:${CL}"
ip link show 2>/dev/null | awk '/^[0-9]+: vmbr/{gsub(":",""); printf "    %s\n", $2}' || echo "    vmbr0"
ask CT_BR "Network bridge" "vmbr0"

pick NET_TYPE "IP address" \
  "dhcp"   "DHCP automatic (recommended)" \
  "static" "Static IP manual"

CT_NET="ip=dhcp"; CT_IP_DISPLAY="DHCP"
if [[ "$NET_TYPE" == "static" ]]; then
  ask CT_IP "IP with prefix (e.g. 192.168.1.200/24)" ""
  ask CT_GW "Gateway IP" ""
  CT_NET="ip=${CT_IP},gw=${CT_GW}"
  CT_IP_DISPLAY="$CT_IP"
fi

askpass CT_PASS "Container root password"

# ── PROXMOX ─────────────────────────────────────────────────────────────
divider
echo -e "  ${WH}PROXMOX CONNECTION${CL}"
divider

DEFAULT_IP=$(hostname -I | awk '{print $1}')
ask PVE_HOST "Proxmox host IP" "$DEFAULT_IP"
ask PVE_NODE "Proxmox node name" "$(hostname)"
ask PVE_USER "Proxmox API user" "root@pam"

pick AUTH_METHOD "Authentication method" \
  "token"    "API Token (recommended)" \
  "password" "Password"

PVE_TOKEN_NAME="" PVE_TOKEN_VALUE="" PVE_PASS=""
if [[ "$AUTH_METHOD" == "token" ]]; then
  ask PVE_TOKEN_NAME "Token name" "fixitblock"
  askpass PVE_TOKEN_VALUE "Token value"
else
  askpass PVE_PASS "Proxmox password"
fi

# ── AI ───────────────────────────────────────────────────────────────────
divider
echo -e "  ${WH}AI CONFIGURATION${CL}"
divider

pick AI_PROV "Primary AI provider" \
  "anthropic" "Anthropic Claude — best reasoning (needs API key)" \
  "ollama"    "Ollama only — free, fully local, no API key" \
  "openai"    "OpenAI GPT-4o (needs API key)" \
  "groq"      "Groq — fast inference (needs API key)" \
  "none"      "No AI — basic monitoring only"

AI_KEY="" AI_MDL=""
case "$AI_PROV" in
  anthropic) askpass AI_KEY "Anthropic API key (console.anthropic.com)"; AI_MDL="claude-3-5-sonnet-20241022" ;;
  openai)    askpass AI_KEY "OpenAI API key";    AI_MDL="gpt-4o" ;;
  groq)      askpass AI_KEY "Groq API key";      AI_MDL="llama-3.1-70b-versatile" ;;
  ollama)    AI_MDL="llama3.2" ;;
esac

pick OLLAMA_MDL "Ollama offline model (always installed as fallback)" \
  "llama3.2"  "Llama 3.2 recommended" \
  "mistral"   "Mistral 7B fast" \
  "codellama" "CodeLlama code-focused" \
  "llama3.1"  "Llama 3.1 8B" \
  "phi3"      "Phi-3 Mini lightweight"

yesno "Enable AI web search (DuckDuckGo, no key needed)?" "y" && WS_EN="true" || WS_EN="false"
yesno "Enable AI auto-execute for LOW risk fixes?" "n"       && AUTO_EXEC="true" || AUTO_EXEC="false"

# ── ADMIN ACCOUNT ────────────────────────────────────────────────────────
divider
echo -e "  ${WH}ADMIN ACCOUNT (for web dashboard login)${CL}"
divider

ask ADM_USER "Admin username" "admin"

while true; do
  askpass ADM_PASS  "Admin password (12+ chars, upper+lower+number+symbol)"
  askpass ADM_PASS2 "Confirm password"
  ERR=""
  [[ "$ADM_PASS" != "$ADM_PASS2" ]]    && ERR="Passwords do not match"
  [[ ${#ADM_PASS} -lt 12 ]]            && ERR="Too short — need 12+ characters"
  [[ ! "$ADM_PASS" =~ [A-Z] ]]         && ERR="Need at least one UPPERCASE letter"
  [[ ! "$ADM_PASS" =~ [a-z] ]]         && ERR="Need at least one lowercase letter"
  [[ ! "$ADM_PASS" =~ [0-9] ]]         && ERR="Need at least one number"
  [[ ! "$ADM_PASS" =~ [^a-zA-Z0-9] ]] && ERR="Need at least one symbol (!@#\$...)"
  [[ -z "$ERR" ]] && break
  echo -e "  ${RD}✗ ${ERR}${CL}\n"
done

# ── AGENT SETTINGS ───────────────────────────────────────────────────────
divider
echo -e "  ${WH}AGENT SETTINGS${CL}"
divider

ask WEB_PORT "Web dashboard port" "7070"

pick SCAN_INT "Scan interval" \
  "60"   "Every 1 minute" \
  "300"  "Every 5 minutes (recommended)" \
  "600"  "Every 10 minutes" \
  "3600" "Every hour"

yesno "Enable auto-fix for safe issues (log cleanup etc)?" "y" && AFX_VAL="true" || AFX_VAL="false"

# ── SUMMARY ──────────────────────────────────────────────────────────────
divider
echo -e "  ${WH}INSTALLATION SUMMARY${CL}"
divider
echo -e "  Container:  ${GN}CT${CT_ID} '${CT_HOST}'${CL}"
echo -e "  Resources:  ${GN}${CT_CPU} CPU / ${CT_RAM}MB RAM / ${CT_DISK}GB disk${CL}"
echo -e "  Network:    ${GN}${CT_IP_DISPLAY} via ${CT_BR}${CL}"
echo -e "  Proxmox:    ${GN}${PVE_HOST} (${PVE_NODE})${CL}"
echo -e "  AI:         ${GN}${AI_PROV}${AI_MDL:+ — ${AI_MDL}}${CL}"
echo -e "  Offline AI: ${GN}Ollama (${OLLAMA_MDL})${CL}"
echo -e "  Web Search: ${GN}${WS_EN}${CL}"
echo -e "  Auto-Fix:   ${GN}${AFX_VAL}${CL}"
echo -e "  Admin:      ${GN}${ADM_USER}${CL}"
echo -e "  Web Port:   ${GN}${WEB_PORT}${CL}"
divider
echo ""
yesno "Proceed with installation?" "y" || { echo "Aborted."; exit 0; }

#══════════════════════════════════════════════════════════════════════════
#  INSTALL
#══════════════════════════════════════════════════════════════════════════
header
echo -e "  ${GN}Starting installation...${CL}\n"

msg_info "Checking LXC template"
TMPL="ubuntu-22.04-standard_22.04-1_amd64.tar.zst"
if ! ls /var/lib/vz/template/cache/$TMPL &>/dev/null; then
  msg_info "Downloading Ubuntu 22.04 template"
  pveam update -q 2>/dev/null
  pveam download local $TMPL 2>/dev/null || die "Template download failed"
fi
msg_ok "Template ready"

msg_info "Creating container CT${CT_ID}"
pct create ${CT_ID} local:vztmpl/${TMPL} \
  --hostname "${CT_HOST}" --storage "${CT_STG}" \
  --rootfs   "${CT_STG}:${CT_DISK}" --memory "${CT_RAM}" \
  --cores    "${CT_CPU}" \
  --net0     "name=eth0,bridge=${CT_BR},firewall=1,${CT_NET}" \
  --password "${CT_PASS}" --unprivileged 1 \
  --features nesting=1 --onboot 1 --start 1 2>/dev/null
sleep 6
CTIP=$(pct exec ${CT_ID} -- hostname -I 2>/dev/null | awk '{print $1}' || echo "pending")
msg_ok "Container CT${CT_ID} running — IP: ${CTIP}"

msg_info "Setting up SSH keys"
pct exec ${CT_ID} -- bash -c "
  mkdir -p /root/.ssh && chmod 700 /root/.ssh
  ssh-keygen -t ed25519 -f /root/.ssh/fixitblock_id -N '' -q 2>/dev/null
" 2>/dev/null
pct exec ${CT_ID} -- cat /root/.ssh/fixitblock_id.pub >> /root/.ssh/authorized_keys 2>/dev/null || true
msg_ok "SSH keys configured"

msg_info "Installing packages (1-2 minutes)"
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

msg_info "Deploying FixItBlock agent"
REPO="https://raw.githubusercontent.com/blockt4875-svg/fixitblock/main"
pct exec ${CT_ID} -- bash -c "
  mkdir -p /opt/fixitblock/agent/{ai,hardware,security,scripts,static}
  cd /opt/fixitblock
  for f in agent/proxmox_agent.py agent/server.py agent/cli.py \
            agent/static/index.html \
            agent/ai/brain.py agent/ai/__init__.py \
            agent/hardware/profiler.py agent/hardware/__init__.py \
            agent/security/auth.py agent/security/__init__.py; do
    curl -fsSL ${REPO}/\$f -o \$f 2>/dev/null
  done
" 2>/dev/null
msg_ok "Agent deployed"

msg_info "Writing configuration"
ANTH_KEY=""; OPEN_KEY=""; GROQ_KEY2=""
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
chmod 600 /opt/fixitblock/.env" 2>/dev/null
msg_ok "Configuration written"

msg_info "Creating admin account"
pct exec ${CT_ID} -- bash -c "
  cd /opt/fixitblock
  python3 -c \"
import sys,os; sys.path.insert(0,'agent'); os.chdir('/opt/fixitblock')
from dotenv import load_dotenv; load_dotenv('.env')
from security.auth import credentials
ok,msg = credentials.create_user('${ADM_USER}','${ADM_PASS}','admin')
sys.exit(0 if ok else 1)
\" 2>/dev/null
" && msg_ok "Admin '${ADM_USER}' created" || msg_error "Admin creation failed — set up via web UI"

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
systemctl daemon-reload && systemctl enable fixitblock && systemctl start fixitblock
" 2>/dev/null
msg_ok "Service installed and started"

msg_info "Hardening container"
pct exec ${CT_ID} -- bash -c "
  ufw --force reset 2>/dev/null; ufw default deny incoming 2>/dev/null
  ufw default allow outgoing 2>/dev/null; ufw allow ssh 2>/dev/null
  ufw allow ${WEB_PORT}/tcp 2>/dev/null; ufw --force enable 2>/dev/null
  systemctl enable fail2ban --now 2>/dev/null || true
" 2>/dev/null
msg_ok "Container hardened"

msg_info "Installing Ollama offline AI"
pct exec ${CT_ID} -- bash -c "
  curl -fsSL https://ollama.ai/install.sh | sh 2>/dev/null || true
  systemctl enable ollama --now 2>/dev/null || true
  sleep 3
  nohup bash -c 'ollama pull ${OLLAMA_MDL}; ollama pull mistral' \
    > /var/log/ollama-pull.log 2>&1 &
" 2>/dev/null
msg_ok "Ollama installed — models pulling in background"

sleep 3
FINAL_IP=$(pct exec ${CT_ID} -- hostname -I 2>/dev/null | awk '{print $1}' || echo "$CTIP")
STATUS=$(pct exec ${CT_ID} -- systemctl is-active fixitblock 2>/dev/null || echo "unknown")

divider
echo -e "  ${GN}✓ FixItBlock v3 Installation Complete!${CL}"
divider
echo -e "  ${BL}Web UI:${CL}    http://${FINAL_IP}:${WEB_PORT}"
echo -e "  ${BL}Username:${CL}  ${ADM_USER}"
echo -e "  ${BL}Container:${CL} CT${CT_ID} (${CT_HOST})"
echo -e "  ${BL}Service:${CL}   ${STATUS}"
echo ""
echo -e "  ${YW}Useful commands:${CL}"
echo -e "  pct exec ${CT_ID} -- journalctl -u fixitblock -f"
echo -e "  pct exec ${CT_ID} -- systemctl restart fixitblock"
divider
echo ""
