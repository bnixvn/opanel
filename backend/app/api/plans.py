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
from app.services import provisioning

router = APIRouter(prefix="/plans", tags=["plans"])


class PlanCreate(BaseModel):
    slug: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=128)
    website_limit: int = Field(default=1, ge=0, le=10000)
    storage_limit_mb: int = Field(default=1024, ge=0, le=1048576)
    database_limit: int = Field(default=10, ge=0, le=1000)
    mailbox_limit: int = Field(default=10, ge=0, le=10000)
    php_version: str = "8.4"
    app_type: str = "php"
    auto_ssl: bool = False
    active: bool = True


class PlanUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)
    website_limit: Optional[int] = Field(default=None, ge=0, le=10000)
    storage_limit_mb: Optional[int] = Field(default=None, ge=0, le=1048576)
    database_limit: Optional[int] = Field(default=None, ge=0, le=1000)
    mailbox_limit: Optional[int] = Field(default=None, ge=0, le=10000)
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
        "php_version": plan.php_version,
        "app_type": plan.app_type,
        "auto_ssl": plan.auto_ssl,
        "active": plan.active,
    }


def _owner(user: User):
    """Whose packages this caller manages: a reseller's own, or the admin's (None)."""
    ensure_role(user.role, Role.reseller)
    return user.id if is_reseller_role(user.role) else None


@router.get("", response_model=List[Dict[str, Any]])
def list_plans(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return [_plan_dict(p) for p in provisioning.list_plans(db, _owner(current_user))]


@router.post("", response_model=Dict[str, Any])
def create_plan(payload: PlanCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    owner_id = _owner(current_user)
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
            owner_id=owner_id,
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
    try:
        plan = provisioning.update_plan(db, plan_id, owner_id, **payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _plan_dict(plan)


@router.delete("/{plan_id}")
def delete_plan(plan_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if not provisioning.delete_plan(db, plan_id, _owner(current_user)):
        raise HTTPException(status_code=404, detail="Plan not found")
    return {"ok": True}
