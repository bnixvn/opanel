from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.core.permissions import Role, ensure_role
from app.models.entities import User
from app.schemas.schemas import ServiceAction
from app.services.system import install_wordpress_stack, list_services, resource_usage, service_action, system_info

router = APIRouter(prefix="/services", tags=["services"])


@router.get("/system-info")
def get_system_info(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    return system_info()


@router.get("/resource-usage")
def get_resource_usage(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.end_user)
    return resource_usage()


@router.get("/list")
def get_services(current_user: User = Depends(get_current_user)):
    # Admin only. The service inventory names every daemon on the box and its
    # unit state; a hosting customer has no use for it and it is not theirs to
    # see. system-info and resource-usage stay open because the dashboard's own
    # CPU/RAM/disk cards read them.
    ensure_role(current_user.role, Role.admin)
    return {"services": list_services()}


@router.post("/action")
def run_service_action(payload: ServiceAction, current_user: User = Depends(get_current_user)):
    # Including "status": it was the read half of the page end users no longer
    # have, and leaving it open would have let them enumerate services anyway.
    ensure_role(current_user.role, Role.admin)
    try:
        result = service_action(payload.name, payload.action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return result.__dict__


@router.post("/install-wordpress-stack")
def install_stack(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    result = install_wordpress_stack()
    return result.__dict__
