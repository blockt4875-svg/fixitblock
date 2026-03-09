"""
FixItBlock — AI Brain v2
Primary:  Anthropic Claude (claude-3-5-sonnet)
Offline:  Ollama (local — multiple models, no internet needed)
Optional: OpenAI, Groq

Autonomous capabilities:
  - Web search for fixes, scripts, CVEs
  - Auto-execute approved fixes via SSH
  - Self-heal own config and dependencies
  - Generate hardware-optimized scripts
  - Chain-of-thought deep reasoning
"""

import os, re, json, time, hashlib, logging, threading, subprocess
from typing import Optional
from datetime import datetime, timezone
from collections import deque
import requests

log = logging.getLogger("fixitblock.ai")

# ─── CONFIG ────────────────────────────────────────────────────────────
class AIConfig:
    def __init__(self):
        self.provider          = os.getenv("AI_PROVIDER",         "anthropic").lower()
        # Anthropic (primary)
        self.anthropic_key     = os.getenv("ANTHROPIC_API_KEY",   "")
        self.anthropic_model   = os.getenv("ANTHROPIC_MODEL",     "claude-3-5-sonnet-20241022")
        # Ollama (offline fallback — multiple models supported)
        self.ollama_host       = os.getenv("OLLAMA_HOST",         "http://localhost:11434")
        self.ollama_model      = os.getenv("OLLAMA_MODEL",        "llama3.2")
        self.ollama_model2     = os.getenv("OLLAMA_MODEL2",       "mistral")
        self.ollama_model3     = os.getenv("OLLAMA_MODEL3",       "codellama")
        # OpenAI (optional)
        self.openai_key        = os.getenv("OPENAI_API_KEY",      "")
        self.openai_model      = os.getenv("OPENAI_MODEL",        "gpt-4o")
        # Groq (optional — very fast)
        self.groq_key          = os.getenv("GROQ_API_KEY",        "")
        self.groq_model        = os.getenv("GROQ_MODEL",          "llama-3.1-70b-versatile")
        # Behaviour
        self.web_search        = os.getenv("AI_WEB_SEARCH",       "true").lower() == "true"
        self.auto_execute      = os.getenv("AI_AUTO_EXECUTE",     "false").lower() == "true"
        self.self_heal         = os.getenv("AI_SELF_HEAL",        "true").lower() == "true"
        self.chain_of_thought  = os.getenv("AI_COT",              "true").lower() == "true"
        self.max_tokens        = int(os.getenv("AI_MAX_TOKENS",   "8192"))
        self.temperature       = float(os.getenv("AI_TEMPERATURE","0.15"))
        self.require_approval  = os.getenv("AI_REQUIRE_APPROVAL", "true").lower() == "true"
        self.max_auto_risk     = os.getenv("AI_MAX_AUTO_RISK",    "low")
        # Search APIs
        self.brave_key         = os.getenv("BRAVE_SEARCH_KEY",    "")
        self.serpapi_key       = os.getenv("SERPAPI_KEY",         "")
        # Cascade order: comma-separated provider names
        raw = os.getenv("AI_CASCADE", "anthropic,ollama")
        self.cascade = [p.strip().lower() for p in raw.split(",") if p.strip()]

    def model_for(self, provider: str) -> str:
        return {"anthropic": self.anthropic_model, "openai": self.openai_model,
                "ollama": self.ollama_model, "groq": self.groq_model}.get(provider, "?")

ai_config = AIConfig()

