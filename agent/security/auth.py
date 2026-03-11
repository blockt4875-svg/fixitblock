"""
FixItBlock — Security Module
Handles admin authentication, sessions, rate limiting, audit logging,
input sanitization, and system hardening checks.
"""

import os
import re
import json
import hmac
import time
import uuid
import hashlib
import logging
import secrets
import threading
from datetime import datetime, timezone, timedelta
from functools import wraps
from typing import Optional

from flask import request, jsonify, g

log = logging.getLogger("fixitblock.security")

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────

ADMIN_PASSWORD_FILE = "/opt/fixitblock/.admin_credentials"
SESSION_FILE        = "/opt/fixitblock/.sessions"
AUDIT_LOG_FILE      = "/var/log/fixitblock-audit.log"
SECRET_KEY_FILE     = "/opt/fixitblock/.secret_key"

SESSION_TTL_HOURS   = int(os.getenv("SESSION_TTL_HOURS", "8"))
MAX_LOGIN_ATTEMPTS  = int(os.getenv("MAX_LOGIN_ATTEMPTS", "5"))
LOCKOUT_MINUTES     = int(os.getenv("LOCKOUT_MINUTES",   "15"))
MIN_PASSWORD_LEN    = 12


# ─────────────────────────────────────────────
#  SECRET KEY
# ─────────────────────────────────────────────

def _load_or_create_secret() -> bytes:
    try:
        if os.path.exists(SECRET_KEY_FILE):
            with open(SECRET_KEY_FILE, "rb") as f:
                key = f.read()
                if len(key) >= 32:
                    return key
        key = secrets.token_bytes(64)
        os.makedirs(os.path.dirname(SECRET_KEY_FILE), exist_ok=True)
        with open(SECRET_KEY_FILE, "wb") as f:
            f.write(key)
        os.chmod(SECRET_KEY_FILE, 0o600)
        return key
    except Exception:
        return secrets.token_bytes(64)

SECRET_KEY = _load_or_create_secret()


# ─────────────────────────────────────────────
#  PASSWORD HASHING (bcrypt-like with PBKDF2)
# ─────────────────────────────────────────────

def _hash_password(password: str) -> str:
    salt   = secrets.token_hex(32)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                  salt.encode("utf-8"), 600_000)
    return f"pbkdf2$sha256$600000${salt}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        parts = stored.split("$")
        if len(parts) != 5 or parts[0] != "pbkdf2":
            return False
        _, algo, iterations, salt, expected = parts
        digest = hashlib.pbkdf2_hmac(
            algo, password.encode("utf-8"),
            salt.encode("utf-8"), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), expected)
    except Exception:
        return False


def _validate_password_strength(password: str) -> list[str]:
    errors = []
    if len(password) < MIN_PASSWORD_LEN:
        errors.append(f"Password must be at least {MIN_PASSWORD_LEN} characters")
    if not re.search(r"[A-Z]", password):
        errors.append("Password must contain at least one uppercase letter")
    if not re.search(r"[a-z]", password):
        errors.append("Password must contain at least one lowercase letter")
    if not re.search(r"\d", password):
        errors.append("Password must contain at least one number")
    if not re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>/?]", password):
        errors.append("Password must contain at least one special character")
    return errors


# ─────────────────────────────────────────────
#  CREDENTIALS STORE
# ─────────────────────────────────────────────

class CredentialsStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        try:
            if os.path.exists(ADMIN_PASSWORD_FILE):
                with open(ADMIN_PASSWORD_FILE, "r") as f:
                    return json.load(f)
        except Exception:
            pass
        return {"users": {}, "initialized": False}

    def _save(self):
        try:
            os.makedirs(os.path.dirname(ADMIN_PASSWORD_FILE), exist_ok=True)
            with open(ADMIN_PASSWORD_FILE, "w") as f:
                json.dump(self._data, f, indent=2)
            os.chmod(ADMIN_PASSWORD_FILE, 0o600)
        except Exception as e:
            log.error(f"Failed to save credentials: {e}")

    def is_initialized(self) -> bool:
        with self._lock:
            return bool(self._data.get("users"))

    def create_user(self, username: str, password: str, role: str = "admin") -> tuple[bool, str]:
        if not username or len(username) < 3:
            return False, "Username must be at least 3 characters"
        if not re.match(r"^[a-zA-Z0-9_\-]+$", username):
            return False, "Username may only contain letters, numbers, _ and -"
        errors = _validate_password_strength(password)
        if errors:
            return False, "; ".join(errors)
        with self._lock:
            if username in self._data.get("users", {}):
                return False, "Username already exists"
            if "users" not in self._data:
                self._data["users"] = {}
            self._data["users"][username] = {
                "password_hash": _hash_password(password),
                "role":          role,
                "created_at":    datetime.now(timezone.utc).isoformat(),
                "last_login":    None,
            }
            self._data["initialized"] = True
            self._save()
        log.info(f"User created: {username} ({role})")
        return True, "User created successfully"

    def verify(self, username: str, password: str) -> tuple[bool, str]:
        with self._lock:
            user = self._data.get("users", {}).get(username)
            if not user:
                return False, "Invalid credentials"
            if not _verify_password(password, user["password_hash"]):
                return False, "Invalid credentials"
            # Update last login
            self._data["users"][username]["last_login"] = datetime.now(timezone.utc).isoformat()
            self._save()
            return True, user.get("role", "admin")

    def _check_password(self, username: str, password: str) -> bool:
        """Verify password without updating last_login (used internally)."""
        with self._lock:
            user = self._data.get("users", {}).get(username)
            if not user: return False
            return _verify_password(password, user["password_hash"])

    def change_password(self, username: str, old_password: str, new_password: str) -> tuple[bool, str]:
        if not self._check_password(username, old_password):
            return False, "Current password is incorrect"
        errors = _validate_password_strength(new_password)
        if errors:
            return False, "; ".join(errors)
        with self._lock:
            self._data["users"][username]["password_hash"] = _hash_password(new_password)
            self._save()
        audit("password_change", username=username)
        return True, "Password changed successfully"

    def list_users(self) -> list[dict]:
        with self._lock:
            return [
                {"username": u, "role": d["role"], "last_login": d.get("last_login")}
                for u, d in self._data.get("users", {}).items()
            ]


credentials = CredentialsStore()


# ─────────────────────────────────────────────
#  SESSION MANAGER
# ─────────────────────────────────────────────

class SessionManager:
    def __init__(self):
        self._sessions: dict[str, dict] = {}
        self._lock = threading.Lock()
        # Cleanup expired sessions every 10 minutes
        t = threading.Thread(target=self._cleanup_loop, daemon=True)
        t.start()

    def create(self, username: str, role: str, ip: str) -> str:
        token = secrets.token_urlsafe(48)
        sig   = hmac.new(SECRET_KEY, token.encode(), hashlib.sha256).hexdigest()
        signed_token = f"{token}.{sig}"

        with self._lock:
            self._sessions[token] = {
                "username":   username,
                "role":       role,
                "ip":         ip,
                "created_at": time.time(),
                "expires_at": time.time() + SESSION_TTL_HOURS * 3600,
                "last_seen":  time.time(),
            }
        audit("login", username=username, ip=ip)
        return signed_token

    def validate(self, signed_token: str) -> Optional[dict]:
        if not signed_token or "." not in signed_token:
            return None
        try:
            token, sig = signed_token.rsplit(".", 1)
            expected = hmac.new(SECRET_KEY, token.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, expected):
                return None
            with self._lock:
                session = self._sessions.get(token)
                if not session:
                    return None
                if time.time() > session["expires_at"]:
                    del self._sessions[token]
                    return None
                session["last_seen"] = time.time()
                return dict(session)
        except Exception:
            return None

    def revoke(self, signed_token: str, username: str = ""):
        try:
            token = signed_token.rsplit(".", 1)[0]
            with self._lock:
                self._sessions.pop(token, None)
            if username:
                audit("logout", username=username)
        except Exception:
            pass

    def revoke_all(self, username: str):
        with self._lock:
            to_del = [t for t, s in self._sessions.items() if s["username"] == username]
            for t in to_del:
                del self._sessions[t]
        audit("revoke_all_sessions", username=username)

    def _cleanup_loop(self):
        while True:
            time.sleep(600)
            now = time.time()
            with self._lock:
                expired = [t for t, s in self._sessions.items() if now > s["expires_at"]]
                for t in expired:
                    del self._sessions[t]
            if expired:
                log.debug(f"Cleaned {len(expired)} expired sessions")

    def active_count(self) -> int:
        with self._lock:
            return len(self._sessions)


sessions = SessionManager()


# ─────────────────────────────────────────────
#  RATE LIMITER
# ─────────────────────────────────────────────

