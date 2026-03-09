"""
Proxmox Maintenance Agent — Core Engine  v2
Handles API + SSH connections, issue detection, and auto-remediation.
"""

import os
import re
import time
import uuid
import logging
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional, Callable

import requests
import paramiko
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────

log = logging.getLogger("fixitblock")
log.setLevel(logging.INFO)

_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
_sh  = logging.StreamHandler()
_sh.setFormatter(_fmt)
log.addHandler(_sh)

try:
    _fh = logging.FileHandler("/var/log/fixitblock.log", mode="a")
    _fh.setFormatter(_fmt)
    log.addHandler(_fh)
except OSError:
    pass  # no write access in dev/CLI mode


# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────

class Config:
    def __init__(self):
        self.host          = os.getenv("PROXMOX_HOST", "").strip()
        self.port          = int(os.getenv("PROXMOX_PORT", "8006"))
        self.user          = os.getenv("PROXMOX_USER", "root@pam")
        self.password      = os.getenv("PROXMOX_PASSWORD", "")
        self.token_name    = os.getenv("PROXMOX_TOKEN_NAME", "")
        self.token_value   = os.getenv("PROXMOX_TOKEN_VALUE", "")
        self.ssh_host      = os.getenv("SSH_HOST", self.host).strip()
        self.ssh_port      = int(os.getenv("SSH_PORT", "22"))
        self.ssh_user      = os.getenv("SSH_USER", "root")
        self.ssh_password  = os.getenv("SSH_PASSWORD", "")
        self.ssh_key       = os.getenv("SSH_KEY_PATH", "/root/.ssh/id_rsa")
        self.node          = os.getenv("PROXMOX_NODE", "pve")
        self.auto_fix      = os.getenv("AUTO_FIX", "false").lower() == "true"
        self.scan_interval = max(30, int(os.getenv("SCAN_INTERVAL", "300")))
        # Thresholds
        self.disk_warn_pct   = float(os.getenv("DISK_WARN_PCT",   "75"))
        self.disk_crit_pct   = float(os.getenv("DISK_CRIT_PCT",   "90"))
        self.mem_warn_pct    = float(os.getenv("MEM_WARN_PCT",    "80"))
        self.mem_crit_pct    = float(os.getenv("MEM_CRIT_PCT",    "95"))
        self.cpu_warn_pct    = float(os.getenv("CPU_WARN_PCT",    "85"))
        self.log_max_days    = int(os.getenv("LOG_MAX_DAYS",      "14"))
        self.snap_max_days   = int(os.getenv("SNAP_MAX_DAYS",     "30"))
        self.backup_max_days = int(os.getenv("BACKUP_MAX_DAYS",   "7"))

    def validate(self) -> list[str]:
        errors = []
        if not self.host:
            errors.append("PROXMOX_HOST is required")
        if not self.token_value and not self.password:
            errors.append("Either PROXMOX_PASSWORD or PROXMOX_TOKEN_VALUE is required")
        return errors


config = Config()


# ─────────────────────────────────────────────
#  PROXMOX API CLIENT
# ─────────────────────────────────────────────

