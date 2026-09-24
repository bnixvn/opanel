"""The dashboard's status summary: one request, every part best effort.

The dashboard used to repeat the sidebar as a grid of links. It now shows
what the sidebar cannot: how things stand. Each part is read independently
and a part that fails is left out rather than failing the page, and nothing
here refreshes a remote check -- opening the dashboard must stay cheap.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import is_admin_role
from app.models.entities import BackupSchedule, DatabaseAccount, User, Website
from app.services import firewall, malware_scan, panel_settings, updates, waf
from app.services.shell import shell
from app.services.system import list_services

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
    names = list_services()
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
    jobs = panel_settings.list_malware_scan_jobs(limit=1)
    last = jobs[0] if jobs else None
    return {
        "installed": bool(status.get("installed")),
        "active": bool(status.get("active")),
        "last_scan": {
            "status": last.get("status"),
            "infected": int(last.get("infected") or 0),
            "finished_at": last.get("finished_at") or last.get("started_at"),
        } if last else None,
    }


def _backups(db: Session) -> dict:
    schedules = db.query(BackupSchedule).filter(BackupSchedule.is_active.is_(True)).all()
    ran = [s for s in schedules if s.last_run_at]
    latest = max(ran, key=lambda s: s.last_run_at) if ran else None
    failed = [s.id for s in schedules if (s.last_status or "").lower() in {"error", "failed"}]
    return {
        "schedules": len(schedules),
        "last_run_at": latest.last_run_at.isoformat() if latest else None,
        "last_status": latest.last_status if latest else None,
        "failed": len(failed),
    }


@router.get("/summary")
def dashboard_summary(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    admin = is_admin_role(current_user.role)
    site_query = db.query(Website)
    db_query = db.query(DatabaseAccount)
    if not admin:
        site_query = site_query.filter(Website.owner_id == current_user.id)
        db_query = db_query.filter(DatabaseAccount.owner_id == current_user.id)
    sites = site_query.all()
    unsecured = sorted(site.domain for site in sites if not site.ssl_enabled)
    summary: dict[str, Any] = {
        "websites": {
            "total": len(sites),
            "active": sum(1 for site in sites if (site.status or "active") == "active"),
            "suspended": sum(1 for site in sites if site.status == "suspended"),
        },
        "ssl": {
            "total": len(sites),
            "secured": len(sites) - len(unsecured),
            "unsecured": unsecured[:UNSECURED_SHOWN],
            "unsecured_count": len(unsecured),
        },
        "databases": {"total": db_query.count()},
    }
    if admin:
        summary["users"] = {"total": db.query(User).filter(User.role != "admin").count()}
        summary["firewall"] = {"enabled": _safe(firewall.is_enabled)}
        summary["waf"] = {"engine": _safe(_waf_engine, "unknown")}
        summary["malware"] = _safe(_malware)
        summary["services"] = _safe(_services)
        summary["updates"] = _safe(updates.cached_release_summary)
        summary["backups"] = _safe(lambda: _backups(db))
    return summary
