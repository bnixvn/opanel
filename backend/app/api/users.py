import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session
from typing import List, Optional

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.access import managed_user
from app.core.permissions import Role, ensure_role, is_admin_role, is_reseller_role
from app.core.security import hash_password
from app.core.step_up import require_sensitive_action_step_up
from app.models.entities import AuditLog, BackupSchedule, DatabaseAccount, HostingPlan, McpToken, User, Website, WebsiteAlias
from app.schemas.schemas import (
    AuditLogOut,
    UserCreate,
    UserOut,
    UserPasswordUpdate,
    UserUpdate,
    UserUsageOut,
)
from app.services.audit import log_action
from app.services import dns_manager, mail, mariadb, notifications, openlitespeed, reseller as reseller_pool, sftp_accounts, site_users, ssl, storage_quota, wordpress

router = APIRouter(prefix="/users", tags=["users"])


def _user_out(user: User, db: Session) -> dict:
    data = UserOut.model_validate(user).model_dump()
    data.update(storage_quota.storage_usage_summary(db, user))
    return data


def _decode_schedule_user_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = [item for item in raw.split(",") if item]
    if isinstance(value, int):
        value = [value]
    return [int(item) for item in value if int(item) > 0]


def _remove_user_from_backup_schedules(db: Session, user_id: int) -> None:
    for schedule in db.query(BackupSchedule).all():
        changed = False
        if schedule.user_id == user_id:
            schedule.user_id = None
            changed = True
        user_ids = _decode_schedule_user_ids(schedule.user_ids)
        if user_id in user_ids:
            user_ids = [item for item in user_ids if item != user_id]
            schedule.user_ids = json.dumps(user_ids)
            changed = True
        if changed and not schedule.all_users and schedule.user_id is None and not user_ids:
            db.delete(schedule)


def _delete_owned_website(db: Session, website: Website, also_deleting=()) -> None:
    # .all(), not .first(): a website may carry more than one database, and the
    # extra rows used to survive the deletion with their MariaDB schemas intact.
    db_items = db.query(DatabaseAccount).filter(DatabaseAccount.website_id == website.id).all()
    for db_item in db_items:
        mariadb.drop_database(db_item.db_name, db_item.db_user)
    db.query(WebsiteAlias).filter(WebsiteAlias.website_id == website.id).delete(synchronize_session=False)
    openlitespeed.remove_vhost(website.domain)
    ssl.release_site_certificates(db, website, also_deleting)
    wordpress.delete_wordpress(website.root_path)
    for db_item in db_items:
        db.delete(db_item)
    db.delete(website)


def _delete_orphan_databases(db: Session, owner_id: int) -> list[str]:
    """Drop the owner's databases that no website teardown reached.

    POST /api/databases creates rows with website_id NULL, so a per-website
    lookup could never see them -- on a live box a third of the rows looked
    like that. Because db_name is derived deterministically from the domain and
    create_database issues CREATE DATABASE IF NOT EXISTS, a surviving schema is
    silently adopted by the next website created for the same domain and filed
    under its new owner, so this is not merely leftover disk usage.
    """
    dropped: list[str] = []
    for item in db.query(DatabaseAccount).filter(DatabaseAccount.owner_id == owner_id).all():
        mariadb.drop_database(item.db_name, item.db_user)
        db.delete(item)
        dropped.append(item.db_name)
    return dropped


def _account_limits(source) -> dict:
    return {field: getattr(source, field) for field in reseller_pool.LIMIT_FIELDS}


def _reseller(db: Session, reseller_id: int) -> User:
    owner = db.query(User).filter(User.id == reseller_id).first()
    if owner is None or not is_reseller_role(owner.role):
        raise HTTPException(status_code=400, detail="That account is not a reseller")
    return owner