class ProxmoxAPI:
    def __init__(self, cfg: Config):
        self.cfg     = cfg
        self.base    = f"https://{cfg.host}:{cfg.port}/api2/json"
        self.session = requests.Session()
        self.session.verify = False
        self._connected  = False
        self._use_token  = False
        self._ticket_exp: Optional[datetime] = None

    def connect(self):
        cfg = self.cfg
        if cfg.token_name and cfg.token_value:
            auth = f"PVEAPIToken={cfg.user}!{cfg.token_name}={cfg.token_value}"
            self.session.headers.update({"Authorization": auth})
            self._use_token = True
            self._connected = True
            log.info("API: connected via token")
        else:
            self._login()

    def _login(self):
        """Password login — ticket valid 2 h; we refresh at 1 h 50 m."""
        cfg = self.cfg
        r = self.session.post(f"{self.base}/access/ticket", data={
            "username": cfg.user,
            "password": cfg.password,
        }, timeout=10)
        r.raise_for_status()
        data = r.json()["data"]
        self.session.cookies.set("PVEAuthCookie", data["ticket"])
        self.session.headers.update({"CSRFPreventionToken": data["CSRFPreventionToken"]})
        self._ticket_exp = datetime.now(timezone.utc) + timedelta(hours=1, minutes=50)
        self._connected  = True
        log.info("API: connected via password")

    def _ensure_auth(self):
        if not self._use_token and self._ticket_exp:
            if datetime.now(timezone.utc) >= self._ticket_exp:
                log.info("API ticket expiring — re-authenticating")
                self._login()

    def get(self, path: str):
        self._ensure_auth()
        r = self.session.get(f"{self.base}{path}", timeout=15)
        r.raise_for_status()
        return r.json().get("data", r.json())

    def post(self, path: str, data: dict | None = None):
        self._ensure_auth()
        r = self.session.post(f"{self.base}{path}", json=data or {}, timeout=15)
        r.raise_for_status()
        return r.json().get("data", r.json())

    def delete(self, path: str):
        self._ensure_auth()
        r = self.session.delete(f"{self.base}{path}", timeout=15)
        r.raise_for_status()
        return r.json().get("data", {})

    @property
    def is_connected(self) -> bool:
        return self._connected


# ─────────────────────────────────────────────
#  SSH CLIENT  (auto-reconnect)
# ─────────────────────────────────────────────

class SSHClient:
    def __init__(self, cfg: Config):
        self.cfg    = cfg
        self._lock  = threading.Lock()
        self._client: Optional[paramiko.SSHClient] = None

    def _make_client(self) -> paramiko.SSHClient:
        c = paramiko.SSHClient()
        c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs: dict = {
            "hostname":       self.cfg.ssh_host,
            "port":           self.cfg.ssh_port,
            "username":       self.cfg.ssh_user,
            "timeout":        10,
            "banner_timeout": 15,
        }
        if self.cfg.ssh_key and os.path.exists(self.cfg.ssh_key):
            kwargs["key_filename"] = self.cfg.ssh_key
        elif self.cfg.ssh_password:
            kwargs["password"] = self.cfg.ssh_password
        c.connect(**kwargs)
        return c

    def connect(self):
        with self._lock:
            self._client = self._make_client()
        log.info(f"SSH: connected to {self.cfg.ssh_host}")

    def run(self, cmd: str, timeout: int = 30) -> tuple[int, str, str]:
        with self._lock:
            # Lazy connect + auto-reconnect if transport is dead
            if self._client is None:
                try:
                    self._client = self._make_client()
                except Exception as e:
                    return -1, "", f"SSH connect failed: {e}"
            else:
                transport = self._client.get_transport()
                if transport is None or not transport.is_active():
                    try:
                        self._client = self._make_client()
                    except Exception as e:
                        return -1, "", f"SSH reconnect failed: {e}"

            try:
                _, stdout, stderr = self._client.exec_command(cmd, timeout=timeout)
                rc  = stdout.channel.recv_exit_status()
                out = stdout.read().decode("utf-8", errors="replace").strip()
                err = stderr.read().decode("utf-8", errors="replace").strip()
                return rc, out, err
            except Exception as e:
                log.warning(f"SSH exec failed ({cmd[:60]}…): {e}")
                self._client = None
                return -1, "", str(e)

    def close(self):
        with self._lock:
            if self._client:
                self._client.close()
                self._client = None


# ─────────────────────────────────────────────
#  ISSUE MODEL
# ─────────────────────────────────────────────

