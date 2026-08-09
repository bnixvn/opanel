"""Provisioning API — /api/provisioning/v1

Used by WHMCS (or any billing system) to manage hosting accounts.
Auth: Bearer API token (not user JWT).
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.provisioning_deps import require_provisioning_read, require_provisioning_write
from app.core.database import get_db
from app.models.entities import ApiToken
from app.schemas.schemas import (
    ProvisioningAccountCreate,
    ProvisioningAccountOut,
    ProvisioningEnvelope,
    ProvisioningLoginOut,
    ProvisioningPackageChange,
    ProvisioningPasswordChange,
    ProvisioningPlanOut,
    ProvisioningSuspendRequest,
    ProvisioningUsageOut,
)
from app.services import provisioning

router = APIRouter(prefix="/provisioning/v1", tags=["provisioning"])


def _ok(data) -> dict:
    return {"success": True, "data": data, "error": None}


def _err(error: str, status_code: int = 400) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"success": False, "data": None, "error": error})


# --- Plans ---

@router.get("/plans", response_model=ProvisioningEnvelope)
def list_plans(
    db: Session = Depends(get_db),
    _token: ApiToken = Depends(require_provisioning_read),
):
    plans = provisioning.list_plans(db)
    return _ok([ProvisioningPlanOut.model_validate(p).model_dump() for p in plans])


# --- Accounts ---

@router.get("/accounts/{external_id}", response_model=ProvisioningEnvelope)
def get_account(
    external_id: str,
    db: Session = Depends(get_db),
    _token: ApiToken = Depends(require_provisioning_read),
):
    account = provisioning.get_account(db, external_id)
    if account is None:
        raise _err(f"Account {external_id} not found", 404)
    return _ok(provisioning._account_dict(db, account))


@router.post("/accounts", response_model=ProvisioningEnvelope, status_code=201)
def create_account(
    payload: ProvisioningAccountCreate,
    db: Session = Depends(get_db),
    _token: ApiToken = Depends(require_provisioning_write),
):
    try:
        account, is_new = provisioning.create_account(
            db,
            external_id=payload.external_id,
            username=payload.username,
            password=payload.password,
            domain=payload.domain,
            package_id=payload.package_id,
            php_version=payload.php_version,
            app_type=payload.app_type,
            install_wordpress=payload.install_wordpress,
            enable_ssl=payload.enable_ssl,
        )
    except ValueError as exc:
        raise _err(str(exc))
    except RuntimeError as exc:
        raise _err(str(exc), 500)

    return _ok(provisioning._account_dict(db, account))


@router.post("/accounts/{external_id}/suspend", response_model=ProvisioningEnvelope)
def suspend_account(
    external_id: str,
    payload: ProvisioningSuspendRequest = ProvisioningSuspendRequest(),
    db: Session = Depends(get_db),
    _token: ApiToken = Depends(require_provisioning_write),
):
    try:
        account = provisioning.suspend_account(db, external_id, payload.reason)
    except ValueError as exc:
        raise _err(str(exc), 404)
    return _ok(provisioning._account_dict(db, account))


@router.post("/accounts/{external_id}/unsuspend", response_model=ProvisioningEnvelope)
def unsuspend_account(
    external_id: str,
    db: Session = Depends(get_db),
    _token: ApiToken = Depends(require_provisioning_write),
):
    try:
        account = provisioning.unsuspend_account(db, external_id)
    except ValueError as exc:
        raise _err(str(exc), 404)
    return _ok(provisioning._account_dict(db, account))


@router.delete("/accounts/{external_id}", response_model=ProvisioningEnvelope)
def terminate_account(
    external_id: str,
    backup: bool = Query(default=True),
    db: Session = Depends(get_db),
    _token: ApiToken = Depends(require_provisioning_write),
):
    try:
        provisioning.terminate_account(db, external_id, backup=backup)
    except ValueError as exc:
        raise _err(str(exc), 404)
    return _ok({"terminated": True})


@router.patch("/accounts/{external_id}/password", response_model=ProvisioningEnvelope)
def change_password(
    external_id: str,
    payload: ProvisioningPasswordChange,
    db: Session = Depends(get_db),
    _token: ApiToken = Depends(require_provisioning_write),
):
    try:
        provisioning.change_password(db, external_id, payload.password)
    except ValueError as exc:
        raise _err(str(exc), 404)
    return _ok({"changed": True})


@router.patch("/accounts/{external_id}/package", response_model=ProvisioningEnvelope)
def change_package(
    external_id: str,
    payload: ProvisioningPackageChange,
    db: Session = Depends(get_db),
    _token: ApiToken = Depends(require_provisioning_write),
):
    try:
        account = provisioning.change_package(db, external_id, payload.package_id)
    except ValueError as exc:
        raise _err(str(exc), 400)
    return _ok(provisioning._account_dict(db, account))


@router.get("/accounts/{external_id}/usage", response_model=ProvisioningEnvelope)
def get_usage(
    external_id: str,
    db: Session = Depends(get_db),
    _token: ApiToken = Depends(require_provisioning_read),
):
    account = provisioning.get_account(db, external_id)
    if account is None:
        raise _err(f"Account {external_id} not found", 404)
    return _ok(provisioning._usage_dict(db, account))


@router.post("/accounts/{external_id}/login", response_model=ProvisioningEnvelope)
def create_login(
    external_id: str,
    db: Session = Depends(get_db),
    _token: ApiToken = Depends(require_provisioning_write),
):
    from app.core.config import settings

    panel_url = settings.panel_url or f"https://{settings.panel_domain}:{settings.panel_port}"
    try:
        result = provisioning.create_sso_login(db, external_id, panel_url)
    except ValueError as exc:
        raise _err(str(exc), 404)
    return _ok(result)
