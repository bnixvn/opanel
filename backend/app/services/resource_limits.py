"""Resource limits addon: CPU, memory, processes and disk I/O per hosting account.

The work happens in a root agent (app/agents/opanel_limits_agent.py, installed
as /usr/local/sbin/opanel-limits-agent and run as opanel-limits.service once
the addon is installed). This module is the panel's side of it:

- it turns the accounts in the database into the agent's configuration and
  hands it to the root helper (`limits-apply`) whenever an account, its limits
  or its reseller change, after the addon is installed, and when the panel
  starts;
- it reads back the usage the agent writes, for the accounts a caller may see.

Every account that is not an administrator gets a slice, whether or not it
has a limit, so its usage can be shown. A reseller's group caps sit on a
slice above its own account and all its customers, so they bound the sum
(operator, 2026-10-04: the administrator sets those, a reseller sets each
customer's). 0 means unlimited everywhere, and nothing here does anything
until the addon is installed.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.core.access import managed_user_ids
from app.core.permissions import is_admin_role, is_reseller_role
from app.models.entities import User, Website
from app.schemas.schemas import GROUP_LIMIT_FIELDS, RESOURCE_LIMIT_FIELDS
from app.services import site_users
from app.services.shell import shell

logger = logging.getLogger("opanel.resource_limits")

UNIT_FILE = Path("/etc/systemd/system/opanel-limits.service")
# `systemctl enable` makes this link, and the addon's Stop removes it.
UNIT_WANTS = Path("/etc/systemd/system/multi-user.target.wants/opanel-limits.service")
AGENT_SOURCE = Path(__file__).resolve().parent.parent / "agents" / "opanel_limits_agent.py"
USAGE_FILE = Path("/run/opanel-limits/usage.json")
HISTORY_FILE = Path("/var/lib/opanel-limits/history.json")
# Where an account's processes come from before the agent moves them: the web
# server (lsphp), PHP-FPM (the tools), cron, SSH/SFTP without a logind
# session, and the panel itself (the file manager, WP-CLI and the terminal
# run as the account).
SOURCES = ["lshttpd.service", "php*-fpm.service", "cron.service", "ssh.service", "opanel-api.service"]
IO_PATH = "/home"
# The agent writes usage every 10 seconds; older than this, it is not running.
STALE_SECONDS = 60


def installed() -> bool:
    """Whether the addon is installed. The unit file is what the helper writes
    on install and removes on uninstall, and anyone may stat it."""
    return UNIT_FILE.exists()


def enforcing() -> bool:
    """Installed and switched on, so the agent holds every account's limits.
    Stopping the addon releases them (addon_limits_disable)."""
    return installed() and UNIT_WANTS.exists()


# --- the agent -------------------------------------------------------------------

def _agent_source() -> bytes:
    return AGENT_SOURCE.read_bytes().replace(b"\r\n", b"\n")


def install_agent() -> None:
    """Hand the helper this release's agent. It installs it only if it matches
    the hash the helper itself carries, so a copy the panel user edited never
    runs as root.

    Called when the addon is installed or started. A panel update installs the
    new agent itself (update.sh, from the source it installs), so nothing here
    has to compare the copies on a timer."""
    result = shell.privileged("limits-agent-install", input=_agent_source().decode("utf-8"), check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "limits-agent-install failed").strip())


# --- naming ---------------------------------------------------------------------

def group_slice(reseller: User) -> str:
    return f"hosting-r{reseller.id}.slice"


def account_slice(user: User) -> str:
    """A reseller's own account sits in its group, as do its customers."""
    if is_reseller_role(user.role):
        return f"hosting-r{user.id}-a{user.id}.slice"
    if user.reseller_id:
        return f"hosting-r{user.reseller_id}-a{user.id}.slice"
    return f"hosting-a{user.id}.slice"


def _uid_of(name: str) -> Optional[int]:
    try:
        import pwd
    except ImportError:  # not Linux: tests, development
        return None
    try:
        return pwd.getpwnam(name).pw_uid
    except KeyError:
        return None


def account_uids(db: Session, user: User) -> list[int]:
    """The Linux users an account's processes run as: its own, and the one of
    each of its websites (the same user today; kept apart in case a site was
    imported under another). SFTP sub-accounts share their owner's uid."""
    names = {site_users.linux_user_for_panel_username(user.username)}
    names.update(row[0] for row in db.query(Website.linux_user).filter(Website.owner_id == user.id) if row[0])
    uids = {_uid_of(name) for name in names}
    return sorted(uid for uid in uids if uid is not None and 1000 <= uid < 60000)


def _limits(user, fields) -> dict:
    return {name: int(getattr(user, field) or 0) for name, field in zip(RESOURCE_LIMIT_FIELDS, fields)}


# --- configuration -------------------------------------------------------------

def build_config(db: Session) -> dict:
    users = db.query(User).order_by(User.id).all()
    groups, accounts, seen = [], [], set()
    for user in users:
        if is_admin_role(user.role):
            continue
        if is_reseller_role(user.role):
            groups.append({"slice": group_slice(user), "limits": _limits(user, GROUP_LIMIT_FIELDS)})
        uids = [uid for uid in account_uids(db, user) if uid not in seen]
        seen.update(uids)
        accounts.append({"slice": account_slice(user), "uids": uids,
                         "limits": _limits(user, RESOURCE_LIMIT_FIELDS), "sessions": True})
    return {"version": 1, "io_path": IO_PATH, "sources": SOURCES, "groups": groups, "accounts": accounts}