class Issue:
    def __init__(
        self,
        category:  str,
        severity:  str,
        title:     str,
        description: str,
        fix_fn:    Optional[Callable] = None,
        fix_label: Optional[str]      = None,
    ):
        self.id          = uuid.uuid4().hex[:12]
        self.category    = category
        self.severity    = severity
        self.title       = title
        self.description = description
        self.fix_fn      = fix_fn
        self.fix_label   = fix_label
        self.fixed       = False
        self.fix_output: Optional[str] = None
        self.timestamp   = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict:
        return {
            "id":          self.id,
            "category":    self.category,
            "severity":    self.severity,
            "title":       self.title,
            "description": self.description,
            "fixable":     self.fix_fn is not None,
            "fix_label":   self.fix_label,
            "fixed":       self.fixed,
            "fix_output":  self.fix_output,
            "timestamp":   self.timestamp,
        }

    def apply_fix(self) -> tuple[bool, str]:
        if not self.fix_fn:
            return False, "No automated fix available"
        if self.fixed:
            return True, self.fix_output or "Already fixed"
        try:
            result          = self.fix_fn()
            self.fixed      = True
            self.fix_output = result or "Fix applied successfully"
            log.info(f"[FIX OK]   {self.title}: {self.fix_output}")
            return True, self.fix_output
        except Exception as e:
            self.fix_output = f"Fix failed: {e}"
            log.error(f"[FIX FAIL] {self.title}: {e}")
            return False, self.fix_output


# ─────────────────────────────────────────────
#  CHECKERS
# ─────────────────────────────────────────────

class DiskChecker:
    def __init__(self, api: ProxmoxAPI, ssh: SSHClient, cfg: Config):
        self.api = api
        self.ssh = ssh
        self.cfg = cfg

    def check(self) -> list[Issue]:
        issues: list[Issue] = []
        node = self.cfg.node

        # Storage pools via API
        try:
            for s in self.api.get(f"/nodes/{node}/storage"):
                total = s.get("total", 0)
                used  = s.get("used",  0)
                if total == 0:
                    continue
                pct    = (used / total) * 100
                name   = s.get("storage", "unknown")
                ug, tg = used / 1e9, total / 1e9
                if pct >= self.cfg.disk_crit_pct:
                    issues.append(Issue(
                        "disk", "critical",
                        f"Storage '{name}' critically full ({pct:.1f}%)",
                        f"Used {ug:.1f} GB of {tg:.1f} GB. Immediate action required.",
                        fix_fn=lambda n=name: self._cleanup_unused_volumes(n),
                        fix_label=f"Remove unused disk images on {name}",
                    ))
                elif pct >= self.cfg.disk_warn_pct:
                    issues.append(Issue(
                        "disk", "warning",
                        f"Storage '{name}' running low ({pct:.1f}%)",
                        f"Used {ug:.1f} GB of {tg:.1f} GB.",
                        fix_fn=lambda n=name: self._cleanup_unused_volumes(n),
                        fix_label=f"Remove unused disk images on {name}",
                    ))
        except Exception as e:
            log.warning(f"DiskChecker/API: {e}")

        # Filesystem usage via SSH
        try:
            rc, out, _ = self.ssh.run(
                "df --output=source,pcent,target -x tmpfs -x devtmpfs 2>/dev/null | tail -n +2"
            )
            if rc == 0:
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    src, pct_str, mnt = parts[0], parts[1].rstrip("%"), parts[2]
                    try:
                        pct = float(pct_str)
                    except ValueError:
                        continue
                    if mnt in ("/dev", "/run", "/dev/shm"):
                        continue
                    if pct >= self.cfg.disk_crit_pct:
                        issues.append(Issue(
                            "disk", "critical",
                            f"Filesystem {mnt} critically full ({pct:.0f}%)",
                            f"Device: {src}",
                            fix_fn=lambda m=mnt: self._clean_filesystem(m),
                            fix_label=f"Clean logs/tmp/apt on {mnt}",
                        ))
                    elif pct >= self.cfg.disk_warn_pct:
                        issues.append(Issue(
                            "disk", "warning",
                            f"Filesystem {mnt} running low ({pct:.0f}%)",
                            f"Device: {src}",
                        ))
        except Exception as e:
            log.warning(f"DiskChecker/SSH: {e}")

        return issues

    def _cleanup_unused_volumes(self, storage: str) -> str:
        rc, out, _ = self.ssh.run(
            f"pvesm list {storage} 2>/dev/null | awk 'NR>1 && /unused/ {{print $1}}'"
        )
        cleaned = []
        if rc == 0 and out:
            for vol in out.splitlines():
                vol = vol.strip()
                if vol:
                    rc2, _, _ = self.ssh.run(f"pvesm free '{vol}' 2>/dev/null")
                    if rc2 == 0:
                        cleaned.append(vol.split("/")[-1])
        return f"Removed {len(cleaned)} unused volume(s)" + (
            f": {', '.join(cleaned[:5])}" if cleaned else " (none found)"
        )

    def _clean_filesystem(self, mnt: str) -> str:
        steps = [
            ("apt-get clean -y 2>/dev/null",                        "apt cache"),
            ("journalctl --vacuum-time=7d 2>/dev/null",             "journal"),
            ("find /tmp -type f -atime +7 -delete 2>/dev/null",     "old /tmp files"),
            ("find /var/log -name '*.gz' -delete 2>/dev/null",      "compressed logs"),
            ("find /var/log -name '*.1'  -delete 2>/dev/null",      "rotated logs"),
        ]
        done = []
        for cmd, label in steps:
            rc, _, _ = self.ssh.run(cmd, timeout=60)
            if rc == 0:
                done.append(label)
        return "Cleaned: " + (", ".join(done) if done else "nothing found")


