"""
FixItBlock — Web API Server  v3
Fully hardened. Auth-gated. AI-powered. Hardware-aware.
"""

import os
import re
import sys
import json
import hashlib
import threading
import subprocess

# Add parent to path so submodules resolve
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, request, send_from_directory, make_response, g
from flask_cors import CORS

from proxmox_agent import agent, log
from security.auth import (
    credentials, sessions, rate_limiter, audit,
    get_audit_log, sanitize_input, sanitize_script,
    apply_security_headers, require_auth, require_admin, rate_limit_login
)
from hardware.profiler import HardwareProfiler
from ai.brain import AIBrain, WebSearchEngine, ai_config

# ─────────────────────────────────────────────
#  APP SETUP
# ─────────────────────────────────────────────

app = Flask(__name__, static_folder="static")
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1MB max request
CORS(app, resources={r"/api/*": {"origins": "*"}},
     expose_headers=["X-Auth-Token"])

# Security headers on every response
app.after_request(apply_security_headers)

# ─────────────────────────────────────────────
#  SUBSYSTEM INITIALIZATION
# ─────────────────────────────────────────────

_connected       = False
_connect_error   = None
_hw_profiler     = HardwareProfiler(ssh_client=None)
_search_engine   = WebSearchEngine(ai_config)
_ai_brain: AIBrain = None
_state_lock      = threading.Lock()  # guards writes to _connected, _ai_brain


def _startup():
    global _connected, _connect_error, _hw_profiler, _ai_brain

    # Connect Proxmox agent
    try:
        agent.connect()
        with _state_lock:
            _connected = True
        log.info("Agent connected")
    except Exception as e:
        with _state_lock:
            _connect_error = str(e)
        log.error(f"Agent connect failed: {e}")

    # Inject SSH into hardware profiler
    _hw_profiler = HardwareProfiler(ssh_client=agent.ssh if _connected else None)

    # Profile hardware in background — updates brain when done
    def profile_hw():
        try:
            profile = _hw_profiler.profile()
            log.info(f"Hardware: {_hw_profiler.summary()}")
            if _ai_brain:
                _ai_brain.update_hardware(profile)
        except Exception as e:
            log.error(f"Hardware profiling failed: {e}")

    threading.Thread(target=profile_hw, daemon=True, name="hw-profiler").start()

    # Initialize AI brain — assign atomically under lock
    brain = AIBrain(
        cfg=ai_config,
        search=_search_engine,
        hardware_profile=_hw_profiler.get(),
        ssh_client=agent.ssh if _connected else None,
    )
    with _state_lock:
        _ai_brain = brain
    log.info(f"AI brain initialized: {ai_config.provider}")

    # Initial scan + scheduler
    if _connected:
        threading.Thread(target=agent.scan, daemon=True, name="initial-scan").start()
        agent.start_scheduler()


threading.Thread(target=_startup, daemon=True, name="startup").start()


# ─────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────

def _require_connected():
    with _state_lock:
        connected = _connected
        err = _connect_error
    if not connected:
        return jsonify({"error": err or "Agent not connected"}), 503
    return None

def _ok(data):
    return jsonify({"success": True, "data": data})

def _err(msg, code=400):
    return jsonify({"success": False, "error": msg}), code

def _sanitize_chat(message: str) -> tuple[bool, str]:
    """
    Light sanitization for AI chat messages.
    Only checks length and null bytes — shell patterns are INTENTIONALLY allowed
    because users legitimately ask the AI about bash syntax, pipes, etc.
    The LLM's own safety filters and the SYSTEM_PROMPT handle the actual guardrails.
    """
    if not isinstance(message, str):
        return False, ""
    if len(message) > 4096:
        return False, "Message too long (max 4096 characters)"
    if "\x00" in message:
        return False, "Invalid input"
    return True, message.strip()


# ─────────────────────────────────────────────
#  AUTH ROUTES (public)
# ─────────────────────────────────────────────