# ─── WEB SEARCH ────────────────────────────────────────────────────────
class WebSearchEngine:
    def __init__(self, cfg: AIConfig):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "FixItBlock/3.0 Proxmox-AI"})
        self._cache: dict = {}

    def search(self, query: str, num: int = 6) -> list[dict]:
        cached = self._cache.get(query)
        if cached and time.time() - cached[0] < 300:
            return cached[1]
        log.info(f"[search] {query[:70]}")
        results = []
        if self.cfg.brave_key:   results = self._brave(query, num)
        if not results and self.cfg.serpapi_key: results = self._serpapi(query, num)
        if not results:          results = self._ddg(query, num)
        self._cache[query] = (time.time(), results)
        return results

    def _brave(self, q, n):
        try:
            r = self.session.get("https://api.search.brave.com/res/v1/web/search",
                headers={"Accept":"application/json","X-Subscription-Token":self.cfg.brave_key},
                params={"q":q,"count":n}, timeout=10)
            r.raise_for_status()
            return [{"title":w.get("title",""),"url":w.get("url",""),"snippet":w.get("description","")}
                    for w in r.json().get("web",{}).get("results",[])]
        except: return []

    def _serpapi(self, q, n):
        try:
            r = self.session.get("https://serpapi.com/search",
                params={"q":q,"api_key":self.cfg.serpapi_key,"num":n}, timeout=10)
            r.raise_for_status()
            return [{"title":i.get("title",""),"url":i.get("link",""),"snippet":i.get("snippet","")}
                    for i in r.json().get("organic_results",[])]
        except: return []

    def _ddg(self, q, n):
        try:
            r = self.session.get("https://api.duckduckgo.com/",
                params={"q":q,"format":"json","no_html":"1","skip_disambig":"1"}, timeout=10)
            r.raise_for_status()
            d = r.json(); out = []
            if d.get("AbstractText"):
                out.append({"title":d.get("Heading",""),"url":d.get("AbstractURL",""),"snippet":d.get("AbstractText","")})
            for t in d.get("RelatedTopics",[])[:n]:
                if isinstance(t,dict) and t.get("Text"):
                    out.append({"title":t.get("Text","")[:70],"url":t.get("FirstURL",""),"snippet":t.get("Text","")})
            return out[:n]
        except: return []

    def fetch_page(self, url: str, max_chars: int = 4000) -> str:
        try:
            r = self.session.get(url, timeout=15); r.raise_for_status()
            t = re.sub(r"<script[^>]*>.*?</script>","",r.text,flags=re.DOTALL|re.I)
            t = re.sub(r"<style[^>]*>.*?</style>","",t,flags=re.DOTALL|re.I)
            t = re.sub(r"<[^>]+>"," ",t)
            return re.sub(r"\s+"," ",t).strip()[:max_chars]
        except: return ""

    def search_forum(self, q): return self.search(f"site:forum.proxmox.com {q}", 4)
    def search_github(self, q): return self.search(f"site:github.com proxmox bash {q}", 4)
    def search_cve(self, ver): return self.search(f"CVE proxmox {ver} 2024 2025", 5)

# ─── PROVIDERS ─────────────────────────────────────────────────────────
class AnthropicProvider:
    name = "anthropic"
    def __init__(self, cfg):
        self.cfg = cfg
    def chat(self, messages, system=""):
        user_msgs = [m for m in messages if m["role"] != "system"]
        sys_parts = [m["content"] for m in messages if m["role"] == "system"]
        if system: sys_parts.insert(0, system)
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key":self.cfg.anthropic_key,"anthropic-version":"2023-06-01","content-type":"application/json"},
            json={"model":self.cfg.anthropic_model,"max_tokens":self.cfg.max_tokens,
                  "messages":user_msgs,**({} if not sys_parts else {"system":"\n\n".join(sys_parts)})},
            timeout=90)
        r.raise_for_status()
        return r.json()["content"][0]["text"]
    def available(self):
        if not self.cfg.anthropic_key: return False
        try:
            r = requests.post("https://api.anthropic.com/v1/messages",
                headers={"x-api-key":self.cfg.anthropic_key,"anthropic-version":"2023-06-01","content-type":"application/json"},
                json={"model":self.cfg.anthropic_model,"max_tokens":1,"messages":[{"role":"user","content":"hi"}]},
                timeout=8)
            return r.status_code in (200,400)
        except: return False

