"""Redis per account addon (operator, 2026-10-27): the account's own Redis.

GET  /redis           this account's Redis: allowed, limit, on, running, memory used, socket
POST /redis/enable    turn it on
POST /redis/disable   turn it off
POST /redis/flush     empty it
GET  /redis/accounts  the accounts with Redis on (administrator: all; reseller: its own)

Each acts for the signed-in account; an administrator acts for a customer by
logging in as the customer. services/redis_user.py holds the rules.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.access import managed_user_ids
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import User
from app.services import redis_user
from app.services.audit import log_action

router = APIRouter(prefix="/redis", tags=["redis"])


def _run(work) -> None:
    try:
        work()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("")
def info(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return redis_user.describe(db, current_user)


@router.get("/accounts")
def accounts(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    return {"installed": redis_user.installed(), "running": redis_user.running(),
            "accounts": redis_user.accounts(db, managed_user_ids(db, current_user))}


@router.post("/enable")
def enable(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _run(lambda: redis_user.enable(db, current_user))
    log_action(db, current_user.id, "redis_enable", current_user.username,
               f"{redis_user.limit_mb(db, current_user)} MB", request=request)
    return redis_user.describe(db, current_user)


@router.post("/disable")
def disable(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _run(lambda: redis_user.disable(db, current_user))
    log_action(db, current_user.id, "redis_disable", current_user.username, request=request)
    return redis_user.describe(db, current_user)


@router.post("/flush")
def flush(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _run(lambda: redis_user.flush(db, current_user))
    log_action(db, current_user.id, "redis_flush", current_user.username, request=request)
    return redis_user.describe(db, current_user)
