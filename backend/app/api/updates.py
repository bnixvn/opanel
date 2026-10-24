from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_current_user
from app.core.permissions import Role, ensure_role
from app.models.entities import User
from app.schemas.schemas import PanelAutoUpdateConfig, ServiceUpdateRun, SystemAutoUpdateConfig
from app.services import changelog, service_versions, updates

router = APIRouter(prefix="/updates", tags=["updates"])


@router.get("/status")
def get_update_status(refresh: bool = Query(default=False), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return updates.status(force_refresh=refresh)


@router.get("/changelog")
def get_changelog(page: int = Query(default=1, ge=1), per_page: int = Query(default=10, ge=1, le=50),
                  newer: bool = Query(default=False), refresh: bool = Query(default=False),
                  current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return changelog.page(page=page, per_page=per_page, newer_only=newer, refresh=refresh)


@router.post("/os/run")
def run_os_update(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return updates.run_os_update().__dict__


@router.post("/os/auto")
def configure_os_auto_update(payload: SystemAutoUpdateConfig, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return updates.configure_os_auto_update(payload.enabled, payload.mode, payload.auto_reboot).__dict__


@router.post("/panel/run")
def run_panel_update(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return updates.run_panel_update().__dict__


@router.post("/panel/auto")
def configure_panel_auto_update(payload: PanelAutoUpdateConfig, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return updates.configure_panel_auto_update(payload.enabled, payload.time).__dict__


@router.get("/services")
def get_service_versions(refresh: bool = Query(default=False), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        return service_versions.status(refresh=refresh)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/services/run")
def run_service_update(payload: ServiceUpdateRun, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    try:
        return service_versions.run(None if payload.all else payload.services)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
