#!/usr/bin/env bash
# FixItBlock v3 — AI Proxmox Maintenance Agent

YW='\033[33m' BL='\033[36m' RD='\033[01;31m' GN='\033[1;92m' WH='\033[1;37m' CL='\033[m'
msg_info() { echo -e " ${YW}[....] $1${CL}"; }
msg_ok()   { echo -e " ${GN}[ OK ] $1${CL}"; }
msg_err()  { echo -e " ${RD}[FAIL] $1${CL}"; }
die()      { msg_err "$*"; exit 1; }
div()      { echo -e "\n${WH}  ════════════════════════════════════════════${CL}"; }

banner() {
  clear; echo -e "${YW}"
  echo '  ███████╗██╗██╗  ██╗██╗████████╗██████╗ ██╗      ██████╗  ██████╗██╗  ██╗'
  echo '  ██╔════╝██║╚██╗██╔╝██║╚══██╔══╝██╔══██╗██║     ██╔═══██╗██╔════╝██║ ██╔╝'
  echo '  █████╗  ██║ ╚███╔╝ ██║   ██║   ██████╔╝██║     ██║   ██║██║     █████╔╝ '
  echo '  ██╔══╝  ██║ ██╔██╗ ██║   ██║   ██╔══██╗██║     ██║   ██║██║     ██╔═██╗ '
  echo '  ██║     ██║██╔╝ ██╗██║   ██║   ██████╔╝███████╗╚██████╔╝╚██████╗██║  ██╗'
  echo '  ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝   ╚═╝   ╚═════╝ ╚══════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝'
  echo -e "${BL}              AI-Powered Proxmox Maintenance Agent v3${CL}\n"
}

