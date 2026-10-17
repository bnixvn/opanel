"""AutoSSL: the server's switch and NAT addresses, each website's state, a check now."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.access import ensure_owner_access
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import AutosslSite, User, Website
from app.services import autossl
from app.services.audit import log_action

router = APIRouter(tags=["autossl"])


class AutosslSettingsIn(BaseModel):
    enabled: bool | None = None
    extra_ips: list[str] | str | None = None


class AutosslSiteIn(BaseModel):
    enabled: bool


def _website(db: Session, current_user: User, website_id: int) -> Website:
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    ensure_owner_access(db, current_user, website.owner_id)
    return website


@router.get("/autossl")
def get_autossl(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The switch, the extra addresses, the last pass, and every website's state."""
    ensure_role(current_user.role, Role.admin)
    states = {row.website_id: row for row in db.query(AutosslSite).all()}
    sites = [autossl.serialize(website, states.get(website.id))
             for website in db.query(Website).order_by(Website.domain).all()]
    return {**autossl.settings(), "server_ips": sorted(autossl.server_ips()), "sites": sites}


@router.put("/autossl/settings")
def put_autossl_settings(payload: AutosslSettingsIn, request: Request, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        result = autossl.save_settings(payload.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "autossl_settings", "server",
               f"{'on' if result['enabled'] else 'off'} extra={','.join(result['extra_ips']) or '-'}", request=request)
    return result


@router.post("/autossl/check")
def check_all(request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """A pass now, whatever is due: the checks, and at most one certificate."""
    ensure_role(current_user.role, Role.admin)
    figures = autossl.tick(db, force=True)
    log_action(db, current_user.id, "autossl_check", "server", str(figures), request=request)
    return figures


@router.get("/websites/{website_id}/autossl")
def get_site(website_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    website = _website(db, current_user, website_id)
    return autossl.serialize(website, db.get(AutosslSite, website.id))


@router.patch("/websites/{website_id}/autossl")
def patch_site(website_id: int, payload: AutosslSiteIn, request: Request, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    """The site's owner turns AutoSSL off for it - or on again."""
    website = _website(db, current_user, website_id)
    row = autossl._state(db, website)
    row.enabled = payload.enabled
    row.next_check = None
    db.commit()
    log_action(db, current_user.id, "autossl_site", website.domain, "on" if payload.enabled else "off", request=request)
    return autossl.serialize(website, row)


@router.post("/websites/{website_id}/autossl/check")
def check_site(website_id: int, request: Request, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    """Check the site's names now, and issue when its own name points here."""
    website = _website(db, current_user, website_id)
    result = autossl.run_site(db, website, force=True)
    log_action(db, current_user.id, "autossl_site_check", website.domain, result["status"], request=request)
    return result