@app.route("/api/auth/status")
def auth_status():
    """Check if admin account is set up."""
    return jsonify({
        "initialized": credentials.is_initialized(),
        "ai_provider": ai_config.provider,
        "ai_available": _ai_brain.is_available() if _ai_brain else False,
    })


@app.route("/api/auth/setup", methods=["POST"])
def auth_setup():
    """First-time admin account setup."""
    if credentials.is_initialized():
        return _err("Admin account already set up. Use /api/auth/change-password.", 409)
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    ok, msg = credentials.create_user(username, password, role="admin")
    if ok:
        audit("admin_account_created", username=username)
        return _ok({"message": msg})
    return _err(msg)


@app.route("/api/auth/login", methods=["POST"])
@rate_limit_login
def auth_login():
    if not credentials.is_initialized():
        return _err("No admin account configured. Visit /setup first.", 403)
    data     = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))
    ip       = request.remote_addr or "unknown"

    ok, result = credentials.verify(username, password)
    if not ok:
        rate_limiter.record_failure(ip)
        audit("login_failed", username=username, ip=ip)
        return _err("Invalid username or password", 401)

    rate_limiter.record_success(ip)
    token = sessions.create(username=username, role=result, ip=ip)
    resp  = make_response(_ok({
        "token":    token,
        "username": username,
        "role":     result,
        "ttl_hours": int(os.getenv("SESSION_TTL_HOURS", "8"))
    }))
    resp.set_cookie("fixitblock_session", token, httponly=True,
                    samesite="Strict", max_age=8*3600)
    return resp


@app.route("/api/auth/logout", methods=["POST"])
@require_auth
def auth_logout():
    token = (request.headers.get("X-Auth-Token") or
             request.cookies.get("fixitblock_session", ""))
    sessions.revoke(token, username=g.username)
    resp = make_response(_ok({"message": "Logged out"}))
    resp.delete_cookie("fixitblock_session")
    return resp


@app.route("/api/auth/change-password", methods=["POST"])
@require_auth
def change_password():
    data     = request.get_json(silent=True) or {}
    old_pass = str(data.get("old_password", ""))
    new_pass = str(data.get("new_password", ""))
    ok, msg  = credentials.change_password(g.username, old_pass, new_pass)
    if ok:
        sessions.revoke_all(g.username)
        return _ok({"message": msg + " — please log in again."})
    return _err(msg)


@app.route("/api/auth/audit")
@require_admin
def audit_log():
    limit   = min(int(request.args.get("limit", 100)), 500)
    entries = get_audit_log(limit)
    return jsonify(entries)


# ─────────────────────────────────────────────
#  CORE AGENT ROUTES (auth required)
# ─────────────────────────────────────────────

@app.route("/api/status")
@require_auth
def status():
    return jsonify({
        "connected":     _connected,
        "connect_error": _connect_error,
        "summary":       agent.get_summary() if _connected else {},
        "hardware":      _hw_profiler.summary(),
        "ai":            _ai_brain.get_status() if _ai_brain else {},
    })


@app.route("/api/scan", methods=["POST"])
@require_auth
def trigger_scan():
    err = _require_connected()
    if err: return err
    if agent.get_summary().get("scanning", False):
        return jsonify({"message": "Scan already in progress"}), 202
    audit("manual_scan", username=g.username)
    threading.Thread(target=agent.scan, daemon=True, name="manual-scan").start()
    return jsonify({"message": "Scan triggered"})


@app.route("/api/issues")
@require_auth
def get_issues():
    err = _require_connected()
    if err: return err
    category = request.args.get("category")
    severity = request.args.get("severity")
    issues   = agent.get_issues()
    if category: issues = [i for i in issues if i["category"] == category]
    if severity: issues = [i for i in issues if i["severity"] == severity]
    sev_order = {"critical": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda x: (sev_order.get(x["severity"], 9), x["timestamp"]))
    return jsonify(issues)


