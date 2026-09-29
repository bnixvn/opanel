"""DNS Manager addon: zones, records, nameserver settings.

Customers manage the zones of their own websites' domains; administrators
every zone. The rules live in app.services.dns_manager -- this module maps its
errors to HTTP and writes the audit log.
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import User
from app.services import dns_manager
from app.services.audit import log_action

router = APIRouter(prefix="/dns", tags=["dns"])


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except dns_manager.NotInstalled as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class ZoneIn(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    owner_id: Optional[int] = Field(default=None, gt=0)


class RecordIn(BaseModel):
    name: str = Field(default="@", max_length=253)
    type: str = Field(min_length=1, max_length=8)
    value: str = Field(min_length=1, max_length=4000)
    priority: Optional[int] = Field(default=None, ge=0, le=65535)
    ttl: Optional[int] = Field(default=None, ge=60, le=604800)


class RecordRef(BaseModel):
    name: str = Field(max_length=253)
    type: str = Field(min_length=1, max_length=8)
    content: str = Field(min_length=1, max_length=5000)


class RecordUpdate(BaseModel):
    old: RecordRef
    new: RecordIn


class DnsSettingsIn(BaseModel):
    ns1: Optional[str] = Field(default=None, max_length=253)
    ns2: Optional[str] = Field(default=None, max_length=253)
    hostmaster: Optional[str] = Field(default=None, max_length=254)
    default_ttl: Optional[int] = None


@router.get("/overview")
def get_overview(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return dns_manager.overview(db, current_user)


@router.get("/zones")
def get_zones(q: str = "", page: int = 1, per_page: int = 50, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)):
    return dns_manager.list_zones(db, current_user, q, page, per_page)


@router.post("/zones")
def post_zone(payload: ZoneIn, request: Request, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)):
    row = _call(dns_manager.create_zone, db, current_user, payload.domain, payload.owner_id)
    log_action(db, current_user.id, "dns_zone_add", row.name, f"owner={row.owner.username}", request=request)
    return dns_manager.zone_out(row)


@router.delete("/zones/{zone_id}")
def delete_zone(zone_id: int, request: Request, confirm: str = "", db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    row = _call(dns_manager.get_zone, db, current_user, zone_id)
    # Every record of the domain goes with it, so the name has to be typed.
    if confirm.strip().lower().rstrip(".") != row.name:
        raise HTTPException(status_code=400, detail="Type the domain name to confirm")
    name = _call(dns_manager.delete_zone, db, current_user, zone_id)
    log_action(db, current_user.id, "dns_zone_delete", name, request=request)
    return {"ok": True, "name": name}


@router.get("/zones/{zone_id}")
def get_zone(zone_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _call(dns_manager.records, db, current_user, zone_id)


@router.get("/zones/{zone_id}/delegation")
def get_delegation(zone_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = _call(dns_manager.get_zone, db, current_user, zone_id)
    return dns_manager.delegation(row)


@router.post("/zones/{zone_id}/records")
def post_record(zone_id: int, payload: RecordIn, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    result = _call(dns_manager.add_record, db, current_user, zone_id, payload.model_dump())
    log_action(db, current_user.id, "dns_record_add", result["zone"]["name"],
               f"{payload.type} {payload.name}", request=request)
    return result


@router.put("/zones/{zone_id}/records")
def put_record(zone_id: int, payload: RecordUpdate, request: Request, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    result = _call(dns_manager.update_record, db, current_user, zone_id, payload.old.model_dump(),
                   payload.new.model_dump())
    log_action(db, current_user.id, "dns_record_update", result["zone"]["name"],
               f"{payload.new.type} {payload.new.name}", request=request)
    return result


@router.post("/zones/{zone_id}/records/delete")
def delete_record(zone_id: int, payload: RecordRef, request: Request, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    result = _call(dns_manager.delete_record, db, current_user, zone_id, payload.model_dump())
    log_action(db, current_user.id, "dns_record_delete", result["zone"]["name"],
               f"{payload.type} {payload.name}", request=request)
    return result


@router.post("/zones/{zone_id}/defaults")
def post_defaults(zone_id: int, request: Request, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    result = _call(dns_manager.restore_defaults, db, current_user, zone_id)
    log_action(db, current_user.id, "dns_zone_defaults", result["zone"]["name"], request=request)
    return result


@router.get("/settings")
def get_settings(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return dns_manager.current_settings()


@router.put("/settings")
def put_settings(payload: DnsSettingsIn, request: Request, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    values = {key: value for key, value in payload.model_dump().items() if value is not None}
    result = _call(dns_manager.save_settings, db, values)
    log_action(db, current_user.id, "dns_settings", ", ".join(sorted(values)), request=request)
    return result


@router.post("/sync")
def post_sync(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """A zone for each of the caller's domains that has none (every domain for
    an administrator); the DNS page asks for this as it opens."""
    created = _call(dns_manager.sync_zones, db, None if dns_manager._is_admin(current_user) else current_user.id)
    if created:
        log_action(db, current_user.id, "dns_zones_sync", f"{len(created)} zones", ", ".join(created)[:500],
                   request=request)
    return {"created": created}
