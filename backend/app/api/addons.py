"""Addons: install and operate optional panel components.

Admin only, all of it. An addon installs software as root, so there is no
end-user surface here at all -- not even a read-only one.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import User
from app.schemas.schemas import AddonServiceToggle, Fail2banSettingsIn, Fail2banUnbanIn
from app.services import addons
from app.services.audit import log_action

router = APIRouter(prefix="/addons", tags=["addons"])


def _admin(current_user: User) -> None:
    ensure_role(current_user.role, Role.admin)


# ---------------------------------------------------------------------------
# Fail2ban. Declared before /{addon_id} so the parameterised route cannot
# swallow these paths.
# ---------------------------------------------------------------------------
@router.get("/fail2ban/settings")
def get_fail2ban_settings(current_user: User = Depends(get_current_user)):
    _admin(current_user)
    return addons.fail2ban_settings()


@router.post("/fail2ban/settings")
def save_fail2ban_settings(
    payload: Fail2banSettingsIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _admin(current_user)
    try:
        result = addons.fail2ban_save_settings(payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    log_action(db, current_user.id, "addon_configure", "fail2ban", request=request)
    return result


@router.get("/fail2ban/banned")
def get_fail2ban_banned(current_user: User = Depends(get_current_user)):
    _admin(current_user)
    return {"banned": addons.fail2ban_banned()}


@router.post("/fail2ban/unban")
def post_fail2ban_unban(
    payload: Fail2banUnbanIn,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _admin(current_user)
    try:
        message = addons.fail2ban_unban(payload.address)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    log_action(db, current_user.id, "addon_unban", payload.address, request=request)
    return {"message": message, "banned": addons.fail2ban_banned()}


@router.get("/fail2ban/log")
def get_fail2ban_log(lines: int = 40, current_user: User = Depends(get_current_user)):
    _admin(current_user)
    return {"log": addons.fail2ban_log(lines)}


# ---------------------------------------------------------------------------
# The catalog
# ---------------------------------------------------------------------------
@router.get("")
def list_addons(current_user: User = Depends(get_current_user)):
    _admin(current_user)
    return {"addons": addons.catalog()}


@router.get("/{addon_id}")
def get_addon(addon_id: str, current_user: User = Depends(get_current_user)):
    _admin(current_user)
    try:
        return addons.status(addon_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{addon_id}/install")
def install_addon(
    addon_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _admin(current_user)
    try:
        result = addons.install(addon_id, actor=current_user.username)
    except ValueError as exc:
        # An unknown id is a 404; "already installed" or "busy" is a 409.
        code = 404 if not addons.is_known(addon_id) else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    log_action(db, current_user.id, "addon_install", addon_id, request=request)
    return result


@router.post("/{addon_id}/uninstall")
def uninstall_addon(
    addon_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _admin(current_user)
    try:
        result = addons.uninstall(addon_id, actor=current_user.username)
    except ValueError as exc:
        code = 404 if not addons.is_known(addon_id) else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    log_action(db, current_user.id, "addon_uninstall", addon_id, request=request)
    return result


@router.post("/{addon_id}/service")
def toggle_addon_service(
    addon_id: str,
    payload: AddonServiceToggle,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _admin(current_user)
    try:
        result = addons.set_running(addon_id, payload.running)
    except ValueError as exc:
        code = 404 if not addons.is_known(addon_id) else 409
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    log_action(
        db, current_user.id,
        "addon_start" if payload.running else "addon_stop",
        addon_id, request=request,
    )
    return result