class VMChecker:
    def __init__(self, api: ProxmoxAPI, ssh: SSHClient, cfg: Config):
        self.api = api
        self.ssh = ssh
        self.cfg = cfg

    def check(self) -> list[Issue]:
        issues: list[Issue] = []
        node = self.cfg.node

        # Node health
        try:
            ns   = self.api.get(f"/nodes/{node}/status")
            cpu  = ns.get("cpu", 0) * 100
            mem  = ns.get("memory", {})
            mpct = (mem.get("used", 0) / max(mem.get("total", 1), 1)) * 100
            load = ns.get("loadavg", [0, 0, 0])

            if cpu >= self.cfg.cpu_warn_pct:
                issues.append(Issue(
                    "vm", "warning",
                    f"Node CPU high ({cpu:.1f}%)",
                    f"1-min load average: {load[0]}",
                ))
            if mpct >= self.cfg.mem_crit_pct:
                issues.append(Issue(
                    "vm", "critical",
                    f"Node memory critically high ({mpct:.1f}%)",
                    f"Used {mem.get('used',0)/1e9:.1f} GB / {mem.get('total',1)/1e9:.1f} GB",
                    fix_fn=self._drop_caches,
                    fix_label="Sync and drop page/slab caches",
                ))
            elif mpct >= self.cfg.mem_warn_pct:
                issues.append(Issue(
                    "vm", "warning",
                    f"Node memory high ({mpct:.1f}%)",
                    f"Used {mem.get('used',0)/1e9:.1f} GB / {mem.get('total',1)/1e9:.1f} GB",
                    fix_fn=self._drop_caches,
                    fix_label="Drop page/slab caches",
                ))
        except Exception as e:
            log.warning(f"VMChecker/node: {e}")

        # QEMU VMs
        try:
            for vm in self.api.get(f"/nodes/{node}/qemu"):
                vmid   = vm.get("vmid")
                name   = vm.get("name", f"vm-{vmid}")
                status = vm.get("status", "unknown")
                if status == "stopped":
                    issues.append(Issue("vm", "info",
                        f"VM '{name}' ({vmid}) is stopped", "VM is not running."))
                    continue
                if status == "running":
                    try:
                        st   = self.api.get(f"/nodes/{node}/qemu/{vmid}/status/current")
                        cpu  = st.get("cpu", 0) * 100
                        mu   = st.get("mem", 0)
                        mmax = st.get("maxmem", 1)
                        mpct = (mu / max(mmax, 1)) * 100
                        if cpu >= self.cfg.cpu_warn_pct:
                            issues.append(Issue("vm", "warning",
                                f"VM '{name}' ({vmid}) high CPU ({cpu:.1f}%)",
                                "Sustained CPU usage above threshold."))
                        if mpct >= self.cfg.mem_crit_pct:
                            issues.append(Issue("vm", "critical",
                                f"VM '{name}' ({vmid}) critically low memory ({mpct:.1f}%)",
                                f"{mu/1e9:.1f} GB / {mmax/1e9:.1f} GB"))
                        elif mpct >= self.cfg.mem_warn_pct:
                            issues.append(Issue("vm", "warning",
                                f"VM '{name}' ({vmid}) high memory ({mpct:.1f}%)",
                                f"{mu/1e9:.1f} GB / {mmax/1e9:.1f} GB"))
                    except Exception:
                        pass
        except Exception as e:
            log.warning(f"VMChecker/QEMU: {e}")

        # LXC containers
        try:
            for ct in self.api.get(f"/nodes/{node}/lxc"):
                ctid, name = ct.get("vmid"), ct.get("name", f"ct-{ct.get('vmid')}")
                if ct.get("status") == "stopped":
                    issues.append(Issue("vm", "info",
                        f"Container '{name}' ({ctid}) is stopped", "LXC container is not running."))
        except Exception as e:
            log.warning(f"VMChecker/LXC: {e}")

        return issues

    def _drop_caches(self) -> str:
        rc, _, err = self.ssh.run("sync && echo 3 > /proc/sys/vm/drop_caches")
        return "Page and slab caches dropped" if rc == 0 else f"Failed: {err}"


