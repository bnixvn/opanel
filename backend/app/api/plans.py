"""Hosting packages: the admin's (also used by provisioning/WHMCS) and each
reseller's own, which only that reseller sees and uses for its customers."""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role, is_reseller_role
from app.models.entities import User
from app.schemas.schemas import REDIS_MAX_MB, RESOURCE_LIMIT_FIELDS, ResourceLimitsIn
from app.services import php_isolation, provisioning, redis_user

router = APIRouter(prefix="/plans", tags=["plans"])


class PlanCreate(ResourceLimitsIn):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=128)
    website_limit: int = Field(default=1, ge=0, le=10000)
    storage_limit_mb: int = Field(default=1024, ge=0, le=1048576)
    database_limit: int = Field(default=10, ge=0, le=1000)
    mailbox_limit: int = Field(default=10, ge=0, le=10000)
    # Resource limits addon (0 = unlimited); copied to an account made from it.
    cpu_percent: int = 0
    memory_mb: int = 0
    process_limit: int = 0
    io_read_mbps: int = 0
    io_write_mbps: int = 0
    # The browser terminal for accounts made from it.
    terminal_enabled: bool = False
    # open_basedir for every site of its accounts (services/php_isolation.py).
    open_basedir: bool = False
    # Redis per account addon: what each of its accounts' Redis may use; 0 = not allowed.
    redis_memory_mb: int = Field(default=64, ge=0, le=REDIS_MAX_MB)
    php_version: str = "8.4"
    app_type: str = "php"
    auto_ssl: bool = False
    active: bool = True


class PlanUpdate(ResourceLimitsIn):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    website_limit: Optional[int] = Field(default=None, ge=0, le=10000)
    storage_limit_mb: Optional[int] = Field(default=None, ge=0, le=1048576)
    database_limit: Optional[int] = Field(default=None, ge=0, le=1000)
    mailbox_limit: Optional[int] = Field(default=None, ge=0, le=10000)
    cpu_percent: Optional[int] = None
    memory_mb: Optional[int] = None
    process_limit: Optional[int] = None
    io_read_mbps: Optional[int] = None
    io_write_mbps: Optional[int] = None
    terminal_enabled: Optional[bool] = None
    open_basedir: Optional[bool] = None
    redis_memory_mb: Optional[int] = Field(default=None, ge=0, le=REDIS_MAX_MB)
    php_version: Optional[str] = None
    app_type: Optional[str] = None
    auto_ssl: Optional[bool] = None
    active: Optional[bool] = None


def _plan_dict(plan) -> dict:
    return {
        "id": plan.id,
        "slug": plan.slug,
        "name": plan.name,
        "website_limit": plan.website_limit,
        "storage_limit_mb": plan.storage_limit_mb,
        "database_limit": plan.database_limit,
        "mailbox_limit": plan.mailbox_limit,
        **{field: int(getattr(plan, field) or 0) for field in RESOURCE_LIMIT_FIELDS},
        "terminal_enabled": bool(plan.terminal_enabled),
        "open_basedir": bool(getattr(plan, "open_basedir", False)),
        "redis_memory_mb": int(plan.redis_memory_mb if plan.redis_memory_mb is not None else 64),
        "php_version": plan.php_version,
        "app_type": plan.app_type,
        "auto_ssl": plan.auto_ssl,
        "active": plan.active,
    }


def _owner(user: User):
    """Whose packages this caller manages: a reseller's own, or the admin's (None)."""
    ensure_role(user.role, Role.reseller)
    return user.id if is_reseller_role(user.role) else None


def _check_terminal(user: User, wanted) -> None:
    """A reseller hands out the terminal only if the administrator gave it to them."""
    if wanted and is_reseller_role(user.role) and not user.terminal_enabled:
        raise HTTPException(status_code=403, detail="The administrator has not allowed you to offer the terminal")


@router.get("", response_model=List[Dict[str, Any]])
def list_plans(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return [_plan_dict(p) for p in provisioning.list_plans(db, _owner(current_user))]


@router.post("", response_model=Dict[str, Any])
def create_plan(payload: PlanCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    owner_id = _owner(current_user)
    _check_terminal(current_user, payload.terminal_enabled)
    # Slugs are unique across the server: a reseller's carry its name, so two
    # resellers can both call a package "basic".
    slug = payload.slug
    if owner_id is not None and not slug.startswith(f"{current_user.username}-"):
        slug = f"{current_user.username}-{slug}"[:64]
    try:
        plan = provisioning.create_plan(
            db, slug=slug, name=payload.name,
            website_limit=payload.website_limit, storage_limit_mb=payload.storage_limit_mb,
            database_limit=payload.database_limit, mailbox_limit=payload.mailbox_limit,
            php_version=payload.php_version, app_type=payload.app_type, auto_ssl=payload.auto_ssl,
            terminal_enabled=payload.terminal_enabled,
            open_basedir=payload.open_basedir,
            redis_memory_mb=payload.redis_memory_mb,
            owner_id=owner_id, **{field: getattr(payload, field) for field in RESOURCE_LIMIT_FIELDS},
        )
        if not payload.active:
            provisioning.update_plan(db, plan.id, owner_id, active=False)
            plan = provisioning.get_plan(db, plan.id, owner_id) or plan
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _plan_dict(plan)


@router.patch("/{plan_id}", response_model=Dict[str, Any])
def update_plan(plan_id: int, payload: PlanUpdate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    owner_id = _owner(current_user)
    if provisioning.get_plan(db, plan_id, owner_id) is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    _check_terminal(current_user, payload.terminal_enabled)
    current = provisioning.get_plan(db, plan_id, owner_id)
    before = bool(getattr(current, "open_basedir", False))
    redis_before = current.redis_memory_mb
    try:
        plan = provisioning.update_plan(db, plan_id, owner_id, **payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Turned on or off for the package: every site of its accounts follows
    # (operator, 2026-10-22).
    result = _plan_dict(plan)
    if bool(plan.open_basedir) != before:
        result["open_basedir_failed"] = php_isolation.sync_plan(db, plan)
    # Its accounts' Redis takes the new memory, or goes off at 0
    # (operator, 2026-10-27).
    if plan.redis_memory_mb != redis_before:
        redis_user.sync_plan_in_background(plan.id)
    return result


@router.delete("/{plan_id}")
def delete_plan(plan_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    plan = provisioning.get_plan(db, plan_id, _owner(current_user))
    if plan is not None:
        # Accounts keep the limits it gave them: an account without a package
        # has its own Redis memory, so it takes the package's.
        db.query(User).filter(User.plan_id == plan.id).update(
            {User.redis_memory_mb: plan.redis_memory_mb}, synchronize_session=False)
    if not provisioning.delete_plan(db, plan_id, _owner(current_user)):
        raise HTTPException(status_code=404, detail="Plan not found")
    return {"ok": True}
