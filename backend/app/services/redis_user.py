"""Redis per account (operator, 2026-10-27, as DirectAdmin has it).

Each hosting account may turn on a Redis of its own: an instance of the
template unit opanel-redis@.service running as the account's Linux user, on
the unix socket /home/<user>/.redis/redis.sock and on no TCP port, so its
websites' PHP - which runs as that same user - reaches it and no other tenant
can. It is a cache: nothing is saved to disk, and a restart empties it.

The administrator sets how much memory it may use: on a package
(HostingPlan.redis_memory_mb) and on an account without one
(User.redis_memory_mb); 64 MB unless set, 0 = not allowed. The account turns
it on and off itself (User.redis_enabled). This module keeps the instance in
step whenever the account's limit, package, reseller (its slice) or
suspension changes, through the root helper's redis-user-* verbs; the helper
holds the configuration and the unit.

The server's own Redis on 127.0.0.1:6379, if any, is not touched. Nothing
here does anything until the addon is installed.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.core.permissions import is_admin_role
from app.models.entities import HostingPlan, User
from app.schemas.schemas import REDIS_MAX_MB
from app.services import site_users
from app.services.shell import shell

logger = logging.getLogger("opanel.redis_user")

ADDON_ID = "redis"
# What the helper writes on install and removes on uninstall; anyone may stat them.
UNIT_FILE = Path("/etc/systemd/system/opanel-redis@.service")
SERVER_BIN = Path("/usr/bin/redis-server")
CONF_DIR = Path("/etc/opanel-redis")
# Left by the addon's Stop (addon_redis_disable), taken away by its Start.
STOPPED_MARKER = CONF_DIR / "stopped"
DEFAULT_MB = 64

PROBLEM_TEXT = {
    "admin": "Administrators have no Redis of their own: each hosting account turns on its own, and you can log in as the customer to do it.",
    "no_user": "This account has no Linux user to run Redis as.",
    "not_installed": "The Redis per account addon is not installed.",
    "stopped": "Redis per account is stopped on this server.",
    "suspended": "This account is suspended.",
    "not_allowed": "Redis is not part of this account's package.",
}


def installed() -> bool:
    return UNIT_FILE.exists() and SERVER_BIN.exists()


def running() -> bool:
    """Installed and not stopped on the Addons page."""
    return installed() and not STOPPED_MARKER.exists()


def socket_path(linux_user: str) -> str:
    return f"{site_users.HOME_ROOT.as_posix()}/{linux_user}/.redis/redis.sock"


def _linux_name(user: User) -> Optional[str]:
    try:
        return site_users.linux_user_for_panel_username(user.username)
    except ValueError:
        return None


def linux_user(user: Optional[User]) -> Optional[str]:
    """The Linux user an account's Redis runs as; none for an administrator,
    who uses a customer's by logging in as the customer."""
    if user is None or is_admin_role(user.role):
        return None
    return _linux_name(user)


def _plan_of(db: Session, user: User) -> Optional[HostingPlan]:
    plan_id = getattr(user, "plan_id", None)
    if not plan_id:
        return None
    return db.query(HostingPlan).filter(HostingPlan.id == plan_id).first()


def limit_mb(db: Session, user: User) -> int:
    """The memory the account's Redis may use: its package's, else its own
    (an account without a package, like the other custom limits). 0 = not
    allowed."""
    plan = _plan_of(db, user)
    value = plan.redis_memory_mb if plan is not None else user.redis_memory_mb
    try:
        number = int(DEFAULT_MB if value is None else value)
    except (TypeError, ValueError):
        number = 0
    return max(0, min(number, REDIS_MAX_MB))


def problem(db: Session, user: User) -> Optional[str]:
    """Why the account may not turn its Redis on (a PROBLEM_TEXT key), or None."""
    if is_admin_role(user.role):
        return "admin"
    if linux_user(user) is None:
        return "no_user"
    if not installed():
        return "not_installed"
    if not running():
        return "stopped"
    if not user.is_active:
        return "suspended"
    if limit_mb(db, user) <= 0:
        return "not_allowed"
    return None


# --- the helper ----------------------------------------------------------------------

def _helper(command: str, args: list[str]) -> str:
    result = shell.privileged(command, helper_args=args, check=False)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or f"{command} failed").strip())
    return (result.stdout or "").strip()


def _apply(user: User, mb: int) -> None:
    from app.services import resource_limits

    # The account's slice, so its Redis counts against the account when the
    # Resource limits addon caps it (and moves with it to another reseller).
    _helper("redis-user-apply", [linux_user(user), str(int(mb)), resource_limits.account_slice(user)])


def _remove(user: User) -> None:
    name = _linux_name(user)
    if name:
        _helper("redis-user-remove", [name])


