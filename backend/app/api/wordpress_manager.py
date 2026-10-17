"""/api/wordpress - the WordPress Manager addon (2026-10-17, as SNPanel's).

Every role, while the addon is on (404 otherwise, as Git's routes). A caller
reaches the WordPress in the websites it may manage: its own, its customers'
for a reseller, every one for an administrator. Whatever changes a site is a
task (the answer is the task); the sign-in link, a new password and a search
of wordpress.org answer at once.

Two answers carry a secret, once, and are never logged: the sign-in link and
a password the panel made.
"""
from __future__ import annotations

import json
from typing import List, Optional

import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.access import ensure_owner_access, scope_owner
from app.core.database import get_db
from app.core.permissions import is_admin_role
from app.models.entities import User, Website, WpInstall, WpJob
from app.services import wp_extras, wp_manager
from app.services.audit import log_action

# Where an uploaded .zip waits for the helper to hand it to the account (wp-upload-place).
UPLOAD_DIR = Path("/var/lib/opanel/wp-uploads")


def _require_enabled() -> None:
    """An addon: stopped or not installed, its routes answer as if they were not there."""
    if not wp_manager.enabled():
        raise HTTPException(status_code=404, detail="The WordPress Manager addon is not installed")


router = APIRouter(prefix="/wordpress", tags=["wordpress"], dependencies=[Depends(_require_enabled)])
# Whether the addon is on: asked by every role's page, whatever the answer.
info_router = APIRouter(tags=["wordpress"])


@info_router.get("/wordpress/info")
def wordpress_info(current_user: User = Depends(get_current_user)):
    return {"enabled": wp_manager.enabled()}


class ScanIn(BaseModel):
    website_id: Optional[int] = None


class BulkIn(BaseModel):
    install_ids: List[int] = Field(default_factory=list, max_length=500)


class CoreIn(BaseModel):
    action: str
    version: Optional[str] = None
    mode: Optional[str] = None
    restore_point: bool = True


class SwitchIn(BaseModel):
    on: bool


class ItemsIn(BaseModel):
    action: str
    slugs: List[str] = Field(default_factory=list, max_length=50)
    activate: bool = False
    restore_point: bool = True


class PointIn(BaseModel):
    name: str = Field(pattern=r"^[0-9]{8}T[0-9]{6}$")


class UserIn(BaseModel):
    user_id: Optional[int] = None


class HardenIn(BaseModel):
    item: str
    revert: bool = False


class CloneIn(BaseModel):
    website_id: int = Field(gt=0)


class LsCacheIn(BaseModel):
    action: str = Field(pattern="^(install|activate|deactivate|purge)$")


# --- Shapes ------------------------------------------------------------------------------

def _job_out(job: WpJob) -> dict:
    return {
        "id": job.id, "install_id": job.install_id, "website_id": job.website_id, "kind": job.kind,
        "label": job.label, "params": json.loads(job.params or "{}"), "status": job.status, "stage": job.stage,
        "error": job.error, "log": job.log,
        "created_at": job.created_at.isoformat() + "Z" if job.created_at else None,
        "finished_at": job.finished_at.isoformat() + "Z" if job.finished_at else None,
    }


def _install_out(install: WpInstall, website: Optional[Website], owners: dict[int, str]) -> dict:
    return {
        "id": install.id, "website_id": install.website_id, "owner_id": install.owner_id,
        "owner_username": owners.get(install.owner_id, ""),
        "domain": website.domain if website else "", "php_version": website.php_version if website else "",
        "rel_path": install.rel_path, "path": install.path, "title": install.title, "url": install.url,
        "status": install.status, "state": install.state,
        "core_version": install.core_version, "core_update": install.core_update,
        "plugins_total": install.plugins_total, "plugins_updates": install.plugins_updates,
        "themes_total": install.themes_total, "themes_updates": install.themes_updates,
        "checked": install.last_checked_at is not None,
        "security_warnings": wp_manager.security_warnings(json.loads(install.inventory or "{}")),
        "last_checked_at": install.last_checked_at.isoformat() + "Z" if install.last_checked_at else None,
        "last_error": install.last_error,
    }


def _owners(db: Session, ids) -> dict[int, str]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {user.id: user.username for user in db.query(User).filter(User.id.in_(ids)).all()}


