"""The server's own pages behind the top bar's status chips: Process monitor,
RAM usage, Disk usage and Traffic. Administrators only."""
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.core.permissions import Role, ensure_role
from app.models.entities import User
from app.services import server_monitor

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/processes")
def get_processes(current_user: User = Depends(get_current_user)):
    # Every tenant's command lines are in here: the administrator's alone.
    ensure_role(current_user.role, Role.admin)
    try:
        return server_monitor.processes()
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/memory")
def get_memory(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return server_monitor.memory()


@router.get("/disk")
def get_disk(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return server_monitor.disk()


@router.post("/disk/scan")
def scan_disk(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return {"started": server_monitor.start_scan()}


@router.get("/traffic")
def get_traffic(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return server_monitor.traffic()
