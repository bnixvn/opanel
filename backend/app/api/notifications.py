"""The Notifications addon's page: admin channel settings, each user's own
choices, their Telegram link, and the send log."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.deps import get_current_user
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.models.entities import NotificationMessage, User
from app.services import notifications
from app.services.audit import log_action

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _require_enabled() -> None:
    if not notifications.enabled():
        raise HTTPException(status_code=409, detail="Notifications are not enabled on this panel")


class NotificationSettingsUpdate(BaseModel):
    language: Optional[str] = None
    email_enabled: Optional[bool] = None
    smtp_host: Optional[str] = Field(default=None, max_length=253)
    smtp_port: Optional[int] = None
    smtp_security: Optional[str] = None
    smtp_username: Optional[str] = Field(default=None, max_length=254)
    smtp_password: Optional[str] = Field(default=None, max_length=512)
    clear_smtp_password: Optional[bool] = None
    from_address: Optional[str] = Field(default=None, max_length=254)
    from_name: Optional[str] = Field(default=None, max_length=80)
    telegram_enabled: Optional[bool] = None
    telegram_bot_token: Optional[str] = Field(default=None, max_length=128)
    clear_telegram_bot_token: Optional[bool] = None
    admin_emails: Optional[str] = Field(default=None, max_length=6000)
    admin_telegram_chats: Optional[str] = Field(default=None, max_length=400)
    admin_events: Optional[dict[str, bool]] = None


class NotificationTest(BaseModel):
    channel: str = Field(max_length=16)
    recipient: str = Field(max_length=254)


class NotificationPreferencesUpdate(BaseModel):
    email_enabled: Optional[bool] = None
    telegram_enabled: Optional[bool] = None
    events: Optional[dict[str, bool]] = None


@router.get("/info")
def notifications_info(current_user: User = Depends(get_current_user)):
    """Whether to show the page at all; every account needs this."""
    return {"enabled": notifications.enabled()}


# ---------------------------------------------------------------------------
# Admin: channels, recipients, events, send log
# ---------------------------------------------------------------------------
@router.get("/settings")
def get_settings(current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    _require_enabled()
    return notifications.public_settings()


@router.put("/settings")
async def save_settings(payload: NotificationSettingsUpdate, request: Request, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    _require_enabled()
    body = payload.model_dump(exclude_none=True)
    try:
        # Saving a bot token asks Telegram who the bot is.
        result = await run_in_threadpool(notifications.save_settings, body)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    changed = sorted(key for key in body if key not in {"smtp_password", "telegram_bot_token"})
    log_action(db, current_user.id, "notifications_configure", "notifications", ",".join(changed), request=request)
    return result


@router.post("/test")
async def send_test(payload: NotificationTest, current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    _require_enabled()
    try:
        await run_in_threadpool(notifications.send_test, payload.channel, payload.recipient.strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - the admin needs the server's reason
        raise HTTPException(status_code=400, detail=f"Sending failed: {exc}") from exc
    return {"message": "Test notification sent"}


@router.get("/log")
def send_log(page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200), status: str = "",
             db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    ensure_role(current_user.role, Role.admin)
    query = db.query(NotificationMessage)
    if status in {"pending", "sending", "sent", "failed"}:
        query = query.filter(NotificationMessage.status == status)
    total = query.count()
    rows = query.order_by(NotificationMessage.id.desc()).offset((page - 1) * size).limit(size).all()
    return {"total": total, "page": page, "size": size, "items": [{
        "id": row.id, "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "event": row.event, "audience": row.audience, "channel": row.channel, "recipient": row.recipient,
        "subject": row.subject, "status": row.status, "attempts": row.attempts, "last_error": row.last_error,
        "sent_at": row.sent_at.isoformat() + "Z" if row.sent_at else None,
    } for row in rows]}


# ---------------------------------------------------------------------------
# Every account: its own channels and events
# ---------------------------------------------------------------------------
@router.get("/me")
def my_preferences(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_enabled()
    return notifications.preferences(db, current_user)


@router.put("/me")
def save_my_preferences(payload: NotificationPreferencesUpdate, db: Session = Depends(get_db),
                        current_user: User = Depends(get_current_user)):
    _require_enabled()
    return notifications.save_preferences(db, current_user, payload.model_dump(exclude_none=True))


@router.post("/me/telegram/link")
def start_telegram_link(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_enabled()
    try:
        return notifications.telegram_link_start(db, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/me/telegram/verify")
async def verify_telegram_link(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_enabled()
    try:
        linked = await run_in_threadpool(notifications.telegram_link_verify, db, current_user)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not linked:
        raise HTTPException(status_code=400, detail="The bot has not received your /start yet. Open the link, press Start, then check again.")
    return notifications.preferences(db, current_user)


@router.delete("/me/telegram")
def unlink_telegram(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_enabled()
    notifications.telegram_unlink(db, current_user)
    return notifications.preferences(db, current_user)


@router.post("/me/test")
async def send_my_test(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    _require_enabled()
    try:
        count = await run_in_threadpool(notifications.send_user_test, db, current_user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"message": "Test notification queued", "channels": count}