class BackupChecker:
    def __init__(self, api: ProxmoxAPI, ssh: SSHClient, cfg: Config):
        self.api = api
        self.ssh = ssh
        self.cfg = cfg

    def check(self) -> list[Issue]:
        issues: list[Issue] = []
        node   = self.cfg.node
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.cfg.backup_max_days)

        try:
            storages      = self.api.get(f"/nodes/{node}/storage")
            backup_stores = [s["storage"] for s in storages if "backup" in s.get("content", "")]

            if not backup_stores:
                issues.append(Issue("backup", "warning",
                    "No backup storage configured",
                    "No storage pools have 'backup' as a content type."))
                return issues

            for store in backup_stores:
                try:
                    items = self.api.get(f"/nodes/{node}/storage/{store}/content?content=backup")
                    if not items:
                        issues.append(Issue("backup", "warning",
                            f"No backups found in '{store}'",
                            "Storage is configured for backups but is empty."))
                        continue

                    stale: list[tuple[str, str]] = []
                    for bk in items:
                        bk_date = datetime.fromtimestamp(bk.get("ctime", 0), tz=timezone.utc)
                        if bk_date < cutoff:
                            stale.append((bk.get("volid", ""), bk_date.strftime("%Y-%m-%d")))

                    if stale:
                        vols  = [v[0] for v in stale]
                        dates = sorted({v[1] for v in stale})
                        issues.append(Issue(
                            "backup", "info",
                            f"{len(stale)} old backup(s) in '{store}' (>{self.cfg.backup_max_days}d)",
                            f"Date range: {dates[0]} → {dates[-1]}",
                            fix_fn=lambda v=vols, s=store: self._delete_old_backups(v, s, node),
                            fix_label=f"Delete {len(stale)} backup(s) older than {self.cfg.backup_max_days} days",
                        ))
                except Exception as e:
                    log.warning(f"BackupChecker/store '{store}': {e}")

        except Exception as e:
            log.warning(f"BackupChecker: {e}")

        return issues

    def _delete_old_backups(self, volids: list[str], storage: str, node: str) -> str:
        deleted, failed = 0, 0
        for vol in volids:
            try:
                self.api.delete(f"/nodes/{node}/storage/{storage}/content/{vol}")
                deleted += 1
            except Exception as e:
                log.warning(f"Delete backup {vol}: {e}")
                failed += 1
        return f"Deleted {deleted} backup(s)" + (f"; {failed} failed" if failed else "")


