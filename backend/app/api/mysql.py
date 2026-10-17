"""Server > MySQL: MariaDB's share of the server, and who is using it (administrators)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import DatabaseAccount, User
from app.services import mariadb, mysql_limits
from app.services.audit import log_action

router = APIRouter(prefix="/mysql", tags=["mysql"])


def _owners(db: Session) -> dict[str, str]:
    """Database account -> the panel account that owns it."""
    rows = db.query(DatabaseAccount.db_user, User.username).join(User, User.id == DatabaseAccount.owner_id).all()
    return {db_user: username for db_user, username in rows}


def _limits_body() -> dict:
    return {"limits": mysql_limits.saved(), "defaults": mysql_limits.DEFAULTS, "ranges": mysql_limits.ranges(),
            "memory_floor_mb": mysql_limits.memory_floor_mb(), "buffer_pool_mb": mysql_limits.buffer_pool_mb()}


@router.get("/limits")
def get_limits(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return _limits_body()


@router.put("/limits")
def put_limits(payload: dict, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """MariaDB's CPU, IO and memory on its unit, and the per-account connection backstop."""
    ensure_role(current_user.role, Role.admin)
    try:
        limits = mysql_limits.apply(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    log_action(db, current_user.id, "mysql_limits", "mariadb",
               " ".join(f"{key}={limits[key]}" for key in (*mysql_limits.FIELDS, "userstat")), request=request)
    return _limits_body()


@router.get("/usage")
def get_usage(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """MariaDB now: its unit's use, the server's own figures, the busiest users and the running statements."""
    ensure_role(current_user.role, Role.admin)
    owners = _owners(db)
    globals_ = mysql_limits.server_globals()
    return {
        "unit": mysql_limits.unit_usage(),
        "server": globals_,
        "users": mysql_limits.busiest_users(owners) if globals_ and globals_.get("userstat") else None,
        "processes": mysql_limits.processes(owners),
    }


@router.post("/processes/{thread_id}/kill")
def kill_process(thread_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Stop one running statement; its connection stays."""
    ensure_role(current_user.role, Role.admin)
    try:
        stopped = mysql_limits.kill_query(thread_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (mariadb.MariaDBError, RuntimeError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    log_action(db, current_user.id, "mysql_kill_query", stopped["user"], f"thread {thread_id}", request=request)
    return {"ok": True, **stopped}