class OllamaProvider:
    name = "ollama"
    def __init__(self, cfg, model_override=None):
        self.cfg   = cfg
        self.model = model_override or cfg.ollama_model
    def chat(self, messages, system=""):
        msgs   = [m for m in messages if m["role"] != "system"]
        sysp   = [m["content"] for m in messages if m["role"] == "system"]
        if system: sysp.insert(0, system)
        r = requests.post(f"{self.cfg.ollama_host}/api/chat",
            json={"model":self.model,"messages":msgs,"stream":False,
                  "system":"\n\n".join(sysp) if sysp else "",
                  "options":{"temperature":self.cfg.temperature,"num_ctx":8192}},
            timeout=180)
        r.raise_for_status()
        return r.json()["message"]["content"]
    def available(self):
        try:
            r = requests.get(f"{self.cfg.ollama_host}/api/tags", timeout=5)
            if r.status_code != 200: return False
            models = [m["name"] for m in r.json().get("models",[])]
            return any(self.model in m for m in models)
        except: return False
    def list_models(self):
        try:
            r = requests.get(f"{self.cfg.ollama_host}/api/tags", timeout=5)
            return [m["name"] for m in r.json().get("models",[])]
        except: return []
    def pull(self, model=None):
        m = model or self.model
        try:
            r = requests.post(f"{self.cfg.ollama_host}/api/pull",json={"name":m},timeout=600,stream=True)
            return r.status_code == 200
        except: return False

class OpenAIProvider:
    name = "openai"
    def __init__(self, cfg):
        self.cfg = cfg
    def chat(self, messages, system=""):
        msgs = list(messages)
        if system: msgs.insert(0,{"role":"system","content":system})
        r = requests.post("https://api.openai.com/v1/chat/completions",
            headers={"Authorization":f"Bearer {self.cfg.openai_key}","Content-Type":"application/json"},
            json={"model":self.cfg.openai_model,"messages":msgs,"max_tokens":self.cfg.max_tokens,"temperature":self.cfg.temperature},
            timeout=90)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    def available(self):
        if not self.cfg.openai_key: return False
        try:
            r = requests.get("https://api.openai.com/v1/models",headers={"Authorization":f"Bearer {self.cfg.openai_key}"},timeout=5)
            return r.status_code == 200
        except: return False

class GroqProvider:
    name = "groq"
    def __init__(self, cfg):
        self.cfg = cfg
    def chat(self, messages, system=""):
        msgs = list(messages)
        if system: msgs.insert(0,{"role":"system","content":system})
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization":f"Bearer {self.cfg.groq_key}","Content-Type":"application/json"},
            json={"model":self.cfg.groq_model,"messages":msgs,"max_tokens":self.cfg.max_tokens,"temperature":self.cfg.temperature},
            timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    def available(self):
        if not self.cfg.groq_key: return False
        try:
            r = requests.get("https://api.groq.com/openai/v1/models",headers={"Authorization":f"Bearer {self.cfg.groq_key}"},timeout=5)
            return r.status_code == 200
        except: return False

def _make_provider(name: str, cfg: AIConfig):
    return {"anthropic":AnthropicProvider,"ollama":OllamaProvider,
            "openai":OpenAIProvider,"groq":GroqProvider}.get(name, OllamaProvider)(cfg)

# ─── CASCADE ROUTER ─────────────────────────────────────────────────────
class CascadeRouter:
    """Try providers in order. Anthropic → Ollama → etc."""
    def __init__(self, cfg: AIConfig):
        self.cfg       = cfg
        self._provs    = []
        self._active   = None
        self._lock     = threading.Lock()
        self._build_providers()

    def _build_providers(self):
        self._provs = []
        for name in self.cfg.cascade:
            self._provs.append(_make_provider(name, self.cfg))
        # Always add all Ollama models as fallbacks
        ollama_base = OllamaProvider(self.cfg)
        if ollama_base not in self._provs:
            self._provs.append(ollama_base)
        for alt_model in [self.cfg.ollama_model2, self.cfg.ollama_model3]:
            if alt_model:
                self._provs.append(OllamaProvider(self.cfg, model_override=alt_model))

    def chat(self, messages, system="") -> tuple[str, str]:
        errs = []
        for prov in self._provs:
            try:
                if not prov.available():
                    log.debug(f"[cascade] {prov.name} unavailable")
                    continue
                log.info(f"[cascade] using {prov.name}")
                resp = prov.chat(messages, system=system)
                with self._lock: self._active = prov.name
                return resp, prov.name
            except Exception as e:
                log.warning(f"[cascade] {prov.name} error: {e}")
                errs.append(f"{prov.name}: {e}")
        raise RuntimeError(f"All providers failed: {'; '.join(errs)}")

    def active(self) -> str:
        with self._lock: return self._active or (self.cfg.cascade[0] if self.cfg.cascade else "none")

    def status(self) -> dict:
        seen = set()
        out  = {}
        for p in self._provs:
            key = getattr(p, "model", p.name)
            if key in seen: continue
            seen.add(key)
            out[f"{p.name}:{key}"] = {"available": p.available(), "model": key}
        return out