@app.route("/api/issues/<issue_id>/fix", methods=["POST"])
@require_auth
def fix_issue(issue_id):
    err = _require_connected()
    if err: return err
    safe, cleaned = sanitize_input(issue_id, allow_path=False)
    if not safe:
        return _err("Invalid issue ID")
    audit("fix_issue", username=g.username, issue_id=issue_id)
    success, message = agent.fix_issue(issue_id)
    return jsonify({"success": success, "message": message}), (200 if success else 500)


@app.route("/api/fix-all", methods=["POST"])
@require_auth
def fix_all():
    err = _require_connected()
    if err: return err
    audit("fix_all", username=g.username)
    results = agent.fix_all()
    fixed   = sum(1 for r in results if r["success"])
    return jsonify({"fixed": fixed, "total": len(results), "results": results})


@app.route("/api/history")
@require_auth
def get_history():
    return jsonify(agent.get_history())


@app.route("/api/config")
@require_auth
def get_config():
    cfg = agent.config
    return jsonify({
        "host":            cfg.host,
        "node":            cfg.node,
        "auto_fix":        cfg.auto_fix,
        "scan_interval":   cfg.scan_interval,
        "disk_warn_pct":   cfg.disk_warn_pct,
        "disk_crit_pct":   cfg.disk_crit_pct,
        "mem_warn_pct":    cfg.mem_warn_pct,
        "mem_crit_pct":    cfg.mem_crit_pct,
        "log_max_days":    cfg.log_max_days,
        "snap_max_days":   cfg.snap_max_days,
        "backup_max_days": cfg.backup_max_days,
    })


# ─────────────────────────────────────────────
#  HARDWARE ROUTES
# ─────────────────────────────────────────────

@app.route("/api/hardware")
@require_auth
def hardware_profile():
    return jsonify(_hw_profiler.get())


@app.route("/api/hardware/refresh", methods=["POST"])
@require_auth
def hardware_refresh():
    audit("hardware_refresh", username=g.username)
    threading.Thread(target=_hw_profiler.profile, daemon=True, name="hw-refresh").start()
    return jsonify({"message": "Hardware profile refresh started"})


@app.route("/api/hardware/summary")
@require_auth
def hardware_summary():
    return jsonify({"summary": _hw_profiler.summary()})


# ─────────────────────────────────────────────
#  AI ROUTES
# ─────────────────────────────────────────────

@app.route("/api/ai/chat", methods=["POST"])
@require_auth
def ai_chat():
    if not _ai_brain:
        return _err("AI brain not initialized", 503)
    data    = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()

    if not message:
        return _err("Message is required")
    safe, cleaned = _sanitize_chat(message)
    if not safe:
        return _err(f"Invalid message: {cleaned}")

    audit("ai_chat", username=g.username, message_len=len(message))

    issues = agent.get_issues() if _connected else []
    result = _ai_brain.think(
        cleaned,
        issues=issues,
        use_search=data.get("use_search", True)
    )
    return jsonify(result)


@app.route("/api/ai/generate-fix", methods=["POST"])
@require_auth
def ai_generate_fix():
    if not _ai_brain:
        return _err("AI brain not initialized", 503)
    data     = request.get_json(silent=True) or {}
    issue_id = str(data.get("issue_id", ""))
    issues   = {i["id"]: i for i in agent.get_issues()}
    issue    = issues.get(issue_id)
    if not issue:
        return _err("Issue not found", 404)

    audit("ai_generate_fix", username=g.username, issue_id=issue_id)
    result = _ai_brain.generate_fix_script(issue, _hw_profiler.get())
    return jsonify(result)


@app.route("/api/ai/search-scripts", methods=["POST"])
@require_auth
def ai_search_scripts():
    if not _ai_brain:
        return _err("AI brain not initialized", 503)
    data  = request.get_json(silent=True) or {}
    task  = str(data.get("task", "")).strip()
    if not task:
        return _err("Task description is required")
    safe, cleaned = _sanitize_chat(task)
    if not safe:
        return _err(f"Invalid task: {cleaned}")

    audit("ai_search_scripts", username=g.username, task=task)
    result = _ai_brain.search_scripts_for(cleaned)
    return jsonify(result)