class RateLimiter:
    def __init__(self):
        self._attempts: dict[str, list[float]] = {}
        self._lockouts: dict[str, float]       = {}
        self._lock = threading.Lock()

    def check_login(self, ip: str) -> tuple[bool, str]:
        """Returns (allowed, message)"""
        now = time.time()
        with self._lock:
            # Check lockout
            lockout_until = self._lockouts.get(ip, 0)
            if now < lockout_until:
                remaining = int((lockout_until - now) / 60)
                return False, f"Too many failed attempts. Try again in {remaining} minute(s)."

            # Prune old attempts (outside 10-minute window)
            window  = 600
            self._attempts[ip] = [t for t in self._attempts.get(ip, []) if now - t < window]
            if len(self._attempts[ip]) >= MAX_LOGIN_ATTEMPTS:
                self._lockouts[ip] = now + LOCKOUT_MINUTES * 60
                self._attempts[ip] = []
                audit("account_lockout", ip=ip)
                return False, f"Account locked for {LOCKOUT_MINUTES} minutes after too many failed attempts."

            return True, ""

    def record_failure(self, ip: str):
        with self._lock:
            self._attempts.setdefault(ip, []).append(time.time())

    def record_success(self, ip: str):
        with self._lock:
            self._attempts.pop(ip, None)
            self._lockouts.pop(ip, None)


rate_limiter = RateLimiter()


# ─────────────────────────────────────────────
#  AUDIT LOGGER
# ─────────────────────────────────────────────

_audit_lock = threading.Lock()

def _safe_remote_addr() -> str:
    """Get remote addr safely — works both inside and outside Flask request context."""
    try:
        from flask import has_request_context, request as _req
        if has_request_context():
            return _req.remote_addr or ""
    except Exception:
        pass
    return ""


_audit_dir_ready = False

def audit(event: str, **kwargs):
    """Write a tamper-evident audit log entry."""
    global _audit_dir_ready
    entry = {
        "ts":    datetime.now(timezone.utc).isoformat(),
        "event": event,
        "ip":    kwargs.get("ip", _safe_remote_addr()),
        **{k: v for k, v in kwargs.items() if k != "ip"}
    }
    line = json.dumps(entry)
    # HMAC for integrity
    mac  = hmac.new(SECRET_KEY, line.encode(), hashlib.sha256).hexdigest()[:16]
    full = f"{line} [{mac}]\n"

    with _audit_lock:
        try:
            if not _audit_dir_ready:
                os.makedirs(os.path.dirname(AUDIT_LOG_FILE), exist_ok=True)
                _audit_dir_ready = True
            exists = os.path.exists(AUDIT_LOG_FILE)
            with open(AUDIT_LOG_FILE, "a") as f:
                f.write(full)
            if not exists:
                os.chmod(AUDIT_LOG_FILE, 0o600)  # set perms only on first creation
        except Exception as e:
            log.error(f"Audit log write failed: {e}")

    log.info(f"AUDIT: {event} — {kwargs}")