def _check_pool(db: Session, reseller: User, **kwargs) -> None:
    try:
        reseller_pool.check_pool(db, reseller, **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("", response_model=UserOut)
def create_user(payload: UserCreate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """A new account. A reseller's new accounts are always its own customers."""
    ensure_role(current_user.role, Role.reseller)
    role = payload.role
    reseller_id = payload.reseller_id
    pools = {field: getattr(payload, field) for field in reseller_pool.POOL_FIELDS}
    if is_reseller_role(current_user.role):
        role, reseller_id = "end_user", current_user.id
        pools = {field: 0 for field in reseller_pool.POOL_FIELDS}
    elif role != "end_user":
        reseller_id = None
    if role != "reseller":
        pools = {field: 0 for field in reseller_pool.POOL_FIELDS}
    # Only the username has to be unique -- one contact email may be shared by
    # several panel users.
    if db.query(User).filter(User.username == payload.username).first():
        raise HTTPException(status_code=409, detail="Username already taken")
    limits = _account_limits(payload)
    if reseller_id:
        _check_pool(db, current_user if reseller_id == current_user.id else _reseller(db, reseller_id),
                    new_account=limits)
    if role == "reseller":
        # Its own account comes out of its share too.
        _check_pool(db, User(id=None, **limits, **pools))
    try:
        site_users.ensure_panel_user(payload.username, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    user = User(
        username=payload.username,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        role=role,
        reseller_id=reseller_id,
        **limits,
        **pools,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    log_action(db, current_user.id, "create_user", user.username, request=request)
    return _user_out(user, db)


@router.get("", response_model=List[UserOut])
def list_users(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The accounts themselves, with no disk measured.

    Storage used is the one figure here that costs real work -- a `du` over
    every site a user owns -- and on a box with a few large accounts it held
    the whole page back. It is served separately by /users/usage, so the list
    paints immediately and the numbers arrive after.

    The limit still comes back: it is arithmetic on a column, not a
    measurement. Used and percent are null rather than 0, so the page can tell
    "not measured yet" apart from "uses nothing".

    An admin sees every account; a reseller sees its customers.
    """
    ensure_role(current_user.role, Role.reseller)
    rows = []
    for user in _listed_users(db, current_user):
        data = UserOut.model_validate(user).model_dump()
        data["storage_used_bytes"] = None
        data["storage_percent"] = None
        data["storage_limit_bytes"] = storage_quota.user_storage_limit_bytes(user)
        rows.append(data)
    return rows


@router.get("/usage", response_model=List[UserUsageOut])
def list_user_usage(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """What each account is using on disk. Slow by nature; fetched on its own.

    Declared above /{user_id} so the literal path wins the route match.
    """
    ensure_role(current_user.role, Role.reseller)
    return [
        {"id": user.id, **storage_quota.storage_usage_summary(db, user)}
        for user in _listed_users(db, current_user)
    ]


def _listed_users(db: Session, actor: User) -> list[User]:
    query = db.query(User).order_by(User.id.desc())
    if not is_admin_role(actor.role):
        query = query.filter(User.reseller_id == actor.id)
    return query.all()


@router.get("/pool")
def my_pool(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """A reseller's share of the server and what it has handed out."""
    ensure_role(current_user.role, Role.reseller)
    if not is_reseller_role(current_user.role):
        raise HTTPException(status_code=400, detail="Only a reseller has a share to report")
    return reseller_pool.usage(db, current_user)


@router.get("/{user_id}/pool")
def reseller_pool_usage(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return reseller_pool.usage(db, _reseller(db, user_id))


@router.get("/me", response_model=UserOut)
def me(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _user_out(current_user, db)

@router.patch("/me", response_model=UserOut)
def update_me(payload: UserUpdate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Let the current user update their own email."""
    if payload.email is not None and payload.email != current_user.email:
        current_user.email = payload.email
        db.commit()
        db.refresh(current_user)
    log_action(db, current_user.id, "update_profile_email", current_user.username, request=request)
    return _user_out(current_user, db)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    user = managed_user(db, current_user, user_id)
    admin = is_admin_role(current_user.role)
    if not admin:
        if user.id == current_user.id:
            raise HTTPException(status_code=403, detail="Your own limits are set by the administrator")
        if payload.role is not None or payload.reseller_id is not None or any(
                getattr(payload, field) is not None for field in reseller_pool.POOL_FIELDS):
            raise HTTPException(status_code=403, detail="Only the administrator can change roles or resellers")

    limit_changes = {field: getattr(payload, field) for field in reseller_pool.LIMIT_FIELDS
                     if getattr(payload, field) is not None}
    pool_changes = {field: getattr(payload, field) for field in reseller_pool.POOL_FIELDS
                    if getattr(payload, field) is not None}
    new_role = payload.role if payload.role is not None else user.role
    new_reseller_id = user.reseller_id
    if payload.reseller_id is not None:
        new_reseller_id = payload.reseller_id or None
    if new_role != "end_user":
        new_reseller_id = None

    if new_role != user.role:
        if user_id == current_user.id:
            raise HTTPException(status_code=400, detail="Cannot change your own role")
        if is_reseller_role(user.role) and reseller_pool.customers(db, user):
            raise HTTPException(status_code=400,
                                detail="This reseller still has customers: move or delete them first")
    # The share each affected reseller would end up with must still hold.
    if new_reseller_id and new_reseller_id != user.reseller_id:
        _check_pool(db, _reseller(db, new_reseller_id), new_account={**_account_limits(user), **limit_changes})
    elif new_reseller_id and limit_changes:
        _check_pool(db, _reseller(db, new_reseller_id), changes={user.id: limit_changes})
    if new_role == "reseller" and (pool_changes or limit_changes or new_role != user.role):
        _check_pool(db, user, pool=pool_changes, changes={user.id: limit_changes})

    role_changed = False
    if new_role != user.role:
        user.role = new_role
        role_changed = True
    user.reseller_id = new_reseller_id
    if new_role == "reseller":
        for field, value in pool_changes.items():
            setattr(user, field, value)
    else:
        for field in reseller_pool.POOL_FIELDS:
            setattr(user, field, 0)
    if payload.email is not None and payload.email != user.email:
        user.email = payload.email
    if payload.is_active is not None:
        if user_id == current_user.id and payload.is_active is False:
            raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
        if user.is_active != payload.is_active:
            user.is_active = payload.is_active
            user.token_version = (user.token_version or 0) + 1
    for field, value in limit_changes.items():
        setattr(user, field, value)

    if role_changed:
        # New role -> existing tokens with old role claim should be invalidated.
        user.token_version = (user.token_version or 0) + 1

    db.commit()
    db.refresh(user)
    log_action(db, current_user.id, "update_user", user.username, request=request)
    return _user_out(user, db)


@router.delete("/{user_id}")
def delete_user(user_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    user = managed_user(db, current_user, user_id)
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot delete yourself")
    if is_reseller_role(user.role) and reseller_pool.customers(db, user):
        raise HTTPException(status_code=400,
                            detail="This reseller still has customers: move or delete them first")
    websites = db.query(Website).filter(Website.owner_id == user.id).order_by(Website.id.asc()).all()
    deleted_domains = []
    panel_linux_user = site_users.linux_user_for_panel_username(user.username)
    try:
        for website in websites:
            if website.linux_user and website.linux_user != panel_linux_user:
                raise ValueError(f"Website {website.domain} is not owned by Linux user {panel_linux_user}")
        sftp_accounts.delete_for_owner(db, user)
        for website in websites:
            _delete_owned_website(db, website, also_deleting=[w.id for w in websites])
            deleted_domains.append(website.domain)
        # Anything owned but not attached to one of those websites.
        _delete_orphan_databases(db, user.id)
        _remove_user_from_backup_schedules(db, user.id)
        mail.delete_for_owner(db, user)
        dns_manager.delete_for_owner(db, user)
        db.query(McpToken).filter(McpToken.user_id == user.id).delete(synchronize_session=False)
        # A reseller's own packages go with it.
        db.query(HostingPlan).filter(HostingPlan.owner_id == user.id).delete(synchronize_session=False)
        site_users.delete_panel_user(user.username)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    username = user.username
    db.delete(user)
    db.commit()
    log_action(db, current_user.id, "delete_user", username, ",".join(deleted_domains), request=request)
    return {"ok": True, "deleted_websites": deleted_domains}


@router.post("/{user_id}/password")
def update_user_password(user_id: int, payload: UserPasswordUpdate, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if user_id != current_user.id:
        ensure_role(current_user.role, Role.reseller)
    else:
        require_sensitive_action_step_up(current_user, payload.current_password, payload.code)
    user = managed_user(db, current_user, user_id)
    try:
        site_users.set_panel_user_password(user.username, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    user.hashed_password = hash_password(payload.password)
    # Force re-login on all other sessions of this user.
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    log_action(db, current_user.id, "update_user_password", user.username, request=request)
    notifications.security_change(user.id, "password_changed" if user.id == current_user.id else "password_reset_by_admin",
                                  notifications.client_ip(request))
    return {"message": f"Changed password for user {user.username}"}


@router.post("/{user_id}/2fa/reset")
def reset_user_two_factor(user_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.reseller)
    user = managed_user(db, current_user, user_id)
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Use the Security page to disable your own 2FA")
    user.totp_enabled = False
    user.totp_secret = None
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    log_action(db, current_user.id, "reset_user_2fa", user.username, request=request)
    notifications.security_change(user.id, "2fa_reset_by_admin", notifications.client_ip(request))
    return {"message": f"Reset 2FA for user {user.username}"}


@router.get("/audit/log", response_model=List[AuditLogOut])
def list_audit(
    user_id: Optional[int] = Query(default=None),
    action: Optional[str] = Query(default=None, max_length=64),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ensure_role(current_user.role, Role.admin)
    query = db.query(AuditLog).order_by(AuditLog.id.desc())
    if user_id is not None:
        query = query.filter(AuditLog.user_id == user_id)
    if action:
        query = query.filter(AuditLog.action == action)
    rows = query.offset(offset).limit(limit).all()
    return [AuditLogOut.from_row(row) for row in rows]