@app.route("/api/ai/analyze-logs", methods=["POST"])
@require_auth
def ai_analyze_logs():
    if not _ai_brain:
        return _err("AI brain not initialized", 503)
    data        = request.get_json(silent=True) or {}
    log_content = str(data.get("logs", ""))[:5000]

    if not log_content and _connected and agent.ssh:
        # Fetch live logs from node
        rc, out, _ = agent.ssh.run(
            "journalctl --since '1 hour ago' -p err --no-pager -q 2>/dev/null | tail -100"
        )
        log_content = out if rc == 0 else "No logs available"

    audit("ai_analyze_logs", username=g.username)
    result = _ai_brain.analyze_logs(log_content)
    return jsonify(result)


@app.route("/api/ai/status")
@require_auth
def ai_status():
    if not _ai_brain:
        return jsonify({"available": False, "error": "Not initialized"})
    return jsonify(_ai_brain.get_status())


@app.route("/api/ai/clear-memory", methods=["POST"])
@require_auth
def ai_clear_memory():
    if _ai_brain:
        _ai_brain.memory.clear()
        audit("ai_memory_cleared", username=g.username)
    return _ok({"message": "Conversation memory cleared"})


@app.route("/api/ai/search", methods=["POST"])
@require_auth
def ai_search():
    """Direct web search endpoint."""
    data  = request.get_json(silent=True) or {}
    query = str(data.get("query", "")).strip()
    if not query:
        return _err("Query is required")
    safe, cleaned = sanitize_input(query)
    if not safe:
        return _err(f"Unsafe query: {cleaned}")

    audit("web_search", username=g.username, query=query)
    results = _search_engine.search(cleaned, num=8)
    return jsonify({"results": results, "query": cleaned})


# ─────────────────────────────────────────────
#  SCRIPT EXECUTION (admin only, hardened)
# ─────────────────────────────────────────────

@app.route("/api/scripts/execute", methods=["POST"])
@require_admin
def execute_script():
    """Execute a bash script on the Proxmox host via SSH. Admin only."""
    data   = request.get_json(silent=True) or {}
    script = str(data.get("script", ""))
    dry_run = bool(data.get("dry_run", True))  # dry run by default

    if not script.strip():
        return _err("Script content is required")

    is_safe, script, warnings = sanitize_script(script)

    if not is_safe and not data.get("force", False):
        return jsonify({
            "success": False,
            "blocked": True,
            "warnings": warnings,
            "message": "Script blocked due to dangerous patterns. Set force=true to override (not recommended)."
        }), 400

    if dry_run:
        return jsonify({
            "success": True,
            "dry_run": True,
            "warnings": warnings,
            "message": "Dry run — script NOT executed. Set dry_run=false to run.",
            "script_lines": len(script.splitlines()),
        })

    audit("script_execute", username=g.username,
          script_hash=hashlib.sha256(script.encode()).hexdigest()[:16],
          lines=len(script.splitlines()), warnings=warnings)

    err = _require_connected()
    if err: return err

    # Execute via SSH
    rc, out, err_out = agent.ssh.run(script, timeout=120)
    return jsonify({
        "success":  rc == 0,
        "exit_code": rc,
        "stdout":   out[:5000],
        "stderr":   err_out[:2000],
        "warnings": warnings,
    })


# ─────────────────────────────────────────────
#  AUTONOMOUS EXECUTION — APPROVAL QUEUE
# ─────────────────────────────────────────────

@app.route("/api/ai/pending-scripts")
@require_auth
def pending_scripts():
    if not _ai_brain:
        return jsonify([])
    return jsonify(_ai_brain.executor.pending())


@app.route("/api/ai/approve-script/<script_id>", methods=["POST"])
@require_admin
def approve_script(script_id: str):
    if not _ai_brain:
        return _err("AI brain not initialized", 503)
    audit("script_approved", username=g.username, script_id=script_id)
    result = _ai_brain.executor.approve(script_id)
    return jsonify(result)


