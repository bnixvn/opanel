"""The dashboard's status summary: one request, every part best effort.

The dashboard used to repeat the sidebar as a grid of links. It now shows
what the sidebar cannot: how things stand. Each part is read independently
and a part that fails is left out rather than failing the page, and nothing
here refreshes a remote check -- opening the dashboard must stay cheap.
"""
from __future__ import annotations

import logging
import platform
import socket
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.access import scope_owner
from app.core.permissions import is_admin_role
from app.core.version import APP_VERSION
from app.models.entities import BackupSchedule, DatabaseAccount, DnsZone, MailDomain, Mailbox, User, WebauthnCredential, Website
from app.services import dns_manager, firewall, mail, malware_scan, network, panel_settings, resource_limits, updates, waf
from app.services.shell import shell
from app.services.system import BASE_SERVICES

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
logger = logging.getLogger(__name__)
UNSECURED_SHOWN = 5


def _safe(read: Callable[[], Any], default: Any = None) -> Any:
    try:
        return read()
    except Exception as exc:  # one broken probe must not blank the dashboard
        logger.warning("dashboard: %s failed: %s", getattr(read, "__name__", "probe"), exc)
        return default


def _services() -> dict:
    # The daemons that must always run. lsphpNN units are started by
    # OpenLiteSpeed on demand and sit "inactive" by design.
    names = list(BASE_SERVICES)
    stopped = []
    for name in names:
        result = shell.run(["systemctl", "is-active", name], check=False)
        if (result.stdout or "").strip() != "active":
            stopped.append(name)
    return {"total": len(names), "running": len(names) - len(stopped), "stopped": stopped}


def _waf_engine() -> str:
    output = waf.status().stdout or ""
    if "not-installed" in output:
        return "off"
    return "on" if "installed" in output else "unknown"


def _malware() -> dict:
    status = malware_scan.cached_status() or {}
    jobs = panel_settings.list_malware_scan_jobs(limit=50)
    last = jobs[0] if jobs else None
    installed = bool(status.get("installed"))
    summary = {
        "installed": installed,
        "active": bool(status.get("active")),
        "last_scan": {
            "status": last.get("status"),
            "infected": int(last.get("infected") or 0),
            "finished_at": last.get("finished_at") or last.get("started_at"),
        } if last else None,
    }
    summary.update(_malware_state(jobs, installed))
    return summary


def _malware_state(jobs: list, installed: bool) -> dict:
    """SNPanel's reading (2026-10-17, the dashboard as SNPanel shows it): what
    the latest scan of *each* target found, so a clean scan of one site does
    not hide the threats an earlier scan found on another."""
    seen, threats, quarantined = set(), 0, 0
    last_scan_at, infected_job = None, None
    for job in jobs:  # newest first
        if job.get("status") not in {"done", "completed", "infected", "finished"}:
            continue
        if last_scan_at is None:
            last_scan_at = job.get("finished_at") or job.get("updated_at") or job.get("started_at")
        target = f"{job.get('scope', '')}:{','.join(job.get('domains') or [])}"
        if target in seen:
            continue
        seen.add(target)
        found = int(job.get("infected") or 0)
        if found and infected_job is None:
            infected_job = job.get("job_id")
        threats += found
        quarantined += int(job.get("quarantined") or 0)
    state = "not_installed" if not installed else "never" if not seen else "threats" if threats else "clean"
    return {"state": state, "threats": threats, "quarantined": quarantined,
            "last_scan_at": last_scan_at, "infected_job": infected_job}


def _backups(db: Session) -> dict:
    schedules = db.query(BackupSchedule).filter(BackupSchedule.is_active.is_(True)).all()
    ran = [s for s in schedules if s.last_run_at]
    latest = max(ran, key=lambda s: s.last_run_at) if ran else None
    failed = [s.id for s in schedules if (s.last_status or "").lower() in {"error", "failed"}]
    # SNPanel's one word for it: no schedule, none run yet, fine, or failing.
    state = "none" if not schedules else "error" if failed else "never" if latest is None else "ok"
    return {
        "state": state,
        "schedules": len(schedules),
        "last_run_at": latest.last_run_at.isoformat() if latest else None,
        "last_status": latest.last_status if latest else None,
        "failed": len(failed),
    }


def _limit_or_none(value):
    value = int(value or 0)
    return value or None  # 0 is unlimited


def _resources(db: Session, user: User) -> dict:
    """From the Resource limits addon's agent: for an administrator, the
    accounts at 80% of a limit and the ones that ran out of memory in a day;
    for anyone else, the account's own use against its limits."""
    if not resource_limits.installed():
        return {"enabled": False, "users_over_80": [], "oom_last_24h": []}
    accounts = resource_limits.overview(db, user).get("accounts", {})
    if not is_admin_role(user.role):
        own = accounts.get(str(user.id)) or {}
        usage, limits = own.get("usage") or {}, own.get("limits") or {}
        return {
            "enabled": True,
            "usage": {"cpu": {"percent": usage.get("cpu_percent", 0)},
                      "memory": {"bytes": int(usage.get("memory_mb") or 0) * 1048576,
                                 "oom_kills": own.get("oom_kills_day") or 0},
                      "processes": {"current": usage.get("processes", 0)}} if usage else None,
            "limits": {"cpu_percent": _limit_or_none(limits.get("cpu_percent")),
                       "memory_mb": _limit_or_none(limits.get("memory_mb")),
                       "process_limit": _limit_or_none(limits.get("process_limit"))},
        }
    over, oom = [], []
    for entry in accounts.values():
        usage, limits = entry.get("usage") or {}, entry.get("limits") or {}
        pairs = (("cpu_percent", "cpu_percent"), ("memory_mb", "memory_mb"), ("processes", "process_limit"))
        shares = [100.0 * float(usage.get(used) or 0) / float(limits[cap]) for used, cap in pairs if limits.get(cap)]
        if shares and max(shares) >= 80:
            over.append(entry["username"])
        if entry.get("oom_kills_day"):
            oom.append(entry["username"])
    return {"enabled": True, "users_over_80": sorted(over), "oom_last_24h": sorted(oom)}