class LogChecker:
    def __init__(self, api: ProxmoxAPI, ssh: SSHClient, cfg: Config):
        self.api = api
        self.ssh = ssh
        self.cfg = cfg

    def check(self) -> list[Issue]:
        issues: list[Issue] = []

        # Oversized log files
        try:
            rc, out, _ = self.ssh.run(
                "find /var/log -type f \\( -name '*.log' -o -name 'syslog' -o -name 'messages' \\)"
                " -size +100M 2>/dev/null | head -20"
            )
            if rc == 0 and out:
                files = [f.strip() for f in out.splitlines() if f.strip()]
                issues.append(Issue(
                    "log", "warning",
                    f"{len(files)} oversized log file(s) (>100 MB each)",
                    "Files: " + ", ".join(files[:4]) + ("…" if len(files) > 4 else ""),
                    fix_fn=lambda f=files: self._truncate_logs(f),
                    fix_label=f"Truncate {len(files)} log file(s) and force logrotate",
                ))
        except Exception as e:
            log.warning(f"LogChecker/oversized: {e}")

        # Journal disk usage
        try:
            rc, out, _ = self.ssh.run("journalctl --disk-usage 2>/dev/null")
            if rc == 0 and out:
                m = re.search(r"([\d.]+)\s*(G|M)iB", out)
                if m:
                    size, unit = float(m.group(1)), m.group(2)
                    if (size if unit == "G" else size / 1024) > 1:
                        issues.append(Issue(
                            "log", "warning",
                            f"Journal using {size:.1f} {unit}iB of disk space",
                            f"Run vacuum to retain only the last {self.cfg.log_max_days} days.",
                            fix_fn=lambda d=self.cfg.log_max_days: self._vacuum_journal(d),
                            fix_label=f"Vacuum journal — keep last {self.cfg.log_max_days} days",
                        ))
        except Exception as e:
            log.warning(f"LogChecker/journal: {e}")

        # Critical kernel/OOM errors
        try:
            pattern = r"CRITICAL|kernel: BUG|Out of memory: Kill|I/O error"
            rc, out, _ = self.ssh.run(
                f"grep -cEi '{pattern}' /var/log/syslog 2>/dev/null || echo 0"
            )
            if rc == 0:
                count = int(out.strip() or "0")
                if count > 0:
                    _, sample, _ = self.ssh.run(
                        f"grep -Ei '{pattern}' /var/log/syslog 2>/dev/null | tail -3"
                    )
                    issues.append(Issue(
                        "log", "critical",
                        f"{count} critical kernel/OOM error(s) in syslog",
                        sample or "See /var/log/syslog for details.",
                    ))
        except Exception as e:
            log.warning(f"LogChecker/syslog: {e}")

        # PVE daemon errors
        try:
            rc, out, _ = self.ssh.run(
                "journalctl -u pveproxy -u pvedaemon -u pve-cluster"
                " --since '1 hour ago' -p err --no-pager -q 2>/dev/null | wc -l"
            )
            if rc == 0 and int(out.strip() or "0") > 5:
                count = int(out.strip())
                _, sample, _ = self.ssh.run(
                    "journalctl -u pveproxy -u pvedaemon -u pve-cluster"
                    " --since '1 hour ago' -p err --no-pager -q 2>/dev/null | tail -5"
                )
                issues.append(Issue(
                    "log", "warning",
                    f"{count} Proxmox daemon error(s) in the last hour",
                    sample or "Check: journalctl -u pveproxy -u pvedaemon",
                ))
        except Exception as e:
            log.warning(f"LogChecker/PVE daemons: {e}")

        return issues

    def _truncate_logs(self, files: list[str]) -> str:
        for f in files:
            self.ssh.run(f"truncate -s 50M '{f}' 2>/dev/null")
        self.ssh.run("logrotate -f /etc/logrotate.conf 2>/dev/null", timeout=60)
        return f"Truncated {len(files)} file(s) and forced logrotate"

    def _vacuum_journal(self, days: int) -> str:
        rc, out, err = self.ssh.run(
            f"journalctl --vacuum-time={days}d --vacuum-size=500M 2>&1", timeout=60
        )
        return out or err or f"Journal vacuumed (retained last {days} days)"


