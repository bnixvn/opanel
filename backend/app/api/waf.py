from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import User, Website
from app.services import openlitespeed, waf

router = APIRouter(prefix="/waf", tags=["waf"])


class WafCustomRulesUpdate(BaseModel):
    content: str = ""


class WebsiteWafRulesUpdate(BaseModel):
    enabled_rule_ids: list[str] = Field(default_factory=list)
    custom_rules: str = ""


def _require_admin(current_user: User) -> None:
    ensure_role(current_user.role, Role.admin)


def _website_or_404(db: Session, website_id: int) -> Website:
    website = db.query(Website).filter(Website.id == website_id).first()
    if not website:
        raise HTTPException(status_code=404, detail="Website not found")
    return website


@router.get("/status")
def get_waf_status(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    return waf.status().__dict__


@router.get("/rules")
def get_waf_rules(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    status = waf.status()
    default_rules = waf.default_rules()
    custom_rules = waf.custom_rules()
    return {
        "status": status.__dict__,
        "default_rules": default_rules.stdout,
        "default_rule_definitions": waf.default_rule_definitions(),
        "custom_rules": custom_rules.stdout,
    }


@router.get("/access-logs")
def get_waf_access_logs(
    domain: str = "",
    verdict: str = Query(default="", pattern="^(|allow|block)$"),
    q: str = "",
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    lines: int = Query(default=5000, ge=1, le=20000),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    domains = [website.domain for website in db.query(Website).order_by(Website.domain.asc()).all()]
    try:
        return waf.access_log_report(domains, domain=domain, verdict=verdict, query=q, limit=limit, offset=offset, lines=lines)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/access-logs")
def clear_waf_access_logs(
    domain: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    domains = [website.domain for website in db.query(Website).order_by(Website.domain.asc()).all()]
    try:
        result = waf.clear_access_logs(domains, domain=domain)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=(result.stderr or result.stdout or "Could not clear WAF access logs").strip())
    return {**result.__dict__, "message": (result.stdout or "WAF access logs cleared.").strip()}


@router.get("/websites/{website_id}")
def get_website_waf(website_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    website = _website_or_404(db, website_id)
    return waf.site_config(website)


@router.put("/websites/{website_id}")
def save_website_waf(payload: WebsiteWafRulesUpdate, website_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    website = _website_or_404(db, website_id)
    try:
        result = waf.save_website_config(website, payload.enabled_rule_ids, payload.custom_rules)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=(result.stderr or result.stdout or "Could not save WAF rules").strip())
    db.add(website)
    db.commit()
    db.refresh(website)
    if website.waf_enabled:
        try:
            openlitespeed.update_waf_block(website.domain, True)
        except (RuntimeError, ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    data = waf.site_config(website)
    data["message"] = "Website WAF rules saved."
    return data


@router.put("/rules/custom")
def save_waf_custom_rules(payload: WafCustomRulesUpdate, current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    try:
        result = waf.save_custom_rules(payload.content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if result.returncode != 0:
        raise HTTPException(status_code=400, detail=(result.stderr or result.stdout or "Could not save WAF rules").strip())
    return result.__dict__


@router.post("/install")
def install_waf(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    return waf.install_engine().__dict__


@router.post("/update-rules")
def update_waf_rules(current_user: User = Depends(get_current_user)):
    _require_admin(current_user)
    return waf.update_rules().__dict__