@app.route("/api/ai/reject-script/<script_id>", methods=["POST"])
@require_admin
def reject_script(script_id: str):
    if not _ai_brain:
        return _err("AI brain not initialized", 503)
    audit("script_rejected", username=g.username, script_id=script_id)
    result = _ai_brain.executor.reject(script_id)
    return jsonify(result)


@app.route("/api/ai/execution-history")
@require_auth
def execution_history():
    if not _ai_brain:
        return jsonify([])
    limit = min(int(request.args.get("limit", 50)), 200)
    return jsonify(_ai_brain.executor.history()[-limit:])


# ─────────────────────────────────────────────
#  SELF-HEAL
# ─────────────────────────────────────────────

@app.route("/api/ai/self-heal", methods=["POST"])
@require_admin
def trigger_self_heal():
    if not _ai_brain:
        return _err("AI brain not initialized", 503)
    audit("self_heal_triggered", username=g.username)
    results = _ai_brain.healer.check()
    return jsonify({"results": results, "count": len(results)})


@app.route("/api/ai/self-heal/log")
@require_auth
def self_heal_log():
    if not _ai_brain:
        return jsonify([])
    return jsonify(_ai_brain.healer.get_log())


# ─────────────────────────────────────────────
#  OLLAMA MANAGEMENT (offline AI)
# ─────────────────────────────────────────────

@app.route("/api/ai/ollama/models")
@require_auth
def ollama_models():
    from ai.brain import OllamaProvider
    prov   = OllamaProvider(ai_config)
    models = prov.list_models()
    active = ai_config.ollama_model
    return jsonify({
        "available": prov.available(),
        "models":    models,
        "active":    active,
        "host":      ai_config.ollama_host,
    })


@app.route("/api/ai/ollama/pull", methods=["POST"])
@require_admin
def ollama_pull():
    from ai.brain import OllamaProvider
    data  = request.get_json(silent=True) or {}
    model = str(data.get("model", ai_config.ollama_model))
    # Validate model name
    if not re.match(r"^[a-zA-Z0-9._:\-]+$", model):
        return _err("Invalid model name")
    audit("ollama_pull", username=g.username, model=model)

    def _pull():
        prov = OllamaProvider(ai_config, model_override=model)
        prov.pull(model)

    threading.Thread(target=_pull, daemon=True, name=f"ollama-pull-{model}").start()
    return _ok({"message": f"Pulling model '{model}' in background"})


@app.route("/api/ai/provider-status")
@require_auth
def provider_status():
    if not _ai_brain:
        return jsonify({"error": "AI not initialized"})
    return jsonify({
        "status":   _ai_brain.router.status(),
        "active":   _ai_brain.router.active(),
        "cascade":  ai_config.cascade,
        "online":   _ai_brain._is_online(),
    })


# ─────────────────────────────────────────────
#  HEALTH / LIVENESS
# ─────────────────────────────────────────────

@app.route("/health")
def health():
    """Unauthenticated liveness check for monitoring and load balancers."""
    ai_ok = _ai_brain is not None and _ai_brain.is_available()
    return jsonify({
        "status":    "ok" if _connected else "starting",
        "connected": _connected,
        "ai_ready":  ai_ok,
        "version":   "3.2",
    }), 200 if _connected else 503


# ─────────────────────────────────────────────
#  STATIC / SPA
# ─────────────────────────────────────────────

@app.route("/")
@app.route("/<path:path>")
def serve(path=""):
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if path:
        # Guard against path traversal (e.g. ../../etc/passwd)
        safe = os.path.realpath(os.path.join(static_dir, path))
        if safe.startswith(os.path.realpath(static_dir) + os.sep) and os.path.isfile(safe):
            return send_from_directory(static_dir, path)
    return send_from_directory(static_dir, "index.html")


if __name__ == "__main__":
    port = int(os.getenv("WEB_PORT", "7070"))
    # Use threaded=True for concurrent requests
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