# ─── MEMORY ─────────────────────────────────────────────────────────────
class ConversationMemory:
    def __init__(self, max_turns=25):
        self._msgs = deque(maxlen=max_turns*2)
        self._lock = threading.Lock()
    def add(self, role, content):
        with self._lock: self._msgs.append({"role":role,"content":content})
    def get(self):
        with self._lock: return list(self._msgs)
    def clear(self):
        with self._lock: self._msgs.clear()

# ─── RISK ENGINE ─────────────────────────────────────────────────────────
RISK_MAP = {
    "critical": [r"rm\s+-rf\s+/(?!\s)",r"mkfs\.",r"dd\s+if=.*of=/dev/[sh]d",r">\s*/dev/sd"],
    "high":     [r"pct\s+destroy",r"qm\s+destroy",r"zpool\s+destroy",r"lvremove",r"wipefs"],
    "medium":   [r"systemctl\s+stop\s+pve",r"iptables\s+-F",r"ufw\s+disable",r"pct\s+stop\b",r"qm\s+stop\b"],
    "low":      [r"journalctl\s+--vacuum",r"apt-get\s+autoremove",r"zpool\s+scrub",r"apt-get\s+clean"],
}
RISK_ORDER = ["safe","low","medium","high","critical"]

def assess_risk(script: str) -> tuple[str, list[str]]:
    warnings = []; max_r = "safe"
    for level, pats in RISK_MAP.items():
        for p in pats:
            if re.search(p, script, re.I):
                warnings.append(f"[{level.upper()}] {p}")
                if RISK_ORDER.index(level) > RISK_ORDER.index(max_r): max_r = level
    return max_r, warnings

# ─── EXECUTOR ────────────────────────────────────────────────────────────
class ScriptExecutor:
    def __init__(self, cfg, ssh=None):
        self.cfg  = cfg
        self.ssh  = ssh
        self._hist: list = []
        self._pend: dict = {}
        self._lock = threading.Lock()

    def submit(self, script: str, desc="", source="ai", requester="system") -> dict:
        risk, warnings = assess_risk(script)
        entry = {"id": hashlib.sha256(script.encode()).hexdigest()[:12],
                 "script": script, "description": desc, "source": source,
                 "requester": requester, "risk": risk, "warnings": warnings,
                 "submitted_at": datetime.now(timezone.utc).isoformat()}
        if risk == "critical":
            entry.update({"status":"blocked","message":"CRITICAL risk — blocked automatically."})
            self._rec(entry); return entry
        needs_ok = self.cfg.require_approval and RISK_ORDER.index(risk) > RISK_ORDER.index(self.cfg.max_auto_risk)
        if needs_ok and not self.cfg.auto_execute:
            entry.update({"status":"pending_approval","message":f"Risk '{risk}' — awaiting approval."})
            with self._lock: self._pend[entry["id"]] = entry
            return entry
        return self._run(entry)

    def approve(self, sid):
        with self._lock: e = self._pend.pop(sid, None)
        return self._run(e) if e else {"status":"not_found"}

    def reject(self, sid):
        with self._lock: e = self._pend.pop(sid, None)
        if e: e["status"] = "rejected"; self._rec(e)
        return e or {"status":"not_found"}

    def _run(self, entry: dict) -> dict:
        if not self.ssh:
            entry.update({"status":"error","message":"No SSH connection"}); self._rec(entry); return entry
        t0 = time.time()
        try:
            rc, out, err = self.ssh.run(entry["script"], timeout=120)
            entry.update({"status":"success" if rc==0 else "failed","exit_code":rc,
                          "stdout":out[:3000],"stderr":err[:1000],"duration":round(time.time()-t0,2)})
        except Exception as e:
            entry.update({"status":"error","message":str(e),"duration":round(time.time()-t0,2)})
        self._rec(entry); return entry

    def _rec(self, e):
        with self._lock:
            self._hist.append(e)
            if len(self._hist) > 200: self._hist = self._hist[-200:]

    def history(self): 
        with self._lock: return list(self._hist)
    def pending(self): 
        with self._lock: return list(self._pend.values())