def _server_ipv4() -> str:
    """The address a customer points a domain at (cPanel's "Shared IP")."""
    addresses = network.detect_addresses().get("ipv4") or []
    return addresses[0] if addresses else ""


def _os_name() -> str:
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.system()


def _uptime_seconds() -> int:
    try:
        return int(float(Path("/proc/uptime").read_text(encoding="ascii").split()[0]))
    except (OSError, ValueError, IndexError):
        return 0


def _server_details() -> dict:
    """The administrator's "Server information": all local reads, no probes."""
    return {
        "hostname": socket.getfqdn(),
        "os": _os_name(),
        "kernel": platform.release(),
        "uptime_seconds": _uptime_seconds(),
        "panel_version": APP_VERSION,
    }


def _accounts(db: Session) -> dict:
    return {
        "end_users": db.query(User).filter(User.role == "end_user").count(),
        "resellers": db.query(User).filter(User.role == "reseller").count(),
    }


@router.get("/summary")
def dashboard_summary(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    admin = is_admin_role(current_user.role)
    site_query = scope_owner(db.query(Website), Website.owner_id, db, current_user)
    db_query = scope_owner(db.query(DatabaseAccount), DatabaseAccount.owner_id, db, current_user)
    sites = site_query.all()
    unsecured = sorted(site.domain for site in sites if not site.ssl_enabled)
    summary: dict[str, Any] = {
        "websites": {
            "total": len(sites),
            "active": sum(1 for site in sites if (site.status or "active") == "active"),
            "suspended": sum(1 for site in sites if site.status == "suspended"),
            # As SNPanel counts them (2026-10-17).
            "with_ssl": sum(1 for site in sites if site.ssl_enabled),
            "waf_on": sum(1 for site in sites if site.waf_enabled),
            "without_ssl": sorted(site.domain for site in sites if not site.ssl_enabled and site.status != "suspended"),
        },
        "ssl": {
            "total": len(sites),
            "secured": len(sites) - len(unsecured),
            "unsecured": unsecured[:UNSECURED_SHOWN],
            "unsecured_count": len(unsecured),
        },
        "databases": {"total": db_query.count()},
        "server": {"ipv4": _safe(_server_ipv4, "")},
    }
    if admin:
        summary["users"] = {"total": db.query(User).filter(User.role != "admin").count()}
        enabled = _safe(firewall.is_enabled)
        summary["firewall"] = {"enabled": enabled,
                               "state": "enabled" if enabled else "disabled" if enabled is False else None,
                               "chain_active": None}
        engine = _safe(_waf_engine, "unknown")
        summary["waf"] = {"engine": True if engine == "on" else False if engine == "off" else None}
        summary["malware"] = _safe(_malware)
        summary["services"] = _safe(_services)
        summary["updates"] = _safe(updates.cached_release_summary)
        summary["backups"] = _safe(lambda: _backups(db))
        summary["server"].update(_safe(_server_details, {}))
        summary["accounts"] = _safe(lambda: _accounts(db))
        if _safe(mail.installed, False):
            summary["mail"] = _safe(lambda: {"domains": db.query(MailDomain).count(),
                                             "mailboxes": db.query(Mailbox).count()})
        if _safe(dns_manager.installed, False):
            summary["dns"] = _safe(lambda: {"zones": db.query(DnsZone).count()})
    else:
        passkeys = db.query(WebauthnCredential).filter(WebauthnCredential.user_id == current_user.id)
        summary["two_factor"] = {"totp": bool(current_user.totp_enabled), "passkeys": _safe(passkeys.count, 0)}
    summary["resources"] = _safe(lambda: _resources(db, current_user),
                                 {"enabled": False, "users_over_80": [], "oom_last_24h": []})
    return summary


def _count_os_updates(output: str) -> int:
    return sum(1 for line in (output or "").splitlines() if "upgradable from" in line)


@router.get("/updates")
def dashboard_updates(current_user: User = Depends(get_current_user)):
    """System packages waiting and a newer panel, for the dashboard's "Open
    Updates" lines (as SNPanel's, 2026-10-17). Administrators only; asking
    the package manager takes a moment, so the dashboard asks this second."""
    if not is_admin_role(current_user.role):
        return {"os": None, "panel": {"available": None, "latest": None}}
    result = _safe(lambda: shell.privileged("updates-status", check=False,
                                            fallback=["bash", "-lc", "apt list --upgradable 2>/dev/null | head -40"]))
    os_count = _count_os_updates(result.stdout) if result is not None and result.returncode == 0 else None
    panel = _safe(updates.cached_release_summary, {}) or {}
    return {"os": os_count, "panel": {"available": panel.get("update_available"), "latest": panel.get("latest_version")}}