def _parse_kv(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for token in (text or "").split():
        key, sep, value = token.partition("=")
        if sep:
            out[key] = value
    return out


def instance_state(user: User) -> dict:
    """Whether the account's Redis runs, and the memory it uses (bytes)."""
    name = linux_user(user)
    state = {"running": False, "used_bytes": 0}
    if not name or not installed():
        return state
    try:
        parsed = _parse_kv(_helper("redis-user-status", [name]))
    except RuntimeError:
        return state
    used = parsed.get("used_memory", "")
    state["running"] = parsed.get("state") == "active"
    state["used_bytes"] = int(used) if used.isdigit() else 0
    return state


def describe(db: Session, user: User) -> dict:
    """GET /api/redis: what the account's page shows."""
    name = linux_user(user)
    why = problem(db, user)
    mb = limit_mb(db, user) if name else 0
    state = instance_state(user) if user.redis_enabled and name else {"running": False, "used_bytes": 0}
    return {
        "installed": installed(),
        "addon_running": running(),
        # The package (or the account) gives it memory to use.
        "allowed": bool(name) and mb > 0,
        "problem": why,
        "detail": PROBLEM_TEXT.get(why) if why else None,
        "limit_mb": mb,
        "enabled": bool(user.redis_enabled),
        **state,
        "linux_user": name,
        "socket": socket_path(name) if name else None,
    }


# --- the account's own switch ------------------------------------------------------

def enable(db: Session, user: User) -> None:
    why = problem(db, user)
    if why:
        raise ValueError(PROBLEM_TEXT[why])
    _apply(user, limit_mb(db, user))
    user.redis_enabled = True
    db.commit()


def disable(db: Session, user: User) -> None:
    """Off: the instance, its configuration and its socket directory go.
    Allowed while the addon is stopped or the account suspended - turning
    something off is never what has to wait."""
    if linux_user(user) is None:
        raise ValueError(PROBLEM_TEXT["admin" if is_admin_role(user.role) else "no_user"])
    if not installed():
        raise ValueError(PROBLEM_TEXT["not_installed"])
    _remove(user)
    user.redis_enabled = False
    db.commit()


def flush(db: Session, user: User) -> None:
    why = problem(db, user)
    if why:
        raise ValueError(PROBLEM_TEXT[why])
    if not user.redis_enabled:
        raise ValueError("Redis is off for this account.")
    _helper("redis-user-flush", [linux_user(user)])


# --- keeping instances in step ------------------------------------------------------

def sync_user(db: Session, user: User) -> None:
    """Bring the account's instance in step with the account, after its limit,
    package, reseller, role or suspension changed. Raises if the helper does.

    Its limit down to 0 (or the account made an administrator) turns it off
    for good. Suspended, the instance goes but the switch stays on, so
    unsuspending brings it back; with the addon stopped nothing runs anyway,
    and starting the addon re-applies every account (sync_all)."""
    if not installed() or not user.redis_enabled:
        return
    mb = limit_mb(db, user)
    if linux_user(user) is None or mb <= 0:
        _remove(user)
        user.redis_enabled = False
        db.commit()
        return
    if not running():
        return
    if not user.is_active:
        _remove(user)
        return
    _apply(user, mb)


def sync_quietly(db: Session, user: Optional[User]) -> None:
    """sync_user() for the places that must not fail because of it."""
    if user is None:
        return
    try:
        sync_user(db, user)
    except Exception:  # noqa: BLE001 - Redis must never break the change that triggered it
        logger.warning("The Redis of %s was not updated", getattr(user, "username", "?"), exc_info=True)


def sync_plan(db: Session, plan_id: int) -> None:
    """Every account on a package whose Redis memory changed."""
    if not installed():
        return
    users = db.query(User).filter(User.plan_id == plan_id, User.redis_enabled.is_(True)).order_by(User.id).all()
    for user in users:
        sync_quietly(db, user)


def sync_all(db: Session) -> None:
    """Every account with Redis on: after the addon is installed or started."""
    if not installed():
        return
    for user in db.query(User).filter(User.redis_enabled.is_(True)).order_by(User.id).all():
        sync_quietly(db, user)


def _in_background(name: str, work) -> None:
    def worker() -> None:
        try:
            from app.core.database import SessionLocal

            with SessionLocal() as session:
                work(session)
        except Exception:  # noqa: BLE001 - logged; an instance not reached keeps what it had
            logger.warning("Redis per account: %s failed", name, exc_info=True)

    threading.Thread(target=worker, name=f"opanel-redis-{name}", daemon=True).start()


def sync_plan_in_background(plan_id: int) -> None:
    """A package edit does not wait on one helper call per account."""
    if installed():
        _in_background("plan", lambda session: sync_plan(session, plan_id))


def sync_all_in_background() -> None:
    if installed():
        _in_background("all", sync_all)


def remove_account(user: User) -> None:
    """The account is being deleted: its Redis goes first (userdel refuses a
    user with processes still running)."""
    if not installed() or not user.redis_enabled:
        return
    try:
        _remove(user)
    except Exception:  # noqa: BLE001 - the deletion goes on; the helper's uninstall sweeps leftovers
        logger.warning("The Redis of %s was not removed", user.username, exc_info=True)


def forget_all(db: Session) -> None:
    """The addon was removed, and every instance with it: every switch goes off."""
    db.query(User).filter(User.redis_enabled.is_(True)).update({User.redis_enabled: False}, synchronize_session=False)
    db.commit()


def accounts(db: Session, user_ids: Optional[set[int]] = None) -> list[dict]:
    """The accounts with Redis on (those an admin or reseller manages), with
    their limit and whether each runs."""
    query = db.query(User).filter(User.redis_enabled.is_(True)).order_by(User.username)
    if user_ids is not None:
        query = query.filter(User.id.in_(sorted(user_ids)))
    rows = []
    for user in query.all():
        rows.append({
            "id": user.id,
            "username": user.username,
            "is_active": bool(user.is_active),
            "limit_mb": limit_mb(db, user),
            **instance_state(user),
        })
    return rows
