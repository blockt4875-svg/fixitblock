#!/usr/bin/env python3
"""
Proxmox Maintenance Agent — CLI  v2
Usage:
  python cli.py scan [--fix | --interactive] [--json]
  python cli.py fix  [--interactive]
  python cli.py status
  python cli.py watch [--interval N]
  python cli.py history
"""

import sys
import os
import json
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from proxmox_agent import agent, config

# ── ANSI palette ────────────────────────────────────────────────────────────
R     = "\033[0;31m"
Y     = "\033[0;33m"
G     = "\033[0;32m"
B     = "\033[0;36m"
A     = "\033[0;34m"
W     = "\033[1;37m"
DIM   = "\033[2m"
ITAL  = "\033[3m"
RST   = "\033[0m"
BOLD  = "\033[1m"

SEV_COL  = {"critical": R, "warning": Y, "info": B}
SEV_ICON = {"critical": "✗", "warning": "⚠", "info": "·"}
CAT_ICON = {"disk": "💾", "vm": "🖥 ", "backup": "📦", "log": "📋", "snapshot": "📸"}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _pct_col(pct: float) -> str:
    if pct >= 90: return R
    if pct >= 75: return Y
    return G


def _bar(pct: float, width: int = 20) -> str:
    filled = int(pct / 100 * width)
    col    = _pct_col(pct)
    return f"{col}{'█' * filled}{DIM}{'░' * (width - filled)}{RST}"


def _hr(char: str = "─", width: int = 56) -> str:
    return f"{DIM}{char * width}{RST}"


def _banner():
    print(f"""
{Y}  ███████╗██╗██╗  ██╗██╗████████╗██████╗ ██╗      ██████╗  ██████╗██╗  ██╗
{Y}  ██╔════╝██║╚██╗██╔╝██║╚══██╔══╝██╔══██╗██║     ██╔═══██╗██╔════╝██║ ██╔╝
{R}  █████╗  ██║ ╚███╔╝ ██║   ██║   ██████╔╝██║     ██║   ██║██║     █████╔╝
{R}  ██╔══╝  ██║ ██╔██╗ ██║   ██║   ██╔══██╗██║     ██║   ██║██║     ██╔═██╗
{B}  ██║     ██║██╔╝ ██╗██║   ██║   ██████╔╝███████╗╚██████╔╝╚██████╗██║  ██╗
{B}  ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝   ╚═╝   ╚═════╝ ╚══════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝{RST}
  {DIM}Proxmox Automated Maintenance Agent{RST}
""")


def _connect() -> bool:
    print(f"  {DIM}Connecting to {config.host}…{RST}")
    try:
        agent.connect()
        ssh = f"SSH: {G}OK{RST}" if agent._ssh_ok else f"SSH: {Y}unavailable{RST}"
        print(f"  {G}✓ API connected{RST}  {ssh}\n")
        return True
    except Exception as e:
        print(f"  {R}✗ {e}{RST}\n")
        return False


def _print_issue(issue: dict, idx: int | None = None):
    col  = SEV_COL.get(issue["severity"], W)
    icon = SEV_ICON.get(issue["severity"], "·")
    cat  = issue["category"]
    pre  = f"[{idx}] " if idx is not None else ""

    fixed_tag    = f" {G}[FIXED]{RST}"   if issue["fixed"]   else ""
    fixable_tag  = f" {DIM}(fixable){RST}" if issue["fixable"] and not issue["fixed"] else ""

    print(f"  {col}{icon}{RST} {pre}{CAT_ICON.get(cat,'·')} {W}{issue['title']}{RST}{fixed_tag}{fixable_tag}")

    # Truncate long descriptions for CLI
    desc = issue["description"]
    if len(desc) > 160:
        desc = desc[:157] + "…"
    for line in desc.splitlines()[:3]:
        print(f"     {DIM}{line}{RST}")

    if issue.get("fix_label") and not issue["fixed"]:
        print(f"     {B}→ {issue['fix_label']}{RST}")
    if issue.get("fix_output"):
        print(f"     {G}✔ {issue['fix_output']}{RST}")
    print()


def _print_summary(issues: list[dict]):
    crit = sum(1 for i in issues if i["severity"] == "critical")
    warn = sum(1 for i in issues if i["severity"] == "warning")
    info = sum(1 for i in issues if i["severity"] == "info")
    fix  = sum(1 for i in issues if i["fixable"] and not i["fixed"])

    print(_hr())
    print(
        f"  Total: {W}{len(issues)}{RST}   "
        f"{R}Crit: {crit}{RST}   "
        f"{Y}Warn: {warn}{RST}   "
        f"{B}Info: {info}{RST}   "
        f"Fixable: {A}{fix}{RST}"
    )
    print(_hr())
    print()