_sync_lock = threading.Lock()


def sync(db: Session) -> None:
    """Hand the agent the current configuration. Raises if the helper refuses."""
    if not installed():
        return
    config = build_config(db)
    with _sync_lock:
        result = shell.privileged("limits-apply", input=json.dumps(config), check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "limits-apply failed").strip())


def sync_quietly(db: Optional[Session] = None) -> None:
    """sync() for the places that must not fail because of it."""
    try:
        if db is not None:
            sync(db)
            return
        from app.core.database import SessionLocal

        with SessionLocal() as session:
            sync(session)
    except Exception:  # noqa: BLE001 - limits must never break the change that triggered them
        logger.warning("Resource limits were not updated", exc_info=True)


def sync_in_background() -> None:
    """After an account changes: the request does not wait on the helper."""
    if not installed():
        return
    threading.Thread(target=sync_quietly, name="opanel-limits-sync", daemon=True).start()


# --- OpenLiteSpeed's own limits ----------------------------------------------------

_refresh_lock = threading.Lock()


def refresh_vhosts() -> int:
    """Re-render every PHP website's vhost and restart OpenLiteSpeed once.

    The vhosts carry OpenLiteSpeed's rlimits only while the addon is not
    enforcing (openlitespeed._cgroup_limits_enforced), so this runs whenever
    the addon is installed, removed, started or stopped. A vhost that renders
    the same is not rewritten; a suspended one stays suspended. Returns how
    many websites were rendered.
    """
    from app.api.websites import _rewrite_website_vhost
    from app.core.database import SessionLocal
    from app.services import openlitespeed

    rendered = 0
    with _refresh_lock, SessionLocal() as db:
        for website in db.query(Website).order_by(Website.id).all():
            # A static site has no lsphp, so nothing of this is in its vhost.
            if (website.app_type or "wordpress") not in {"wordpress", "php"}:
                continue
            try:
                _rewrite_website_vhost(website, defer_reload=True)
                rendered += 1
            except Exception:  # noqa: BLE001 - one site must not stop the rest
                logger.warning("Could not re-render the vhost of %s", website.domain, exc_info=True)
        if rendered:
            openlitespeed.reload_service()
    return rendered


def refresh_vhosts_in_background() -> None:
    def worker() -> None:
        try:
            refresh_vhosts()
        except Exception:  # noqa: BLE001 - logged; a vhost not reached keeps what it had
            logger.warning("Could not re-render the vhosts", exc_info=True)

    threading.Thread(target=worker, name="opanel-limits-vhosts", daemon=True).start()


# --- usage -----------------------------------------------------------------------

def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _visible(db: Session, actor: User) -> list[User]:
    ids = managed_user_ids(db, actor)
    query = db.query(User).order_by(User.id)
    if ids is not None:
        query = query.filter(User.id.in_(sorted(ids)))
    return [user for user in query.all() if not is_admin_role(user.role)]


def oom_kills_last_day(entry) -> int:
    """How many processes the memory limit stopped over the history's day.

    The agent records the slice's running total at each point; a total that
    went down means the slice was made again (a restart, a reboot), so the
    count starts over from there rather than going negative.
    """
    points = entry.get("day") if isinstance(entry, dict) else None
    if not isinstance(points, list):
        return 0
    kills, previous = 0, None
    for point in points:
        value = point.get("oom") if isinstance(point, dict) else None
        if not isinstance(value, int):
            continue
        if previous is not None:
            kills += value - previous if value >= previous else value
        previous = value
    return kills


def overview(db: Session, actor: User) -> dict:
    """Limits and current use of every account the caller may see; for a
    reseller, its group's too."""
    data = _read_json(USAGE_FILE)
    slices = data.get("slices") if isinstance(data.get("slices"), dict) else {}
    stamp = data.get("time") if isinstance(data.get("time"), int) else None
    past = _read_json(HISTORY_FILE)
    accounts = {}
    for user in _visible(db, actor):
        entry = {
            "username": user.username,
            "role": user.role,
            "limits": _limits(user, RESOURCE_LIMIT_FIELDS),
            "usage": slices.get(account_slice(user)),
            "oom_kills_day": oom_kills_last_day(past.get(account_slice(user))),
        }
        if is_reseller_role(user.role):
            entry["group_limits"] = _limits(user, GROUP_LIMIT_FIELDS)
            entry["group_usage"] = slices.get(group_slice(user))
            entry["group_oom_kills_day"] = oom_kills_last_day(past.get(group_slice(user)))
        accounts[str(user.id)] = entry
    return {
        "installed": installed(),
        "running": bool(stamp and time.time() - stamp < STALE_SECONDS),
        "time": stamp,
        "accounts": accounts,
    }


def history(user: User) -> dict:
    data = _read_json(HISTORY_FILE)
    out = {"account": data.get(account_slice(user)) or {"day": [], "week": []}}
    if is_reseller_role(user.role):
        out["group"] = data.get(group_slice(user)) or {"day": [], "week": []}
    return out