def _websites_for(db: Session, actor: User) -> list[Website]:
    return scope_owner(db.query(Website), Website.owner_id, db, actor).order_by(Website.domain).all()


def _install_for(db: Session, actor: User, install_id: int) -> tuple[WpInstall, Website]:
    install = db.get(WpInstall, install_id)
    website = db.get(Website, install.website_id) if install else None
    if not install or not website:
        raise HTTPException(status_code=404, detail="WordPress not found")
    ensure_owner_access(db, actor, website.owner_id)
    return install, website


def _usable(install: WpInstall) -> None:
    if install.status != "ok" or install.state != "installed":
        raise HTTPException(status_code=409, detail="This WordPress is not set up, or the last scan did not find it")


def _job_answer(job: WpJob) -> dict:
    return {"job": _job_out(job)}


# --- Reading -----------------------------------------------------------------------------

@router.get("")
def overview(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    wp_manager.settle_stale(db)
    installs = wp_manager.installs_for(db, current_user)
    websites = _websites_for(db, current_user)
    by_id = {site.id: site for site in websites}
    owners = _owners(db, [install.owner_id for install in installs] + [site.owner_id for site in websites])
    ids = [install.id for install in installs]
    active = db.query(WpJob).filter(WpJob.status.in_(("queued", "running")))
    active = active.filter(WpJob.owner_id.in_({site.owner_id for site in websites})) if not is_admin_role(current_user.role) else active
    return {
        "items": [_install_out(install, by_id.get(install.website_id), owners) for install in installs if install.website_id in by_id],
        "websites": [{"id": site.id, "domain": site.domain, "owner_id": site.owner_id, "owner_username": owners.get(site.owner_id, ""),
                      "app_type": site.app_type} for site in websites],
        "owners": sorted(({"id": i, "username": n} for i, n in owners.items()), key=lambda item: item["username"]),
        "active_jobs": [_job_out(job) for job in active.order_by(WpJob.id).all() if job.install_id in ids or job.kind == "scan"],
    }


@router.get("/installs/{install_id}")
def detail(install_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    wp_manager.settle_stale(db)
    install, website = _install_for(db, current_user, install_id)
    out = _install_out(install, website, _owners(db, [install.owner_id]))
    out["inventory"] = json.loads(install.inventory or "{}")
    out["security"] = {"items": wp_manager.security_items(out["inventory"]),
                       "scanned_at": (out["inventory"].get("security_full") or {}).get("scanned_at")}
    jobs = db.query(WpJob).filter(WpJob.install_id == install.id).order_by(WpJob.id.desc()).limit(20).all()
    out["jobs"] = [_job_out(job) for job in jobs]
    # A staging copy names its live site; a live site lists its copies.
    live = db.get(WpInstall, install.staging_of) if install.staging_of else None
    live_site = db.get(Website, live.website_id) if live else None
    out["staging_of"] = {"id": live.id, "domain": live_site.domain, "rel_path": live.rel_path} if live and live_site else None
    copies = db.query(WpInstall).filter(WpInstall.staging_of == install.id).all()
    sites = {site.id: site for site in db.query(Website).filter(Website.id.in_([c.website_id for c in copies])).all()} if copies else {}
    out["staging_copies"] = [{"id": copy.id, "domain": sites[copy.website_id].domain, "rel_path": copy.rel_path,
                              "created_at": copy.created_at.isoformat() + "Z" if getattr(copy, "created_at", None) else None}
                             for copy in copies if copy.website_id in sites]
    return out


@router.get("/jobs/{job_id}")
def read_job(job_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    wp_manager.settle_stale(db)
    job = db.get(WpJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Task not found")
    if job.owner_id is not None:
        ensure_owner_access(db, current_user, job.owner_id)
    elif not is_admin_role(current_user.role):
        raise HTTPException(status_code=404, detail="Task not found")
    return _job_out(job)


@router.get("/installs/{install_id}/search")
def search(install_id: int, kind: str = Query(pattern="^(plugin|theme)$"), q: str = Query(min_length=2, max_length=80),
           db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _install_for(db, current_user, install_id)
    try:
        return {"items": wp_manager.search_wordpress_org(kind, q)}
    except wp_manager.WpError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --- Tasks --------------------------------------------------------------------------------

@router.post("/scan")
def scan(payload: ScanIn, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    websites = _websites_for(db, current_user)
    if payload.website_id is not None:
        websites = [site for site in websites if site.id == payload.website_id]
        if not websites:
            raise HTTPException(status_code=404, detail="Website not found")
    label = websites[0].domain if len(websites) == 1 else f"{len(websites)} website(s)"
    job = wp_manager.submit(db, kind="scan", actor=current_user, label=label,
                            params={"website_ids": [site.id for site in websites]},
                            website=websites[0] if len(websites) == 1 else None)
    log_action(db, current_user.id, "wordpress_scan", label, request=request)
    return _job_answer(job)


@router.post("/bulk-update")
def bulk_update(payload: BulkIn, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    jobs = []
    for install_id in dict.fromkeys(payload.install_ids):
        install, website = _install_for(db, current_user, install_id)
        _usable(install)
        jobs.append(wp_manager.submit(db, kind="update", actor=current_user, label=install.url or website.domain, install=install))
    log_action(db, current_user.id, "wordpress_bulk_update", f"{len(jobs)} site(s)", request=request)
    return {"jobs": [_job_out(job) for job in jobs]}


def _task(db: Session, current_user: User, install_id: int, kind: str, params: dict, request: Request) -> dict:
    install, website = _install_for(db, current_user, install_id)
    if kind != "refresh":
        _usable(install)
    job = wp_manager.submit(db, kind=kind, actor=current_user, label=install.url or website.domain, params=params, install=install)
    log_action(db, current_user.id, f"wordpress_{kind}", install.url or website.domain,
               json.dumps(params)[:200] if params else "", request=request)
    return _job_answer(job)


@router.get("/installs/{install_id}/clone-targets")
def clone_targets(install_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """The account's other websites, each with whether it can take a staging copy."""
    install, website = _install_for(db, current_user, install_id)
    return {"websites": wp_extras.clone_targets(db, install, website)}


@router.post("/installs/{install_id}/clone")
def clone(install_id: int, payload: CloneIn, request: Request, db: Session = Depends(get_db),
          current_user: User = Depends(get_current_user)):
    install, website = _install_for(db, current_user, install_id)
    target = db.get(Website, payload.website_id)
    if not target:
        raise HTTPException(status_code=404, detail="Website not found")
    ensure_owner_access(db, current_user, target.owner_id)
    allowed = {item["id"]: item for item in wp_extras.clone_targets(db, install, website)}
    if payload.website_id not in allowed or not allowed[payload.website_id]["ok"]:
        raise HTTPException(status_code=400, detail=allowed.get(payload.website_id, {}).get("reason") or "That website cannot take a copy")
    return _task(db, current_user, install_id, "clone", {"website_id": payload.website_id, "domain": target.domain}, request)


@router.post("/installs/{install_id}/push")
def push(install_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    install, _ = _install_for(db, current_user, install_id)
    if not install.staging_of:
        raise HTTPException(status_code=400, detail="This WordPress is not a staging copy")
    return _task(db, current_user, install_id, "push", {}, request)


@router.post("/installs/{install_id}/lscache")
def lscache(install_id: int, payload: LsCacheIn, request: Request, db: Session = Depends(get_db),
            current_user: User = Depends(get_current_user)):
    return _task(db, current_user, install_id, "lscache", {"action": payload.action}, request)


async def _take_upload(file: UploadFile) -> str:
    """The .zip saved where the helper takes it from, its size and kind checked."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = UPLOAD_DIR / f"{secrets.token_hex(12)}.zip"
    size = 0
    try:
        with target.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > wp_extras.UPLOAD_MAX_BYTES:
                    raise HTTPException(status_code=413, detail="The file is larger than 64 MB")
                out.write(chunk)
        with target.open("rb") as check:
            if check.read(4) != b"PK\x03\x04":
                raise HTTPException(status_code=400, detail="That is not a .zip file")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return str(target)


@router.post("/installs/{install_id}/plugins/upload")
async def upload_plugin(install_id: int, request: Request, file: UploadFile = File(...), activate: str = Form("0"),
                        db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    install, _ = _install_for(db, current_user, install_id)
    _usable(install)
    staged = await _take_upload(file)
    return _task(db, current_user, install_id, "upload",
                 {"kind": "plugin", "file": staged, "name": file.filename or "plugin.zip", "activate": activate in {"1", "true"}}, request)


@router.post("/installs/{install_id}/themes/upload")
async def upload_theme(install_id: int, request: Request, file: UploadFile = File(...), activate: str = Form("0"),
                       db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    install, _ = _install_for(db, current_user, install_id)
    _usable(install)
    staged = await _take_upload(file)
    return _task(db, current_user, install_id, "upload",
                 {"kind": "theme", "file": staged, "name": file.filename or "theme.zip", "activate": activate in {"1", "true"}}, request)


@router.post("/installs/{install_id}/refresh")
def refresh(install_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _task(db, current_user, install_id, "refresh", {}, request)


@router.post("/installs/{install_id}/update")
def update_everything(install_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _task(db, current_user, install_id, "update", {}, request)


@router.post("/installs/{install_id}/core")
def core(install_id: int, payload: CoreIn, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if payload.action not in ("update", "auto_update"):
        raise HTTPException(status_code=400, detail="Unknown core action")
    if payload.action == "auto_update" and payload.mode not in ("off", "minor", "all"):
        raise HTTPException(status_code=400, detail="Choose off, minor or all")
    return _task(db, current_user, install_id, "core", payload.model_dump(exclude_none=True), request)


@router.post("/installs/{install_id}/maintenance")
def maintenance(install_id: int, payload: SwitchIn, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _task(db, current_user, install_id, "maintenance", {"on": payload.on}, request)


@router.post("/installs/{install_id}/debug")
def debug(install_id: int, payload: SwitchIn, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _task(db, current_user, install_id, "debug", {"on": payload.on}, request)


def _items(kind: str, install_id: int, payload: ItemsIn, request: Request, db: Session, current_user: User) -> dict:
    if payload.action not in wp_manager.ITEM_ACTIONS[kind]:
        raise HTTPException(status_code=400, detail=f"Unknown {kind} action")
    bad = [slug for slug in payload.slugs if not wp_manager.SLUG_RE.fullmatch(slug)]
    if bad or (payload.action != "update_all" and not payload.slugs):
        raise HTTPException(status_code=400, detail=f"Name the {kind} by its folder (letters, digits, dots, dashes)")
    return _task(db, current_user, install_id, f"{kind}s", payload.model_dump(), request)


@router.post("/installs/{install_id}/plugins")
def plugins(install_id: int, payload: ItemsIn, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _items("plugin", install_id, payload, request, db, current_user)


@router.post("/installs/{install_id}/themes")
def themes(install_id: int, payload: ItemsIn, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _items("theme", install_id, payload, request, db, current_user)


@router.post("/installs/{install_id}/points")
def point_create(install_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _task(db, current_user, install_id, "point_create", {}, request)


@router.post("/installs/{install_id}/points/restore")
def point_restore(install_id: int, payload: PointIn, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _task(db, current_user, install_id, "point_restore", payload.model_dump(), request)


@router.post("/installs/{install_id}/points/delete")
def point_delete(install_id: int, payload: PointIn, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _task(db, current_user, install_id, "point_delete", payload.model_dump(), request)


@router.post("/installs/{install_id}/security/scan")
def security_scan(install_id: int, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _task(db, current_user, install_id, "security_scan", {}, request)


@router.post("/installs/{install_id}/security/apply")
def harden(install_id: int, payload: HardenIn, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if payload.item not in wp_manager.HARDEN_ITEMS:
        raise HTTPException(status_code=400, detail="Unknown security item")
    return _task(db, current_user, install_id, "harden", payload.model_dump(), request)


# --- Answered at once ---------------------------------------------------------------------

@router.post("/installs/{install_id}/login")
def login(install_id: int, payload: UserIn, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    install, website = _install_for(db, current_user, install_id)
    _usable(install)
    try:
        url = wp_manager.login_url(install, website, payload.user_id)
    except wp_manager.WpError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "wordpress_login", install.url or website.domain,
               f"user {payload.user_id}" if payload.user_id else "", request=request)
    return {"url": url}


@router.post("/installs/{install_id}/password")
def password(install_id: int, payload: UserIn, request: Request, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    install, website = _install_for(db, current_user, install_id)
    _usable(install)
    if payload.user_id is None:
        raise HTTPException(status_code=400, detail="Choose an administrator")
    try:
        login_name, new = wp_manager.new_password(install, website, payload.user_id)
    except wp_manager.WpError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_action(db, current_user.id, "wordpress_password", install.url or website.domain, f"user {login_name}", request=request)
    return {"login": login_name, "password": new}