def get_audit_log(limit: int = 100) -> list[dict]:
    """Read recent audit entries — reads only the last N lines for memory safety."""
    try:
        # Read last (limit * 2) lines to handle blank lines without loading full file
        with open(AUDIT_LOG_FILE, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            chunk = min(size, limit * 300)  # ~300 bytes per line estimate
            f.seek(max(0, size - chunk))
            raw = f.read().decode("utf-8", errors="replace")
        lines = raw.splitlines()
        entries = []
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            if " [" in line:
                line = line[:line.rfind(" [")]
            try:
                entries.append(json.loads(line))
                if len(entries) >= limit:
                    break
            except json.JSONDecodeError:
                pass
        return entries
    except FileNotFoundError:
        return []
    except Exception as e:
        log.warning(f"Audit read failed: {e}")
        return []


# ─────────────────────────────────────────────
#  INPUT SANITIZER
# ─────────────────────────────────────────────

# Dangerous shell patterns to block in any user-provided input
# Shell injection patterns — intentionally excludes | (pipe) since
# AI chat messages legitimately discuss piped commands.
# This is used for non-script inputs like IDs, usernames, queries.
_SHELL_DANGEROUS = re.compile(
    r"(;|&&|\$\(|`|\\x[0-9a-f]{2}|/etc/passwd|/etc/shadow"
    r"|rm\s+-rf\s+/|mkfs\.|dd\s+if=.*of=/dev|chmod\s+777"
    r"|curl\s.*\|\s*ba?sh|wget\s.*\|\s*sh|nc\s+-e"
    r"|python3?\s+-c\s|perl\s+-e\s)",
    re.IGNORECASE
)


def sanitize_input(value: str, allow_path: bool = False) -> tuple[bool, str]:
    """
    Sanitize user input to prevent injection.
    Returns (is_safe, cleaned_value).
    """
    if not isinstance(value, str):
        return False, ""
    # Length limit
    if len(value) > 2048:
        return False, "Input too long"
    # Null bytes
    if "\x00" in value:
        return False, "Invalid input"
    # Shell injection
    if _SHELL_DANGEROUS.search(value):
        return False, "Potentially unsafe input detected"
    if not allow_path:
        # Block path traversal
        if ".." in value or value.startswith("/"):
            return False, "Path traversal not allowed"
    return True, value.strip()


def sanitize_script(script: str) -> tuple[bool, str, list[str]]:
    """
    Validate a script before execution.
    Returns (is_safe, script, warnings).
    """
    warnings = []
    dangerous_patterns = [
        (r"rm\s+-rf\s+/(?!\s)", "Dangerous: recursive delete of root"),
        (r"mkfs\.", "Dangerous: filesystem format command"),
        (r"dd\s+if=.*of=/dev/[sh]d", "Dangerous: raw disk write"),
        (r">\s*/dev/sd", "Dangerous: overwrite disk device"),
        (r"chmod\s+777", "Security: overly permissive chmod"),
        (r"curl.*\|\s*(ba)?sh", "Security: remote code execution pattern"),
        (r"wget.*\|\s*(ba)?sh", "Security: remote code execution pattern"),
        (r"eval\s+\$", "Security: eval with variable expansion"),
    ]
    for pattern, message in dangerous_patterns:
        if re.search(pattern, script, re.IGNORECASE):
            warnings.append(message)

    is_safe = len([w for w in warnings if w.startswith("Dangerous")]) == 0
    return is_safe, script, warnings


# ─────────────────────────────────────────────
#  FLASK DECORATORS
# ─────────────────────────────────────────────

def require_auth(f):
    """Flask decorator — requires valid session token."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = (request.headers.get("X-Auth-Token") or
                 request.cookies.get("fixitblock_session") or
                 request.headers.get("Authorization", "").replace("Bearer ", ""))
        if not token:
            audit("unauthorized_access", path=request.path)
            return jsonify({"error": "Authentication required"}), 401
        session = sessions.validate(token)
        if not session:
            audit("invalid_token", path=request.path)
            return jsonify({"error": "Invalid or expired session"}), 401
        g.session  = session
        g.username = session["username"]
        g.role     = session["role"]
        return f(*args, **kwargs)
    return decorated


def require_admin(f):
    """Flask decorator — requires admin role."""
    @wraps(f)
    @require_auth
    def decorated(*args, **kwargs):
        if g.role != "admin":
            audit("forbidden", username=g.username, path=request.path)
            return jsonify({"error": "Admin privileges required"}), 403
        return f(*args, **kwargs)
    return decorated


def rate_limit_login(f):
    """Flask decorator — rate limits login attempts."""
    @wraps(f)
    def decorated(*args, **kwargs):
        ip = request.remote_addr or "unknown"
        allowed, msg = rate_limiter.check_login(ip)
        if not allowed:
            audit("rate_limited", ip=ip)
            return jsonify({"error": msg}), 429
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────
#  SECURITY HEADERS MIDDLEWARE
# ─────────────────────────────────────────────

def apply_security_headers(response):
    """Add security headers to every response."""
    response.headers["X-Content-Type-Options"]    = "nosniff"
    response.headers["X-Frame-Options"]           = "DENY"
    response.headers["X-XSS-Protection"]          = "1; mode=block"
    response.headers["Referrer-Policy"]           = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"]        = "geolocation=(), microphone=(), camera=()"
    response.headers["Cache-Control"]             = "no-store, no-cache, must-revalidate"
    response.headers["Content-Security-Policy"]   = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "  # inline required for SPA
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://fonts.gstatic.com; "
        "font-src 'self' data: https://fonts.gstatic.com; "
        "img-src 'self' data: blob:; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    # Remove server fingerprint
    response.headers.pop("Server", None)
    response.headers.pop("X-Powered-By", None)
    return response