# ─── SELF HEALER ─────────────────────────────────────────────────────────
class SelfHealer:
    AGENT_DIR = "/opt/fixitblock"
    ENV_FILE  = "/opt/fixitblock/.env"

    def __init__(self, cfg, brain):
        self.cfg   = cfg
        self.brain = brain
        self._log  = []
        self._lock = threading.Lock()

    def check(self) -> list:
        if not self.cfg.self_heal: return []
        results = []
        results += self._check_deps()
        results += self._check_service()
        results += self._check_disk()
        with self._lock:
            self._log.extend(results)
            self._log = self._log[-50:]
        return results

    def _check_deps(self):
        out = []
        for pkg in ["flask","requests","paramiko"]:
            try: __import__(pkg)
            except ImportError:
                try:
                    subprocess.run(["pip3","install","--quiet","--break-system-packages",pkg],
                        capture_output=True, timeout=60)
                    out.append({"check":"dep","msg":f"Auto-installed: {pkg}","status":"fixed"})
                except Exception as e:
                    out.append({"check":"dep","msg":f"Could not install {pkg}: {e}","status":"error"})
        return out

    def _check_service(self):
        try:
            r = subprocess.run(["systemctl","is-active","fixitblock"],capture_output=True,text=True,timeout=5)
            if r.stdout.strip() != "active":
                subprocess.run(["systemctl","restart","fixitblock"],capture_output=True,timeout=15)
                return [{"check":"service","msg":"Service restarted (was inactive)","status":"fixed"}]
        except: pass
        return []

    def _check_disk(self):
        try:
            r = subprocess.run(["df","-h",self.AGENT_DIR],capture_output=True,text=True,timeout=5)
            for line in r.stdout.splitlines()[1:]:
                pct = [p for p in line.split() if "%" in p]
                if pct and int(pct[0].rstrip("%")) > 90:
                    return [{"check":"disk","msg":f"Agent disk at {pct[0]}%","status":"warning"}]
        except: pass
        return []

    def get_log(self):
        with self._lock: return list(self._log)

# ─── SYSTEM PROMPT ───────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are FixItBlock AI — an autonomous expert Proxmox VE administrator embedded in the FixItBlock agent.

PRIMARY: Anthropic Claude | OFFLINE FALLBACK: Ollama (local models)

CAPABILITIES:
- Expert in Proxmox VE, Linux, ZFS, Ceph, KVM, LXC, networking, storage
- Real-time web search for fixes, scripts, CVEs (when online)
- Autonomous script generation and execution (with risk gating)
- Hardware-aware: all recommendations adapt to detected system profile
- Self-healing: monitors and repairs own config and dependencies
- Deep chain-of-thought reasoning

REASONING (always follow this order):
1. THINK — use <thinking>...</thinking> tags to reason through the problem
2. SEARCH — note if web search would help
3. PLAN — outline fix steps before any script
4. ACT — generate safe, idempotent, commented script
5. VERIFY — describe how to confirm the fix worked

SCRIPT RULES:
- `set -euo pipefail` always
- Header comment: # RISK: LOW | MEDIUM | HIGH | CRITICAL
- Prerequisite checks before making changes
- Idempotent (safe to run twice)
- Rollback instructions as comments

RISK GATING:
- LOW: can auto-execute if enabled
- MEDIUM: show and ask approval
- HIGH: explain full impact, require confirmation
- CRITICAL: never auto-execute

OFFLINE MODE (Ollama):
- State you're offline when using local AI
- Skip web search hints
- Still generate full scripts from training knowledge
- Multiple models available as fallbacks