class SnapshotChecker:
    def __init__(self, api: ProxmoxAPI, ssh: SSHClient, cfg: Config):
        self.api = api
        self.ssh = ssh
        self.cfg = cfg

    def check(self) -> list[Issue]:
        issues: list[Issue] = []
        node   = self.cfg.node
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.cfg.snap_max_days)

        try:
            for vm in self.api.get(f"/nodes/{node}/qemu"):
                vmid = vm.get("vmid")
                name = vm.get("name", f"vm-{vmid}")
                try:
                    snaps     = self.api.get(f"/nodes/{node}/qemu/{vmid}/snapshot")
                    old_snaps = [
                        s["name"] for s in snaps
                        if s.get("name") != "current"
                        and datetime.fromtimestamp(s.get("snaptime", 0), tz=timezone.utc) < cutoff
                    ]
                    if old_snaps:
                        issues.append(Issue(
                            "snapshot", "warning",
                            f"VM '{name}' has {len(old_snaps)} old snapshot(s) "
                            f"(>{self.cfg.snap_max_days}d)",
                            f"Snapshots: {', '.join(old_snaps)}",
                            fix_fn=lambda vid=vmid, sn=list(old_snaps), n=node:
                                self._delete_snapshots(vid, sn, n),
                            fix_label=f"Delete {len(old_snaps)} old snapshot(s) from '{name}'",
                        ))
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"SnapshotChecker: {e}")

        return issues

    def _delete_snapshots(self, vmid: int, snapnames: list[str], node: str) -> str:
        deleted, failed = [], []
        for snap in snapnames:
            try:
                # Correct endpoint: DELETE /nodes/{node}/qemu/{vmid}/snapshot/{snapname}
                self.api.delete(f"/nodes/{node}/qemu/{vmid}/snapshot/{snap}")
                deleted.append(snap)
            except Exception as e:
                log.warning(f"API delete snapshot {snap} failed ({e}); trying SSH")
                rc, _, _ = self.ssh.run(f"qm delsnapshot {vmid} {snap} 2>/dev/null")
                if rc == 0:
                    deleted.append(snap)
                else:
                    failed.append(snap)
        return (f"Deleted {len(deleted)} snapshot(s)"
                + (f"; failed: {', '.join(failed)}" if failed else ""))


# ─────────────────────────────────────────────
#  SCAN HISTORY
# ─────────────────────────────────────────────

class ScanHistory:
    """Rolling window of the last N scan summaries for trend display."""
    def __init__(self, maxlen: int = 48):
        self._history: deque[dict] = deque(maxlen=maxlen)

    def record(self, issues: list[Issue], duration_s: float):
        counts = {"critical": 0, "warning": 0, "info": 0}
        for i in issues:
            counts[i.severity] = counts.get(i.severity, 0) + 1
        self._history.append({
            "timestamp":  datetime.now(timezone.utc).isoformat(),
            "total":      len(issues),
            "counts":     counts,
            "duration_s": round(duration_s, 1),
        })

    def as_list(self) -> list[dict]:
        return list(self._history)


# ─────────────────────────────────────────────
#  MAIN AGENT
# ─────────────────────────────────────────────