pick() {
  local __v="$1"; shift; local __t="$1"; shift
  local __vals=() __labs=()
  while [[ $# -ge 2 ]]; do __vals+=("$1"); __labs+=("$2"); shift 2; done
  echo ""; echo -e "${WH}  ── ${__t} ─────────────────────────────────────────${CL}"
  for i in "${!__vals[@]}"; do
    echo -e "  ${YW}$((i+1))${CL}) ${WH}${__vals[$i]}${CL} — ${__labs[$i]}"
  done
  local c
  while true; do
    echo -ne "  ${BL}Choose [1-${#__vals[@]}]: ${CL}"; read -r c
    [[ "$c" =~ ^[0-9]+$ ]] && (( c >= 1 && c <= ${#__vals[@]} )) && \
      { printf -v "$__v" '%s' "${__vals[$((c-1))]}"; return; }
    echo -e "  ${RD}Enter a number 1-${#__vals[@]}${CL}"
  done
}

ask() {
  local __v="$1" __p="$2" __d="$3"
  echo -ne "  ${BL}${__p}${__d:+ [${__d}]}: ${CL}"; read -r __i
  printf -v "$__v" '%s' "${__i:-$__d}"
}

askpass() {
  local __v="$1" __p="$2" __i
  echo -ne "  ${BL}${__p}: ${CL}"; read -rs __i; echo ""
  printf -v "$__v" '%s' "$__i"
}

yn() {
  local __p="$1" __d="${2:-y}" __a
  echo -ne "  ${BL}${__p} [y/n] (default=${__d}): ${CL}"; read -r __a
  [[ "${__a:-$__d}" =~ ^[Yy] ]]
}

# ── Pre-flight checks ──────────────────────────────────────────────────
[[ $EUID -eq 0 ]]               || die "Must run as root"
command -v pveversion &>/dev/null || die "Must run on a Proxmox VE host"
command -v pct        &>/dev/null || die "pct not found"

banner
echo -e "  ${GN}Welcome! Press ENTER to accept defaults shown in [brackets].${CL}\n"
echo -ne "  ${BL}Press ENTER to begin...${CL}"; read -r

# ══════════════════════════════════════════════════════
#  SETTINGS
# ══════════════════════════════════════════════════════

div; echo -e "  ${WH}CONTAINER${CL}"; div

NEXT_ID=$(pvesh get /cluster/nextid 2>/dev/null || echo "200")
ask CT_ID   "Container ID" "$NEXT_ID"
ask CT_HOST "Hostname"     "fixitblock"

echo ""; echo -e "  ${WH}Available storage pools:${CL}"
pvesm status 2>/dev/null | awk 'NR>1 {
  free = ($2=="active") ? $5 : "n/a"
  printf "    %-20s  type: %-12s  status: %-8s  free: %s\n", $1, $3, $2, free
}' || echo "    local-lvm"
echo -e "\n  ${YW}You can type any storage name — NFS/CIFS/ZFS/Ceph all supported${CL}"
ask CT_STG "Storage pool" "local-lvm"

pick CT_RAM "RAM" \
  "512"  "512 MB — minimum" \
  "1024" "1 GB  — recommended" \
  "2048" "2 GB  — with Ollama" \
  "4096" "4 GB  — full AI stack" \
  "8192" "8 GB  — maximum"

pick CT_DISK "Disk (GB)" \
  "4"  "4 GB  — minimum" \
  "8"  "8 GB  — recommended" \
  "16" "16 GB — with AI models" \
  "32" "32 GB — maximum"

pick CT_CPU "CPU cores" \
  "1" "1 core" \
  "2" "2 cores — recommended" \
  "4" "4 cores — AI workloads" \
  "8" "8 cores"

echo ""; echo -e "  ${WH}Available bridges:${CL}"
ip link show 2>/dev/null | awk -F': ' '/^[0-9]+: vmbr/{print "    "$2}' || echo "    vmbr0"
ask CT_BR "Network bridge" "vmbr0"

pick NET_TYPE "IP configuration" \
  "dhcp"   "DHCP — automatic (recommended)" \
  "static" "Static IP — manual"

CT_NET="ip=dhcp"; CT_IP_DISPLAY="DHCP"
if [[ "$NET_TYPE" == "static" ]]; then
  ask CT_IP "IP/prefix e.g. 192.168.1.200/24" ""
  ask CT_GW "Gateway e.g. 192.168.1.1" ""
  CT_NET="ip=${CT_IP},gw=${CT_GW}"; CT_IP_DISPLAY="$CT_IP"
fi

while true; do
  askpass CT_PASS "Container root password (min 5 chars)"
  [[ ${#CT_PASS} -ge 5 ]] && break
  echo -e "  ${RD}Too short — minimum 5 characters${CL}"
done

div; echo -e "  ${WH}PROXMOX CONNECTION${CL}"; div

DEFAULT_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
ask PVE_HOST "Proxmox host IP"   "$DEFAULT_IP"
ask PVE_NODE "Proxmox node name" "$(hostname)"
ask PVE_USER "API user"          "root@pam"

pick AUTH_METHOD "Authentication" \
  "token"    "API Token (recommended)" \
  "password" "Password"

PVE_TOKEN_NAME="" PVE_TOKEN_VALUE="" PVE_PASS=""
if [[ "$AUTH_METHOD" == "token" ]]; then
  ask     PVE_TOKEN_NAME "Token name" "fixitblock"
  askpass PVE_TOKEN_VALUE "Token value"
else
  askpass PVE_PASS "Proxmox password"
fi

div; echo -e "  ${WH}AI CONFIGURATION${CL}"; div

pick AI_PROV "Primary AI provider" \
  "anthropic" "Anthropic Claude — best reasoning (needs API key)" \
  "ollama"    "Ollama only      — fully local, free, no key needed" \
  "openai"    "OpenAI GPT-4o   — needs API key" \
  "groq"      "Groq             — fast inference, needs API key" \
  "none"      "No AI            — basic monitoring only"

AI_KEY="" AI_MDL=""
case "$AI_PROV" in
  anthropic) askpass AI_KEY "Anthropic API key (console.anthropic.com)"; AI_MDL="claude-3-5-sonnet-20241022" ;;
  openai)    askpass AI_KEY "OpenAI API key";   AI_MDL="gpt-4o" ;;
  groq)      askpass AI_KEY "Groq API key";     AI_MDL="llama-3.1-70b-versatile" ;;
  ollama)    AI_MDL="llama3.2" ;;
esac

pick OLLAMA_MDL "Ollama offline model (always installed as fallback)" \
  "llama3.2"  "Llama 3.2  — recommended" \
  "mistral"   "Mistral 7B — fast" \
  "codellama" "CodeLlama  — code focused" \
  "llama3.1"  "Llama 3.1  — 8B" \
  "phi3"      "Phi-3 Mini — lightweight"

yn "Enable AI web search (DuckDuckGo, no key needed)?" "y" && WS_EN="true"    || WS_EN="false"
yn "Enable AI auto-execute for LOW risk fixes?"         "n" && AUTO_EXEC="true" || AUTO_EXEC="false"

div; echo -e "  ${WH}ADMIN ACCOUNT${CL}"; div

ask ADM_USER "Dashboard username" "admin"

while true; do
  askpass ADM_PASS  "Password (12+ chars, upper+lower+number+symbol)"
  askpass ADM_PASS2 "Confirm password"
  ERR=""
  [[ "$ADM_PASS" != "$ADM_PASS2" ]]    && ERR="Passwords do not match"
  [[ ${#ADM_PASS} -lt 12 ]]            && ERR="Too short — 12 characters minimum"
  [[ ! "$ADM_PASS" =~ [A-Z] ]]         && ERR="Need at least one UPPERCASE letter"
  [[ ! "$ADM_PASS" =~ [a-z] ]]         && ERR="Need at least one lowercase letter"
  [[ ! "$ADM_PASS" =~ [0-9] ]]         && ERR="Need at least one number"
  [[ ! "$ADM_PASS" =~ [^a-zA-Z0-9] ]] && ERR="Need at least one symbol e.g. !@#\$"
  [[ -z "$ERR" ]] && break
  echo -e "  ${RD}✗ ${ERR}${CL}\n"
done

div; echo -e "  ${WH}AGENT SETTINGS${CL}"; div

ask WEB_PORT "Web dashboard port" "7070"

pick SCAN_INT "Scan interval" \
  "60"   "Every 1 minute" \
  "300"  "Every 5 minutes (recommended)" \
  "600"  "Every 10 minutes" \
  "3600" "Every hour"

yn "Auto-fix safe issues (log cleanup, journal vacuum)?" "y" && AFX_VAL="true" || AFX_VAL="false"

div; echo -e "  ${WH}SUMMARY${CL}"; div
echo -e "  Container : ${GN}CT${CT_ID} '${CT_HOST}'${CL}"
echo -e "  Resources : ${GN}${CT_CPU} vCPU / ${CT_RAM}MB RAM / ${CT_DISK}GB disk${CL}"
echo -e "  Network   : ${GN}${CT_IP_DISPLAY} via ${CT_BR}${CL}"
echo -e "  Storage   : ${GN}${CT_STG}${CL}"
echo -e "  Proxmox   : ${GN}${PVE_HOST} / ${PVE_NODE}${CL}"
echo -e "  AI        : ${GN}${AI_PROV}${AI_MDL:+ (${AI_MDL})}${CL}"
echo -e "  Offline AI: ${GN}Ollama / ${OLLAMA_MDL}${CL}"
echo -e "  Web Search: ${GN}${WS_EN}${CL}"
echo -e "  Admin     : ${GN}${ADM_USER}${CL}"
echo -e "  Web Port  : ${GN}${WEB_PORT}${CL}"
div; echo ""
yn "Proceed with installation?" "y" || { echo "Aborted."; exit 0; }

# ══════════════════════════════════════════════════════
#  INSTALL
# ══════════════════════════════════════════════════════
banner
echo -e "  ${GN}Installing FixItBlock v3...${CL}\n"

# ── Template ──────────────────────────────────────────
msg_info "Checking Ubuntu 22.04 LXC template"
TMPL="ubuntu-22.04-standard_22.04-1_amd64.tar.zst"
if [[ ! -f "/var/lib/vz/template/cache/$TMPL" ]]; then
  msg_info "Downloading template"
  pveam update -q 2>/dev/null || true
  # Try local first, fall back to chosen storage
  pveam download local "$TMPL" 2>/dev/null || \
  pveam download "$CT_STG" "$TMPL" || \
  die "Template download failed"
fi
msg_ok "Template ready"

# ── Create container ──────────────────────────────────
STG_TYPE=$(pvesm status 2>/dev/null | awk -v s="$CT_STG" '$1==s{print $3}')
msg_info "Creating container CT${CT_ID} on ${CT_STG} (${STG_TYPE:-dir})"
pct status "$CT_ID" &>/dev/null && \
  die "CT${CT_ID} already exists — run: pct stop ${CT_ID} && pct destroy ${CT_ID}"

pct create "$CT_ID" "local:vztmpl/${TMPL}" \
  --hostname "$CT_HOST" \
  --storage  "$CT_STG" \
  --rootfs   "${CT_STG}:${CT_DISK}" \
  --memory   "$CT_RAM" \
  --cores    "$CT_CPU" \
  --net0     "name=eth0,bridge=${CT_BR},firewall=1,${CT_NET}" \
  --password "$CT_PASS" \
  --unprivileged 1 \
  --features nesting=1 \
  --onboot   1 \
  --start    1 \
  || die "Container creation failed — check storage name and free space"

# ── Wait for IP ───────────────────────────────────────
msg_info "Waiting for container network"
CTIP=""
for i in $(seq 1 30); do
  sleep 2
  CTIP=$(pct exec "$CT_ID" -- hostname -I 2>/dev/null | awk '{print $1}')
  [[ -n "$CTIP" ]] && break
done
[[ -z "$CTIP" ]] && CTIP="<check-dhcp-lease>"
msg_ok "Container running — IP: ${CTIP}"

# ── SSH keypair ───────────────────────────────────────
msg_info "Generating SSH keypair"
pct exec "$CT_ID" -- bash -c '
  mkdir -p /root/.ssh && chmod 700 /root/.ssh
  ssh-keygen -t ed25519 -f /root/.ssh/fixitblock_id -N "" -q 2>/dev/null || true
' 2>/dev/null || true
pct exec "$CT_ID" -- cat /root/.ssh/fixitblock_id.pub \
  >> /root/.ssh/authorized_keys 2>/dev/null || true
msg_ok "SSH keypair ready"

# ── System packages ───────────────────────────────────
msg_info "Installing system packages (2-4 mins — you will see output)"
pct exec "$CT_ID" -- bash -c '
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y python3 python3-pip git curl ufw fail2ban
' || die "System packages failed"
msg_ok "System packages installed"

# ── Python packages ───────────────────────────────────
msg_info "Installing Python packages (1-2 mins)"
pct exec "$CT_ID" -- bash -c '
  pip3 install --break-system-packages \
    flask flask-cors requests paramiko python-dotenv \
    openai anthropic groq cryptography psutil \
  || pip3 install \
    flask flask-cors requests paramiko python-dotenv \
    openai anthropic groq cryptography psutil
' || die "Python packages failed"
msg_ok "Python packages installed"

# ── Agent files ───────────────────────────────────────
msg_info "Deploying agent from GitHub"
REPO="https://raw.githubusercontent.com/blockt4875-svg/fixitblock/main"
pct exec "$CT_ID" -- bash -c "
  mkdir -p /opt/fixitblock/agent/{ai,hardware,security,scripts,static}
  cd /opt/fixitblock
  for f in \
    agent/proxmox_agent.py agent/server.py agent/cli.py \
    agent/static/index.html \
    agent/ai/brain.py agent/ai/__init__.py \
    agent/hardware/profiler.py agent/hardware/__init__.py \
    agent/security/auth.py agent/security/__init__.py; do
    curl -fsSL '${REPO}'/\$f -o \$f 2>/dev/null \
      && echo '  pulled '\$f \
      || echo '  WARN: failed '\$f
  done
" || die "Agent deployment failed"
msg_ok "Agent deployed"

# ── Config — written locally then pushed to avoid quoting issues ──
msg_info "Writing configuration"
ANTH_KEY="" OPEN_KEY="" GROQ_KEY2=""
[[ "$AI_PROV" == "anthropic" ]] && ANTH_KEY="$AI_KEY"
[[ "$AI_PROV" == "openai" ]]    && OPEN_KEY="$AI_KEY"
[[ "$AI_PROV" == "groq" ]]      && GROQ_KEY2="$AI_KEY"

CFG=$(mktemp)
cat > "$CFG" << EOF
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
EOF
pct push "$CT_ID" "$CFG" /opt/fixitblock/.env --perms 0600
rm -f "$CFG"
msg_ok "Config written"

# ── Admin account — password written to file to survive special chars ──
msg_info "Creating admin account"
PY=$(mktemp)
# Write password safely to a separate file — no shell expansion touches it
PWFILE=$(mktemp)
printf '%s' "$ADM_PASS" > "$PWFILE"
pct push "$CT_ID" "$PWFILE" /tmp/fib_pw --perms 0600
rm -f "$PWFILE"

cat > "$PY" << PYEOF
import sys, os
sys.path.insert(0, 'agent')
os.chdir('/opt/fixitblock')
from dotenv import load_dotenv; load_dotenv('.env')
from security.auth import credentials
pw = open('/tmp/fib_pw').read()
os.remove('/tmp/fib_pw')
ok, msg = credentials.create_user('${ADM_USER}', pw, 'admin')
print(msg)
sys.exit(0 if ok else 1)
PYEOF
pct push "$CT_ID" "$PY" /tmp/fib_setup.py --perms 0700
rm -f "$PY"
pct exec "$CT_ID" -- bash -c '
  cd /opt/fixitblock && python3 /tmp/fib_setup.py; rm -f /tmp/fib_setup.py
' && msg_ok "Admin '${ADM_USER}' created" \
  || msg_err "Admin creation failed — set up manually via web UI on first login"

# ── Systemd service ───────────────────────────────────
msg_info "Installing systemd service"
pct exec "$CT_ID" -- bash -c '
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
' || die "Service install failed"
msg_ok "Service installed and started"

# ── Firewall ──────────────────────────────────────────
msg_info "Hardening container"
pct exec "$CT_ID" -- bash -c "
  ufw --force reset   >/dev/null 2>&1
  ufw default deny incoming  >/dev/null 2>&1
  ufw default allow outgoing >/dev/null 2>&1
  ufw allow ssh              >/dev/null 2>&1
  ufw allow ${WEB_PORT}/tcp  >/dev/null 2>&1
  ufw --force enable         >/dev/null 2>&1
  systemctl enable fail2ban --now >/dev/null 2>&1 || true
"
msg_ok "Firewall and fail2ban active"

# ── Ollama ────────────────────────────────────────────
msg_info "Installing Ollama offline AI"
pct exec "$CT_ID" -- bash -c '
  curl -fsSL https://ollama.ai/install.sh | sh >/dev/null 2>&1 || true
  systemctl enable ollama --now >/dev/null 2>&1 || true
  sleep 3
' || true
pct exec "$CT_ID" -- bash -c "
  nohup bash -c 'ollama pull ${OLLAMA_MDL}; ollama pull mistral' \
    > /var/log/ollama-pull.log 2>&1 &
" || true
msg_ok "Ollama installed — models downloading in background"

# ── Done ──────────────────────────────────────────────
sleep 5
FINAL_IP=$(pct exec "$CT_ID" -- hostname -I 2>/dev/null | awk '{print $1}')
FINAL_IP="${FINAL_IP:-$CTIP}"
STATUS=$(pct exec "$CT_ID" -- systemctl is-active fixitblock 2>/dev/null || echo "unknown")

div
echo -e "  ${GN}✓ FixItBlock v3 Installation Complete!${CL}"
div
echo -e "  ${BL}Web UI   :${CL}  http://${FINAL_IP}:${WEB_PORT}"
echo -e "  ${BL}Username :${CL}  ${ADM_USER}"
echo -e "  ${BL}Container:${CL}  CT${CT_ID} (${CT_HOST})"
echo -e "  ${BL}Service  :${CL}  ${STATUS}"
echo ""
echo -e "  ${YW}Useful commands:${CL}"
echo -e "  pct exec ${CT_ID} -- journalctl -u fixitblock -f"
echo -e "  pct exec ${CT_ID} -- systemctl restart fixitblock"
echo -e "  pct exec ${CT_ID} -- tail -f /var/log/ollama-pull.log"
div
echo ""