Hardware context will be injected automatically with each request.
"""

# ─── MAIN BRAIN ─────────────────────────────────────────────────────────
class AIBrain:
    def __init__(self, cfg: AIConfig, search: WebSearchEngine, hardware_profile=None, ssh_client=None):
        self.cfg      = cfg
        self.search   = search
        self.memory   = ConversationMemory(max_turns=30)
        self.router   = CascadeRouter(cfg)
        self.executor = ScriptExecutor(cfg, ssh=ssh_client)
        self.healer   = SelfHealer(cfg, self)
        self._hw      = hardware_profile or {}
        self._lock    = threading.Lock()
        self.memory.add("system", SYSTEM_PROMPT)
        if cfg.self_heal:
            threading.Thread(target=self._heal_loop, daemon=True, name="self-healer").start()

    def update_hardware(self, p):
        with self._lock: self._hw = p

    def set_ssh(self, ssh):
        self.executor.ssh = ssh

    def _is_online(self):
        try: requests.get("https://1.1.1.1", timeout=3); return True
        except: return False

    def _ctx(self, issues=None) -> str:
        h = self._hw; lines = ["=== SYSTEM CONTEXT ==="]
        if h:
            lines += [
                f"CPU: {h.get('cpu_model','')} | {h.get('cpu_cores',0)}c/{h.get('cpu_threads',0)}t | AES:{h.get('has_aes',False)} AVX2:{h.get('has_avx2',False)}",
                f"RAM: {h.get('ram_total_gb',0):.1f}GB total, {h.get('ram_free_gb',0):.1f}GB free",
            ]
            if h.get("nvme_devices"):    lines.append(f"NVMe: {len(h['nvme_devices'])} drives")
            if h.get("zfs_pools"):       lines.append(f"ZFS: {', '.join(h['zfs_pools'])}")
            if h.get("gpus"):            lines.append(f"GPU: {h['gpus'][0]}")
            if h.get("proxmox_version"): lines.append(f"PVE: {h['proxmox_version']}")
            if h.get("cluster_name"):    lines.append(f"Cluster: {h['cluster_name']}")
        lines.append(f"AI: {self.router.active()} | Online: {self._is_online()} | AutoExec: {self.cfg.auto_execute}")
        if issues:
            lines.append(f"Issues ({len(issues)}):")
            for i in issues[:6]: lines.append(f"  [{i['severity'].upper()}] {i['category']}: {i['title']}")
        lines.append("=== END CONTEXT ===\n")
        return "\n".join(lines)

    def think(self, user_message: str, issues=None, use_search=True, auto_exec=False) -> dict:
        result = {"response":"","scripts":[],"executed":[],"search_used":False,
                  "sources":[],"thinking":[],"provider":"","offline_mode":False,
                  "pending_scripts":[],"timestamp":datetime.now(timezone.utc).isoformat()}
        online = self._is_online()

        # Web search
        search_results = []
        if use_search and self.cfg.web_search and online:
            sq = self._sq(user_message)
            if sq:
                result["thinking"].append(f"🔍 Searching: {sq}")
                search_results = self.search.search(sq, num=5)
                if search_results:
                    result["search_used"] = True
                    result["sources"] = [{"title":s["title"],"url":s["url"]} for s in search_results]
                    result["thinking"].append(f"Found {len(search_results)} sources")
                    best = search_results[0].get("url","")
                    if best and ("github" in best or "proxmox" in best):
                        fetched = self.search.fetch_page(best, 2500)
                        if fetched: search_results[0]["page"] = fetched

        # Build full prompt
        prompt = self._ctx(issues)
        if search_results:
            prompt += "\n=== WEB SEARCH ===\n"
            for i, sr in enumerate(search_results[:3], 1):
                prompt += f"\n[{i}] {sr['title']}\n{sr['url']}\n{sr.get('page', sr.get('snippet',''))[:700]}\n"
            prompt += "=== END ===\n\n"
        if self.cfg.chain_of_thought:
            prompt += "Think step by step using <thinking>...</thinking> before answering.\n\n"
        if not online:
            prompt += "[OFFLINE MODE — no internet, using local Ollama AI]\n\n"
        prompt += user_message

        # Inference
        self.memory.add("user", prompt)
        try:
            resp, prov = self.router.chat(self.memory.get(), system=SYSTEM_PROMPT)
            self.memory.add("assistant", resp)
            result["provider"]     = prov
            result["offline_mode"] = (prov == "ollama")
            # Extract thinking tags
            m = re.search(r"<thinking>(.*?)</thinking>", resp, re.DOTALL)
            if m:
                for line in m.group(1).strip().splitlines()[:6]:
                    if line.strip(): result["thinking"].append(f"💭 {line.strip()}")
                resp = re.sub(r"<thinking>.*?</thinking>","",resp,flags=re.DOTALL).strip()
            result["response"] = resp
        except Exception as e:
            result["response"] = f"⚠ AI error: {e}"; return result

        # Scripts
        scripts = self._extract_scripts(result["response"])
        result["scripts"] = scripts

        if scripts and (auto_exec or self.cfg.auto_execute):
            for sc in scripts:
                if sc["language"] in ("bash","sh"):
                    er = self.executor.submit(sc["content"], desc=user_message[:80], source="ai")
                    if er["status"] == "pending_approval":
                        result["pending_scripts"].append(er)
                        result["thinking"].append(f"⏳ Script awaiting approval (risk={er['risk']})")
                    elif er["status"] == "success":
                        result["executed"].append(er)
                        result["thinking"].append("✅ Script executed")
                    elif er["status"] == "blocked":
                        result["thinking"].append(f"🚫 Blocked: {er['message']}")
                    else:
                        result["thinking"].append(f"❌ Failed: {er.get('message','')}")
        return result

    def _sq(self, msg: str) -> Optional[str]:
        triggers = ["fix","error","failed","issue","problem","script","help","disk","memory",
                    "zfs","network","backup","cve","security","upgrade","performance","crash"]
        if any(t in msg.lower() for t in triggers):
            return f"proxmox {' '.join(re.sub(r'[^\w\s]',' ',msg).split()[:7])}"
        return None

    def _extract_scripts(self, text: str) -> list:
        out = []
        for m in re.finditer(r"```(bash|sh|python3?|zsh)?\n(.*?)```", text, re.DOTALL):
            code = m.group(2).strip()
            if len(code) < 10: continue
            risk, warnings = assess_risk(code)
            out.append({"language":m.group(1) or "bash","content":code,
                        "lines":len(code.splitlines()),"risk":risk,"warnings":warnings})
        return out

    def generate_fix_script(self, issue: dict, hw: dict) -> dict:
        return self.think(f"""Generate a complete, safe bash fix script for:
Issue: {issue['title']} ({issue['severity']})
Description: {issue['description']}
Category: {issue['category']}
Hardware: {hw.get('cpu_cores',0)} cores, {hw.get('ram_total_gb',0):.0f}GB RAM, NVMe={len(hw.get('nvme_devices',[]))}, ZFS={hw.get('zfs_pools',[])}
PVE: {hw.get('proxmox_version','?')}

Requirements: set -euo pipefail, RISK label, prereq checks, fix, verify step, rollback comments.
Return script in ```bash block.""", use_search=True)

    def search_scripts_for(self, task: str) -> dict:
        forum   = self.search.search_forum(task)
        github  = self.search.search_github(task)
        all_src = forum + github
        snippets = ""
        for r in github[:2]:
            if r.get("url"):
                p = self.search.fetch_page(r["url"], 2500)
                if p: snippets += f"\n--- {r['url']} ---\n{p}\n"
        result = self.think(f"""Task: {task}
Found community resources:
{json.dumps(all_src[:4],indent=2)[:1500]}
{f'Script content:{snippets[:1500]}' if snippets else ''}
Generate an optimized, safe, hardware-adapted bash script. Include source URLs as comments.""", use_search=False)
        result["sources"] = [{"title":r["title"],"url":r["url"]} for r in all_src[:5]]
        return result

    def analyze_logs(self, log_content: str) -> dict:
        return self.think(f"""Analyze these Proxmox logs:
1. Root cause of errors
2. Hardware issues or patterns  
3. Security anomalies
4. Performance bottlenecks
5. Top 3 fixes with scripts

{log_content[:4000]}""", use_search=True)

    def _heal_loop(self):
        time.sleep(120)
        while True:
            try: self.healer.check()
            except Exception as e: log.warning(f"[heal] {e}")
            time.sleep(1800)

    def is_available(self) -> bool:
        return any(p.available() for p in self.router._provs)

    def get_status(self) -> dict:
        return {
            "active_provider":  self.router.active(),
            "providers":        self.router.status(),
            "cascade":          self.cfg.cascade,
            "web_search":       self.cfg.web_search,
            "auto_execute":     self.cfg.auto_execute,
            "self_heal":        self.cfg.self_heal,
            "chain_of_thought": self.cfg.chain_of_thought,
            "memory_len":       len(self.memory.get()),
            "pending_scripts":  len(self.executor.pending()),
            "available":        self.is_available(),
            "offline_fallback": OllamaProvider(self.cfg).available(),
        }