class ProxmoxMaintenanceAgent:
    def __init__(self):
        self.config  = config
        self.api     = ProxmoxAPI(config)
        self.ssh     = SSHClient(config)
        self.history = ScanHistory()

        self._issues:        list[Issue]    = []
        self._last_scan:     Optional[str]  = None
        self._scan_duration: float          = 0.0
        self._scanning       = False
        self._ssh_ok         = False
        self._lock           = threading.Lock()

    def connect(self):
        errors = self.config.validate()
        if errors:
            raise ValueError(f"Config errors: {'; '.join(errors)}")
        self.api.connect()
        if self.config.ssh_host:
            try:
                self.ssh.connect()
                self._ssh_ok = True
            except Exception as e:
                log.warning(f"SSH unavailable (API-only mode): {e}")

    def scan(self) -> list[Issue]:
        with self._lock:
            if self._scanning:
                return list(self._issues)
            self._scanning = True

        t0     = time.monotonic()
        issues: list[Issue] = []

        for Checker in (DiskChecker, VMChecker, BackupChecker, LogChecker, SnapshotChecker):
            try:
                found = Checker(self.api, self.ssh, self.config).check()
                issues.extend(found)
                log.info(f"{Checker.__name__}: {len(found)} issue(s)")
            except Exception as e:
                log.error(f"{Checker.__name__} crashed: {e}", exc_info=True)

        duration = time.monotonic() - t0
        self.history.record(issues, duration)

        with self._lock:
            self._issues        = issues
            self._last_scan     = datetime.now(timezone.utc).isoformat()
            self._scan_duration = duration
            self._scanning      = False

        log.info(f"Scan complete — {len(issues)} issue(s) in {duration:.1f}s")

        if self.config.auto_fix:
            self._run_auto_fix(issues)

        return issues

    def _run_auto_fix(self, issues: list[Issue]):
        fixable = [i for i in issues if i.fix_fn and not i.fixed]
        log.info(f"Auto-fix: applying {len(fixable)} fix(es)")
        for issue in fixable:
            issue.apply_fix()

    def fix_issue(self, issue_id: str) -> tuple[bool, str]:
        with self._lock:
            target = next((i for i in self._issues if i.id == issue_id), None)
        if not target:
            return False, "Issue not found"
        return target.apply_fix()

    def fix_all(self) -> list[dict]:
        with self._lock:
            fixable = [i for i in self._issues if i.fix_fn and not i.fixed]
        results = []
        for issue in fixable:
            ok, msg = issue.apply_fix()
            results.append({"id": issue.id, "title": issue.title, "success": ok, "message": msg})
        return results

    def get_issues(self) -> list[dict]:
        with self._lock:
            return [i.to_dict() for i in self._issues]

    def get_summary(self) -> dict:
        with self._lock:
            counts  = {"critical": 0, "warning": 0, "info": 0}
            by_cat  = {}
            fixable = fixed = 0
            for i in self._issues:
                counts[i.severity] = counts.get(i.severity, 0) + 1
                by_cat[i.category] = by_cat.get(i.category, 0) + 1
                if i.fix_fn and not i.fixed: fixable += 1
                if i.fixed:                  fixed   += 1
            return {
                "total":         len(self._issues),
                "counts":        counts,
                "by_category":   by_cat,
                "fixable":       fixable,
                "fixed":         fixed,
                "last_scan":     self._last_scan,
                "scan_duration": round(self._scan_duration, 1),
                "scanning":      self._scanning,
                "auto_fix":      self.config.auto_fix,
                "node":          self.config.node,
                "host":          self.config.host,
                "ssh_ok":        self._ssh_ok,
            }

    def get_history(self) -> list[dict]:
        return self.history.as_list()

    def start_scheduler(self):
        def loop():
            time.sleep(self.config.scan_interval)   # first scan is done at startup
            while True:
                try:
                    log.info("Scheduler: starting scan")
                    self.scan()
                except Exception as e:
                    log.error(f"Scheduler error: {e}")
                time.sleep(self.config.scan_interval)

        threading.Thread(target=loop, daemon=True, name="agent-scheduler").start()
        log.info(f"Scheduler started — interval {self.config.scan_interval}s")


# Singleton used by server.py and cli.py
agent = ProxmoxMaintenanceAgent()