def _apply_fixes(issues: list[dict], interactive: bool):
    fixable = [i for i in issues if i["fixable"] and not i["fixed"]]
    if not fixable:
        print(f"  {G}✓ Nothing to fix.{RST}\n")
        return

    if interactive:
        print(f"\n{BOLD}  Interactive Fix Mode{RST}\n")
        for issue in fixable:
            col = SEV_COL.get(issue["severity"], W)
            print(f"  {col}{issue['title']}{RST}")
            print(f"  {DIM}→ {issue['fix_label']}{RST}")
            try:
                resp = input("  Apply this fix? [y/N] ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                print(f"\n  {DIM}Aborted.{RST}\n")
                return
            if resp == "y":
                print(f"  {Y}Applying…{RST}", end="", flush=True)
                ok, msg = agent.fix_issue(issue["id"])
                print(f"\r  {'  ' + G + '✓ ' if ok else '  ' + R + '✗ '}{msg}{RST}")
            else:
                print(f"  {DIM}Skipped.{RST}")
            print()
    else:
        print(f"  {W}Applying {len(fixable)} fix(es)…{RST}\n")
        for issue in fixable:
            print(f"  {Y}→{RST} {issue['title']}", end="", flush=True)
            ok, msg = agent.fix_issue(issue["id"])
            if ok:
                print(f"  {G}✓{RST}  {DIM}{msg}{RST}")
            else:
                print(f"  {R}✗{RST}  {DIM}{msg}{RST}")
        print()


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_scan(args):
    _banner()
    if not _connect():
        sys.exit(1)

    print(f"  {W}Running full maintenance scan…{RST}\n")
    t0     = time.monotonic()
    issues = agent.scan()
    dur    = time.monotonic() - t0

    if args.json:
        print(json.dumps([i for i in issues], indent=2, default=str))
        return

    if not issues:
        print(f"  {G}✓ No issues found. Node looks healthy.{RST}\n")
        return

    # Group by category
    cats: dict[str, list] = {}
    for i in [i.to_dict() for i in agent._issues]:
        cats.setdefault(i["category"], []).append(i)

    for cat, cat_issues in cats.items():
        print(f"{BOLD}  {CAT_ICON.get(cat,'·')} {cat.upper()}{RST}")
        for issue in cat_issues:
            _print_issue(issue)

    _print_summary([i.to_dict() for i in agent._issues])
    print(f"  {DIM}Scan completed in {dur:.1f}s{RST}\n")

    fixable = [i for i in [x.to_dict() for x in agent._issues] if i["fixable"] and not i["fixed"]]
    if fixable and not (args.auto_fix or args.interactive):
        print(f"  {Y}⚡ {len(fixable)} fixable issue(s).{RST}"
              f"  {DIM}Run with --fix or --interactive to apply.{RST}\n")

    if args.auto_fix or args.interactive:
        _apply_fixes([i.to_dict() for i in agent._issues], interactive=args.interactive)


def cmd_fix(args):
    _banner()
    if not _connect():
        sys.exit(1)
    print(f"  {W}Scanning…{RST}\n")
    agent.scan()
    _apply_fixes([i.to_dict() for i in agent._issues], interactive=args.interactive)


def cmd_status(args):
    _banner()
    if not _connect():
        sys.exit(1)

    cfg = config
    print(f"{BOLD}  Configuration{RST}")
    print(f"  {'Host':<18} {W}{cfg.host}:{cfg.port}{RST}")
    print(f"  {'Node':<18} {W}{cfg.node}{RST}")
    print(f"  {'SSH Host':<18} {W}{cfg.ssh_host}{RST}")
    print(f"  {'Auth':<18} {'token' if cfg.token_name else 'password'}")
    print(f"  {'Auto-Fix':<18} {(G + 'enabled') if cfg.auto_fix else (DIM + 'disabled')}{RST}")
    print(f"  {'Scan Interval':<18} {cfg.scan_interval}s")
    print()

    print(f"{BOLD}  Thresholds{RST}")
    print(f"  {'Disk':<18} warn {Y}{cfg.disk_warn_pct:.0f}%{RST}  crit {R}{cfg.disk_crit_pct:.0f}%{RST}")
    print(f"  {'Memory':<18} warn {Y}{cfg.mem_warn_pct:.0f}%{RST}  crit {R}{cfg.mem_crit_pct:.0f}%{RST}")
    print(f"  {'CPU':<18} warn {Y}{cfg.cpu_warn_pct:.0f}%{RST}")
    print(f"  {'Log retention':<18} {cfg.log_max_days} days")
    print(f"  {'Snap retention':<18} {cfg.snap_max_days} days")
    print(f"  {'Backup retention':<16} {cfg.backup_max_days} days")
    print()

    # Live node stats
    try:
        ns  = agent.api.get(f"/nodes/{cfg.node}/status")
        cpu = ns.get("cpu", 0) * 100
        mem = ns.get("memory", {})
        mp  = (mem.get("used", 0) / max(mem.get("total", 1), 1)) * 100
        up  = ns.get("uptime", 0)
        d, h, m = up // 86400, (up % 86400) // 3600, (up % 3600) // 60

        print(f"{BOLD}  Node Status{RST}  {G}✓ API reachable{RST}")
        print(f"  {'Uptime':<18} {W}{d}d {h}h {m}m{RST}")
        print(f"  {'CPU':<18} {_bar(cpu)} {_pct_col(cpu)}{cpu:.1f}%{RST}")
        print(f"  {'Memory':<18} {_bar(mp)} {_pct_col(mp)}{mp:.1f}%{RST}")
        print()
    except Exception as e:
        print(f"  {R}✗ Node status unavailable: {e}{RST}\n")


def cmd_history(args):
    _banner()
    hist = agent.history.as_list()
    if not hist:
        print(f"  {DIM}No scan history yet. Run a scan first.{RST}\n")
        return

    print(f"{BOLD}  Scan History ({len(hist)} records){RST}\n")
    print(f"  {'Timestamp':<28} {'Total':>6} {'Crit':>6} {'Warn':>6} {'Dur':>6}")
    print(f"  {DIM}{'─'*28} {'─'*6} {'─'*6} {'─'*6} {'─'*6}{RST}")
    for h in reversed(hist):
        c     = h["counts"]
        crit  = c.get("critical", 0)
        warn  = c.get("warning", 0)
        ccol  = R if crit else (Y if warn else G)
        ts    = h["timestamp"][:19].replace("T", " ")
        print(
            f"  {DIM}{ts}{RST}   "
            f"{W}{h['total']:>4}{RST}   "
            f"{ccol}{crit:>4}{RST}   "
            f"{Y if warn else DIM}{warn:>4}{RST}   "
            f"{DIM}{h['duration_s']:>4}s{RST}"
        )
    print()


def cmd_watch(args):
    _banner()
    if not _connect():
        sys.exit(1)

    interval = args.interval or config.scan_interval
    print(f"  {W}Watch mode — scanning every {interval}s{RST}  {DIM}Ctrl+C to exit{RST}\n")

    prev_ids: set[str] = set()

    try:
        while True:
            os.system("clear 2>/dev/null || cls 2>/dev/null")
            _banner()
            issues_raw = agent.scan()
            issues     = [i.to_dict() for i in issues_raw]
            cur_ids    = {i["id"] for i in issues}
            new_ids    = cur_ids - prev_ids

            from datetime import datetime
            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
            print(f"  {DIM}{now}{RST}   interval: {interval}s\n")

            if not issues:
                print(f"  {G}✓ All clear — no issues detected.{RST}\n")
            else:
                crit = sum(1 for i in issues if i["severity"] == "critical")
                warn = sum(1 for i in issues if i["severity"] == "warning")
                print(
                    f"  {R}Critical: {crit}{RST}   "
                    f"{Y}Warning: {warn}{RST}   "
                    f"Total: {W}{len(issues)}{RST}\n"
                )
                for issue in issues[:20]:
                    col  = SEV_COL.get(issue["severity"], W)
                    icon = SEV_ICON.get(issue["severity"], "·")
                    new  = f" {G}[NEW]{RST}" if issue["id"] in new_ids else ""
                    print(f"  {col}{icon}{RST} {issue['title']}{new}")

                if len(issues) > 20:
                    print(f"  {DIM}… and {len(issues)-20} more{RST}")

            prev_ids = cur_ids
            print(f"\n  {DIM}Next scan in {interval}s…{RST}")
            time.sleep(interval)

    except KeyboardInterrupt:
        print(f"\n  {DIM}Watch mode stopped.{RST}\n")


# ── Entry point ──────────────────────────────────────────────────────────────

def main():
    p    = argparse.ArgumentParser(prog="proxmox-agent", description="Proxmox Maintenance Agent CLI")
    subs = p.add_subparsers(dest="command")

    ps = subs.add_parser("scan", help="Run a full maintenance scan")
    ps.add_argument("--fix",         dest="auto_fix",    action="store_true", help="Apply all fixes automatically")
    ps.add_argument("--interactive", dest="interactive", action="store_true", help="Prompt before each fix")
    ps.add_argument("--json",        action="store_true",                     help="Output raw JSON")
    ps.set_defaults(func=cmd_scan)

    pf = subs.add_parser("fix", help="Scan and apply fixes")
    pf.add_argument("--interactive", dest="interactive", action="store_true")
    pf.set_defaults(func=cmd_fix)

    pst = subs.add_parser("status", help="Show config and live node stats")
    pst.set_defaults(func=cmd_status)

    pw = subs.add_parser("watch", help="Continuous scan loop")
    pw.add_argument("--interval", type=int, help="Seconds between scans")
    pw.set_defaults(func=cmd_watch)

    ph = subs.add_parser("history", help="Show scan history")
    ph.set_defaults(func=cmd_history)

    args = p.parse_args()
    if not args.command:
        _banner()
        p.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
