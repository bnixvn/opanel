"""Email addon: domains, mailboxes, forwarders, webmail sign-on and settings.

End users see and change only the mail of their own domains; administrators
see every domain. The rules live in app.services.mail -- this module maps its
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
from app.services import addons, mail
from app.services.audit import log_action

router = APIRouter(prefix="/mail", tags=["mail"])


def _call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except mail.NotInstalled as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


class DomainIn(BaseModel):
    domain: str = Field(min_length=3, max_length=253)
    owner_id: Optional[int] = Field(default=None, gt=0)


class DomainUpdate(BaseModel):
    catch_all: str = Field(default="", max_length=254)


class WebmailHostIn(BaseModel):
    enabled: bool


class MailboxIn(BaseModel):
    domain_id: int = Field(gt=0)
    local_part: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=72)
    quota_mb: Optional[int] = Field(default=None, ge=0, le=mail.MAX_QUOTA_MB)


class MailboxUpdate(BaseModel):
    password: Optional[str] = Field(default=None, min_length=8, max_length=72)
    quota_mb: Optional[int] = Field(default=None, ge=0, le=mail.MAX_QUOTA_MB)
    enabled: Optional[bool] = None


class ForwarderIn(BaseModel):
    domain_id: int = Field(gt=0)
    local_part: str = Field(min_length=1, max_length=64)
    destinations: list[str] = Field(min_length=1, max_length=mail.MAX_DESTINATIONS)


class ForwarderUpdate(BaseModel):
    destinations: list[str] = Field(min_length=1, max_length=mail.MAX_DESTINATIONS)


class MailSettingsIn(BaseModel):
    smarthost_enabled: Optional[bool] = None
    smarthost_host: Optional[str] = Field(default=None, max_length=253)
    smarthost_port: Optional[int] = Field(default=None, ge=1, le=65535)
    smarthost_username: Optional[str] = Field(default=None, max_length=255)
    # Empty or missing keeps the stored one.
    smarthost_password: Optional[str] = Field(default=None, max_length=255)
    auth_rate_per_hour: Optional[int] = Field(default=None, ge=0, le=100000)
    local_rate_per_hour: Optional[int] = Field(default=None, ge=0, le=100000)
    max_message_mb: Optional[int] = Field(default=None, ge=1, le=200)
    spam_header_score: Optional[float] = Field(default=None, ge=1, le=100)
    spam_reject_score: Optional[float] = Field(default=None, ge=1, le=100)
    greylisting: Optional[bool] = None
    default_quota_mb: Optional[int] = Field(default=None, ge=1, le=mail.MAX_USER_QUOTA_MB)


# ---------------------------------------------------------------------------
# Overview and domains
# ---------------------------------------------------------------------------
@router.get("/overview")
def get_overview(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return mail.overview(db, current_user)


@router.post("/domains")
def post_domain(payload: DomainIn, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    row = _call(mail.add_domain, db, current_user, payload.domain, payload.owner_id)
    log_action(db, current_user.id, "mail_domain_add", row.domain, f"owner={row.owner.username}", request=request)
    return mail.domain_out(row)


@router.put("/domains/{domain_id}")
def put_domain(domain_id: int, payload: DomainUpdate, request: Request, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    row = _call(mail.set_catch_all, db, current_user, domain_id, payload.catch_all)
    log_action(db, current_user.id, "mail_catch_all", row.domain, row.catch_all or "off", request=request)
    return mail.domain_out(row)


@router.delete("/domains/{domain_id}")
def delete_domain(domain_id: int, request: Request, confirm: str = "", db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    row = _call(mail.get_domain, db, current_user, domain_id)
    # Every mailbox of the domain goes with it, so the name has to be typed.
    if confirm.strip().lower() != row.domain:
        raise HTTPException(status_code=400, detail="Type the domain name to confirm")
    name = _call(mail.delete_domain, db, current_user, domain_id)
    log_action(db, current_user.id, "mail_domain_delete", name, request=request)
    return {"ok": True, "domain": name}


@router.get("/domains/{domain_id}/dns")
def get_domain_dns(domain_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    row = _call(mail.get_domain, db, current_user, domain_id)
    return {"domain": row.domain, "records": mail.check_dns(row)}


@router.post("/domains/{domain_id}/dkim/rotate")
def post_rotate_dkim(domain_id: int, request: Request, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    row = _call(mail.rotate_dkim, db, current_user, domain_id)
    log_action(db, current_user.id, "mail_dkim_rotate", row.domain, request=request)
    return {"domain": row.domain, "records": mail.dns_records(row)}


@router.post("/domains/{domain_id}/webmail-host")
def post_webmail_host(domain_id: int, payload: WebmailHostIn, request: Request, db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    row = _call(mail.set_webmail_host, db, current_user, domain_id, payload.enabled)
    log_action(db, current_user.id, "mail_webmail_host", f"webmail.{row.domain}",
               "on" if payload.enabled else "off", request=request)
    return mail.domain_out(row)


# ---------------------------------------------------------------------------
# Mailboxes
# ---------------------------------------------------------------------------
@router.get("/mailboxes")
def get_mailboxes(domain_id: Optional[int] = None, q: str = "", page: int = 1, per_page: int = 50,
                  db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return mail.list_mailboxes(db, current_user, domain_id, q, page, per_page)


@router.post("/mailboxes")
def post_mailbox(payload: MailboxIn, request: Request, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    row = _call(mail.create_mailbox, db, current_user, payload.domain_id, payload.local_part,
                payload.password, payload.quota_mb)
    log_action(db, current_user.id, "mailbox_create", row.address, f"quota={row.quota_mb}MB", request=request)
    return mail.mailbox_out(row)


@router.put("/mailboxes/{mailbox_id}")
def put_mailbox(mailbox_id: int, payload: MailboxUpdate, request: Request, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    row = _call(mail.update_mailbox, db, current_user, mailbox_id, password=payload.password,
                quota_mb=payload.quota_mb, enabled=payload.enabled)
    changes = [name for name, value in (("password", payload.password), ("quota", payload.quota_mb),
                                        ("enabled", payload.enabled)) if value is not None]
    log_action(db, current_user.id, "mailbox_update", row.address, ",".join(changes), request=request)
    return mail.mailbox_out(row)


@router.delete("/mailboxes/{mailbox_id}")
def delete_mailbox(mailbox_id: int, request: Request, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    address = _call(mail.delete_mailbox, db, current_user, mailbox_id)
    log_action(db, current_user.id, "mailbox_delete", address, request=request)
    return {"ok": True, "address": address}


@router.post("/mailboxes/{mailbox_id}/webmail")
def post_mailbox_webmail(mailbox_id: int, request: Request, db: Session = Depends(get_db),
                         current_user: User = Depends(get_current_user)):
    url = _call(mail.sso_url, db, current_user, mailbox_id)
    row = mail.get_mailbox(db, current_user, mailbox_id)
    log_action(db, current_user.id, "mailbox_webmail_sso", row.address, request=request)
    return {"url": url, "expires_in": mail.SSO_TTL_SECONDS}


# ---------------------------------------------------------------------------
# Forwarders
# ---------------------------------------------------------------------------
@router.get("/forwarders")
def get_forwarders(domain_id: Optional[int] = None, q: str = "", page: int = 1, per_page: int = 50,
                   db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return mail.list_forwarders(db, current_user, domain_id, q, page, per_page)


@router.post("/forwarders")
def post_forwarder(payload: ForwarderIn, request: Request, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    row = _call(mail.create_forwarder, db, current_user, payload.domain_id, payload.local_part,
                payload.destinations)
    log_action(db, current_user.id, "mail_forwarder_create", row.address, ", ".join(row.destination_list),
               request=request)
    return mail.forwarder_out(row)


@router.put("/forwarders/{forwarder_id}")
def put_forwarder(forwarder_id: int, payload: ForwarderUpdate, request: Request, db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    row = _call(mail.update_forwarder, db, current_user, forwarder_id, payload.destinations)
    log_action(db, current_user.id, "mail_forwarder_update", row.address, ", ".join(row.destination_list),
               request=request)
    return mail.forwarder_out(row)


@router.delete("/forwarders/{forwarder_id}")
def delete_forwarder(forwarder_id: int, request: Request, db: Session = Depends(get_db),
                     current_user: User = Depends(get_current_user)):
    address = _call(mail.delete_forwarder, db, current_user, forwarder_id)
    log_action(db, current_user.id, "mail_forwarder_delete", address, request=request)
    return {"ok": True, "address": address}


# ---------------------------------------------------------------------------
# Server settings (administrators)
# ---------------------------------------------------------------------------
@router.get("/settings")
def get_settings(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return {"settings": mail.current_settings(), "queue": mail.queue_size(), "hostname": mail.hostname(),
            "status": addons.status(mail.ADDON_ID)}


@router.put("/settings")
def put_settings(payload: MailSettingsIn, request: Request, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    values = {key: value for key, value in payload.model_dump().items() if value is not None}
    result = _call(mail.save_settings, values)
    log_action(db, current_user.id, "mail_settings",
               "relay " + (result["smarthost_host"] if result["smarthost_enabled"] else "off"), request=request)
    return {"settings": result}


@router.get("/log")
def get_log(lines: int = 100, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    return {"log": mail.mail_log(lines)}
