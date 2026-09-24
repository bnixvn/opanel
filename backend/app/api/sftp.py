"""SFTP: how to connect, and the extra per-folder logins an account creates."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import is_admin_role
from app.models.entities import SftpAccount, User, Website
from app.services import sftp_accounts
from app.services.audit import log_action

router = APIRouter(prefix="/sftp", tags=["sftp"])


class SftpAccountCreate(BaseModel):
    suffix: str = Field(min_length=1, max_length=16)
    website_id: Optional[int] = None
    subpath: str = Field(default="", max_length=400)
    password: str = Field(min_length=12, max_length=72)
    # Administrators create a login on behalf of a hosting account.
    owner_id: Optional[int] = None


class SftpPassword(BaseModel):
    password: str = Field(min_length=12, max_length=72)


def _account_for(db: Session, account_id: int, current_user: User) -> SftpAccount:
    account = db.query(SftpAccount).filter(SftpAccount.id == account_id).first()
    if not account or (account.owner_id != current_user.id and not is_admin_role(current_user.role)):
        raise HTTPException(status_code=404, detail="SFTP account not found")
    return account


def _fail(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("")
def sftp_overview(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    admin = is_admin_role(current_user.role)
    query = db.query(SftpAccount)
    if not admin:
        query = query.filter(SftpAccount.owner_id == current_user.id)
    accounts = query.order_by(SftpAccount.username.asc()).all()
    website_ids = {a.website_id for a in accounts if a.website_id}
    websites = {w.id: w for w in db.query(Website).filter(Website.id.in_(website_ids)).all()} if website_ids else {}
    primary = None
    if not admin:
        linux_user = sftp_accounts.owner_linux_user(current_user)
        primary = {"username": linux_user, "directory": "/", "server_path": f"/home/{linux_user}"}
    return {
        "host": sftp_accounts.sftp_host(),
        "port": sftp_accounts.sftp_port(),
        "primary": primary,
        "accounts": [sftp_accounts.serialize(a, websites) for a in accounts],
        "max_accounts": sftp_accounts.MAX_ACCOUNTS_PER_OWNER,
    }


@router.post("/accounts")
def create_sftp_account(payload: SftpAccountCreate, request: Request, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    owner = current_user
    if payload.owner_id and payload.owner_id != current_user.id:
        if not is_admin_role(current_user.role):
            raise HTTPException(status_code=403, detail="Not allowed")
        owner = db.query(User).filter(User.id == payload.owner_id).first()
        if not owner:
            raise HTTPException(status_code=404, detail="User not found")
    try:
        account = sftp_accounts.create_account(db, owner, payload.suffix, payload.website_id, payload.subpath, payload.password)
    except (ValueError, RuntimeError) as exc:
        raise _fail(exc) from exc
    log_action(db, current_user.id, "create_sftp_account", account.username, account.directory, request=request)
    websites = {account.website_id: db.get(Website, account.website_id)} if account.website_id else {}
    return sftp_accounts.serialize(account, websites)


@router.post("/accounts/{account_id}/password")
def change_sftp_password(account_id: int, payload: SftpPassword, request: Request, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    account = _account_for(db, account_id, current_user)
    try:
        sftp_accounts.set_password(account, account.owner, payload.password)
    except (ValueError, RuntimeError) as exc:
        raise _fail(exc) from exc
    log_action(db, current_user.id, "change_sftp_password", account.username, request=request)
    return {"ok": True}


@router.delete("/accounts/{account_id}")
def delete_sftp_account(account_id: int, request: Request, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    account = _account_for(db, account_id, current_user)
    username = account.username
    try:
        sftp_accounts.delete_account(db, account, account.owner)
    except (ValueError, RuntimeError) as exc:
        raise _fail(exc) from exc
    log_action(db, current_user.id, "delete_sftp_account", username, request=request)
    return {"ok": True}
