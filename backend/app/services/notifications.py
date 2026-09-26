"""The Notifications addon: email (SMTP) and Telegram alerts.

Two audiences:

* **Admin** events describe the server -- a failed scheduled backup, a daemon
  that stopped, a disk filling up, a certificate that did not renew. They go to
  the admin recipients set on the addon (email addresses and Telegram chats,
  e.g. an ops group), and the admin chooses which of them are sent.
* **User** events describe one account -- a sign-in from a new address, a
  password or 2FA change, a certificate or malware finding on one of its
  websites, its storage filling up. They go to that user through the channels
  they turned on for themselves (their account email, their linked Telegram).

Every message is a row in ``notification_messages`` first and is sent from
there, right away in a background thread and again by ``tick()`` (run every
minute by the backup scheduler's timer) until it goes through or has failed
five times. The same rows are the send log the admin reads.

Nothing here may break the action it reports on: every entry point swallows
its own errors.
"""
from __future__ import annotations

import json
import logging
import re
import secrets as pysecrets
import shutil
import smtplib
import ssl as ssl_lib
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path
from typing import Iterable, Optional

from app.core.config import settings as app_settings
from app.core.database import SessionLocal
from app.core.secrets import decrypt, encrypt
from app.models.entities import NotificationMessage, NotificationPreference, User

log = logging.getLogger("opanel.notifications")

ADDON_ID = "notifications"
STATE_FILE = Path("/var/lib/opanel/notifications-state.json")
MAX_ATTEMPTS = 5
RETRY_MINUTES = (1, 5, 15, 60)
KEEP_DAYS = 30
TELEGRAM_API = "https://api.telegram.org"
LINK_CODE_MINUTES = 15

# ---------------------------------------------------------------------------
# The events. "admin" events are switched on and off by the admin for the
# admin recipients; "user" events by each user for themselves. An event with
# both goes to the admin recipients (a server-wide view) and to the user it is
# about.
# ---------------------------------------------------------------------------
EVENTS: dict[str, dict] = {
    "backup_failed": {"admin": True, "user": False},
    "malware_found": {"admin": True, "user": True},
    "ssl_expiring": {"admin": True, "user": True},
    "service_status": {"admin": True, "user": False},
    "disk_low": {"admin": True, "user": False},
    "storage_quota": {"admin": True, "user": True},
    "update_available": {"admin": True, "user": False},
    "update_result": {"admin": True, "user": False},
    "login_lockout": {"admin": True, "user": False},
    "da_import_done": {"admin": True, "user": False},
    "login_new_ip": {"admin": False, "user": True},
    "account_security": {"admin": False, "user": True},
    "backup_job": {"admin": False, "user": True},
    "account_status": {"admin": False, "user": True},
}
ADMIN_EVENTS = [key for key, value in EVENTS.items() if value["admin"]]
USER_EVENTS = [key for key, value in EVENTS.items() if value["user"]]
# Always delivered: a suspended account cannot sign in to read why.
UNMUTABLE_USER_EVENTS = {"account_status"}

DEFAULTS = {
    "language": "vi",
    "email_enabled": False,
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_security": "starttls",  # starttls | ssl | none
    "smtp_username": "",
    "smtp_password": "",  # encrypted
    "from_address": "",
    "from_name": "OPanel",
    "telegram_enabled": False,
    "telegram_bot_token": "",  # encrypted
    "telegram_bot_username": "",
    "admin_emails": "",
    "admin_telegram_chats": "",
    "admin_events": {key: True for key in ADMIN_EVENTS},
}
SECRET_KEYS = ("smtp_password", "telegram_bot_token")

EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
CHAT_ID_RE = re.compile(r"^-?\d{3,20}$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
PLACEHOLDER_DOMAINS = (".local", ".invalid", ".test", ".example", ".localhost", "@example.com", "@example.org")

_deliver_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
def enabled() -> bool:
    from app.services import addons

    return addons.is_enabled(ADDON_ID)


def _stored() -> dict:
    from app.services import addons

    stored = addons._entry(ADDON_ID).get("settings") or {}
    merged = json.loads(json.dumps(DEFAULTS))
    for key, value in stored.items():
        if key == "admin_events" and isinstance(value, dict):
            merged["admin_events"].update({k: bool(v) for k, v in value.items() if k in ADMIN_EVENTS})
        elif key in merged:
            merged[key] = value
    return merged


def config() -> dict:
    """The settings with secrets decrypted -- for sending, never for the API."""
    cfg = _stored()
    for key in SECRET_KEYS:
        try:
            cfg[key] = decrypt(cfg.get(key) or "")
        except RuntimeError:
            cfg[key] = ""
    return cfg


def public_settings() -> dict:
    cfg = _stored()
    out = {key: value for key, value in cfg.items() if key not in SECRET_KEYS}
    out["smtp_password_set"] = bool(cfg.get("smtp_password"))
    out["telegram_bot_token_set"] = bool(cfg.get("telegram_bot_token"))
    out["admin_event_keys"] = ADMIN_EVENTS
    out["email_ready"] = email_ready(cfg)
    out["telegram_ready"] = telegram_ready(cfg)
    return out


def email_ready(cfg: Optional[dict] = None) -> bool:
    cfg = cfg or _stored()
    return bool(cfg.get("email_enabled") and cfg.get("smtp_host") and cfg.get("from_address"))


def telegram_ready(cfg: Optional[dict] = None) -> bool:
    cfg = cfg or _stored()
    return bool(cfg.get("telegram_enabled") and cfg.get("telegram_bot_token"))


def _split(text: str) -> list[str]:
    return [item for item in re.split(r"[\s,;]+", text or "") if item]


def _clean_emails(text: str) -> str:
    items = _split(text)
    for item in items:
        if not EMAIL_RE.match(item) or len(item) > 254:
            raise ValueError(f"Not an email address: {item}")
    if len(items) > 20:
        raise ValueError("At most 20 admin email addresses")
    return ", ".join(items)


def _clean_chats(text: str) -> str:
    items = _split(text)
    for item in items:
        if not CHAT_ID_RE.match(item):
            raise ValueError(f"Not a Telegram chat ID: {item}")
    if len(items) > 10:
        raise ValueError("At most 10 admin Telegram chats")
    return ", ".join(items)


def save_settings(payload: dict) -> dict:
    """Validate and store the admin settings. A secret left blank keeps the
    stored one; send clear_smtp_password / clear_telegram_bot_token to drop it."""
    from app.services import addons

    current = _stored()
    new = dict(current)
    if "language" in payload:
        if payload["language"] not in {"vi", "en"}:
            raise ValueError("Language must be vi or en")
        new["language"] = payload["language"]
    for key in ("email_enabled", "telegram_enabled"):
        if key in payload:
            new[key] = bool(payload[key])
    if "smtp_host" in payload:
        host = str(payload["smtp_host"] or "").strip()
        if host and not HOST_RE.match(host):
            raise ValueError("SMTP host is not a host name")
        new["smtp_host"] = host
    if "smtp_port" in payload:
        try:
            port = int(payload["smtp_port"])
        except (TypeError, ValueError) as exc:
            raise ValueError("SMTP port must be a number") from exc
        if not 1 <= port <= 65535:
            raise ValueError("SMTP port must be between 1 and 65535")
        new["smtp_port"] = port
    if "smtp_security" in payload:
        if payload["smtp_security"] not in {"starttls", "ssl", "none"}:
            raise ValueError("SMTP security must be starttls, ssl or none")
        new["smtp_security"] = payload["smtp_security"]
    if "smtp_username" in payload:
        new["smtp_username"] = str(payload["smtp_username"] or "").strip()[:254]
    if "from_address" in payload:
        address = str(payload["from_address"] or "").strip()
        if address and not EMAIL_RE.match(address):
            raise ValueError("From address is not an email address")
        new["from_address"] = address
    if "from_name" in payload:
        new["from_name"] = re.sub(r"[\r\n<>\"]", "", str(payload["from_name"] or ""))[:80]
    if "admin_emails" in payload:
        new["admin_emails"] = _clean_emails(payload["admin_emails"])
    if "admin_telegram_chats" in payload:
        new["admin_telegram_chats"] = _clean_chats(payload["admin_telegram_chats"])
    if isinstance(payload.get("admin_events"), dict):
        new["admin_events"] = {key: bool(payload["admin_events"].get(key, current["admin_events"].get(key, True)))
                               for key in ADMIN_EVENTS}
    if payload.get("smtp_password"):
        new["smtp_password"] = encrypt(str(payload["smtp_password"]))
    elif payload.get("clear_smtp_password"):
        new["smtp_password"] = ""
    token_changed = False
    if payload.get("telegram_bot_token"):
        token = str(payload["telegram_bot_token"]).strip()
        if not re.fullmatch(r"\d{5,15}:[A-Za-z0-9_-]{20,64}", token):
            raise ValueError("That does not look like a Telegram bot token (123456:ABC...)")
        new["telegram_bot_username"] = telegram_get_me(token)
        new["telegram_bot_token"] = encrypt(token)
        token_changed = True
    elif payload.get("clear_telegram_bot_token"):
        new["telegram_bot_token"] = ""
        new["telegram_bot_username"] = ""
    if new["email_enabled"] and not (new["smtp_host"] and new["from_address"]):
        raise ValueError("Email needs an SMTP host and a From address")
    if new["telegram_enabled"] and not new["telegram_bot_token"]:
        raise ValueError("Telegram needs a bot token")
    addons._update_state(ADDON_ID, settings=new)
    if token_changed:
        # Chats linked to another bot cannot be reached by this one.
        _forget_user_chats()
    return public_settings()


def _forget_user_chats() -> None:
    db = SessionLocal()
    try:
        db.query(NotificationPreference).update({"telegram_chat_id": None}, synchronize_session=False)
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------
def _host() -> str:
    url = (app_settings.panel_url or "").strip()
    host = urllib.parse.urlsplit(url).hostname if url else ""
    if not host:
        import socket

        host = socket.gethostname()
    return host


def _when(lang: str, value: Optional[datetime] = None) -> str:
    value = value or datetime.now()
    return value.strftime("%d/%m/%Y %H:%M" if lang == "vi" else "%Y-%m-%d %H:%M")


def _lines(items: Iterable[str], limit: int = 10) -> str:
    items = list(items)
    shown = "\n".join(f"- {item}" for item in items[:limit])
    if len(items) > limit:
        shown += f"\n- ... (+{len(items) - limit})"
    return shown


def render(event: str, ctx: dict, lang: str = "vi") -> tuple[str, str]:
    """(subject, body) for one event. ctx carries the facts; missing keys read
    as empty rather than failing the message."""
    vi = lang == "vi"
    c = _Ctx(ctx)
    link = (app_settings.panel_url or "").rstrip("/")

    if event == "backup_failed":
        warning = c["status"] == "warning"
        subject = (("Backup theo lịch có cảnh báo" if warning else "Backup theo lịch bị lỗi") if vi
                   else ("Scheduled backup finished with warnings" if warning else "Scheduled backup failed"))
        body = (f"Lịch backup #{c['schedule_id']} ({c['schedule']}) chạy lúc {c['when']}.\n\n{c['detail']}" if vi
                else f"Backup schedule #{c['schedule_id']} ({c['schedule']}) ran at {c['when']}.\n\n{c['detail']}")
    elif event == "malware_found":
        if c.get("domain"):
            subject = (f"Phát hiện mã độc trên {c['domain']}" if vi else f"Malware found on {c['domain']}")
            body = ((f"Lượt quét lúc {c['when']} tìm thấy {c['count']} file nhiễm trên website {c['domain']}:\n"
                     f"{_lines(c.get('files') or [])}\n\nMở Quét mã độc trong panel để xem và cách ly.") if vi
                    else (f"The scan at {c['when']} found {c['count']} infected file(s) on {c['domain']}:\n"
                          f"{_lines(c.get('files') or [])}\n\nOpen Malware scanner in the panel to review and quarantine."))
        else:
            subject = (f"Phát hiện {c['count']} file nhiễm mã độc" if vi else f"{c['count']} infected file(s) found")
            body = ((f"Lượt quét {c['scope']} lúc {c['when']} tìm thấy {c['count']} file nhiễm:\n{_lines(c.get('files') or [])}")
                    if vi else
                    (f"The {c['scope']} scan at {c['when']} found {c['count']} infected file(s):\n{_lines(c.get('files') or [])}"))
    elif event == "ssl_expiring":
        if c.get("sites"):
            subject = (f"{len(c['sites'])} chứng chỉ SSL sắp hết hạn" if vi else f"{len(c['sites'])} SSL certificate(s) expiring")
            rows = [f"{s['domain']}: {s['days']} " + ("ngày" if vi else "day(s)") for s in c["sites"]]
            body = (("Các chứng chỉ sau chưa được gia hạn tự động (Let's Encrypt gia hạn khi còn 30 ngày):\n"
                     f"{_lines(rows, 30)}\n\nKiểm tra DNS và mục SSL của từng website.") if vi
                    else ("These certificates have not renewed (Let's Encrypt renews at 30 days left):\n"
                          f"{_lines(rows, 30)}\n\nCheck each website's DNS and its SSL page."))
        else:
            subject = (f"Chứng chỉ SSL của {c['domain']} sắp hết hạn" if vi else f"SSL certificate for {c['domain']} is expiring")
            body = ((f"Chứng chỉ SSL của {c['domain']} hết hạn sau {c['days']} ngày ({c['expires']}). "
                     "Khi hết hạn, trình duyệt sẽ chặn truy cập website.\n\n"
                     "Kiểm tra tên miền còn trỏ về máy chủ này, rồi cấp lại SSL trong panel.") if vi
                    else (f"The SSL certificate for {c['domain']} expires in {c['days']} day(s) ({c['expires']}). "
                          "Browsers will refuse the site once it does.\n\n"
                          "Check that the domain still points to this server, then reissue SSL in the panel."))
    elif event == "service_status":
        up = c["state"] == "up"
        subject = ((f"Dịch vụ {c['service']} đã chạy lại" if up else f"Dịch vụ {c['service']} đã ngừng") if vi
                   else (f"{c['service']} is running again" if up else f"{c['service']} has stopped"))
        body = ((f"{c['service']} hoạt động trở lại lúc {c['when']}." if up else
                 f"{c['service']} không chạy từ {c['when']} (kiểm tra 2 lần liên tiếp). "
                 f"Website có thể đang lỗi.\n\nXem: systemctl status {c['service']}") if vi
                else (f"{c['service']} was running again at {c['when']}." if up else
                      f"{c['service']} has not been running since {c['when']} (two checks in a row). "
                      f"Websites may be down.\n\nSee: systemctl status {c['service']}"))
    elif event == "disk_low":
        subject = (f"Ổ đĩa đã dùng {c['percent']}%" if vi else f"Disk {c['percent']}% full")
        body = ((f"Phân vùng / đã dùng {c['percent']}%, còn trống {c['free_gb']} GB. Khi đầy, MariaDB, backup "
                 "và upload sẽ lỗi.\n\nDọn backup cũ, log, hoặc nâng dung lượng.") if vi
                else (f"/ is {c['percent']}% full with {c['free_gb']} GB free. Once full, MariaDB, backups and "
                      "uploads fail.\n\nRemove old backups and logs, or add disk."))
    elif event == "storage_quota":
        full = c["percent"] >= 100 if isinstance(c.get("percent"), (int, float)) else False
        if c.get("username") and ctx.get("_admin_copy"):
            subject = (f"Tài khoản {c['username']} đã dùng {c['percent']}% dung lượng" if vi
                       else f"Account {c['username']} is at {c['percent']}% of its storage")
        else:
            subject = (("Tài khoản đã hết dung lượng" if full else f"Tài khoản đã dùng {c['percent']}% dung lượng") if vi
                       else ("Your account is out of storage" if full else f"Your account is at {c['percent']}% of its storage"))
        body = ((f"Tài khoản {c['username']} đang dùng {c['used']} trên {c['limit']} ({c['percent']}%)."
                 + (" Upload file, backup và cập nhật website sẽ bị từ chối." if full else "")) if vi
                else (f"Account {c['username']} uses {c['used']} of {c['limit']} ({c['percent']}%)."
                      + (" Uploads, backups and website updates will be refused." if full else "")))
    elif event == "update_available":
        subject = (f"Có bản OPanel {c['latest']}" if vi else f"OPanel {c['latest']} is available")
        body = (f"Máy chủ đang chạy {c['current']}. Bản {c['latest']} đã phát hành; cập nhật trong trang Cập nhật." if vi
                else f"This server runs {c['current']}. {c['latest']} is out; update from the Updates page.")
    elif event == "update_result":
        ok = c["status"] == "completed"
        subject = (("Cập nhật OPanel hoàn tất" if ok else "Cập nhật OPanel thất bại") if vi
                   else ("OPanel update completed" if ok else "OPanel update failed"))
        body = ((f"Phiên bản hiện tại: {c['version']}." if ok else f"Lần cập nhật lúc {c['when']} không hoàn tất.\n\n{c['message']}")
                if vi else
                (f"Now running {c['version']}." if ok else f"The update at {c['when']} did not finish.\n\n{c['message']}"))
    elif event == "login_lockout":
        subject = (f"Khóa đăng nhập từ {c['ip']}" if vi else f"Sign-in locked out for {c['ip']}")
        body = ((f"{c['ip']} đã đăng nhập sai liên tục (lần cuối với tài khoản {c['username']}) và bị khóa 15 phút.") if vi
                else (f"{c['ip']} failed to sign in repeatedly (last as {c['username']}) and is locked out for 15 minutes."))
    elif event == "da_import_done":
        ok = c["status"] == "done"
        subject = (("Nhập backup DirectAdmin hoàn tất" if ok else "Nhập backup DirectAdmin thất bại") if vi
                   else ("DirectAdmin import finished" if ok else "DirectAdmin import failed"))
        body = f"{c['file']}\n\n{c['message']}"
    elif event == "login_new_ip":
        subject = ("Đăng nhập từ địa chỉ mới" if vi else "Sign-in from a new address")
        body = ((f"Tài khoản {c['username']} vừa đăng nhập từ {c['ip']} lúc {c['when']}.\n\n"
                 "Nếu không phải bạn, hãy đổi mật khẩu và bật xác thực 2 bước ngay.") if vi
                else (f"Account {c['username']} signed in from {c['ip']} at {c['when']}.\n\n"
                      "If this was not you, change your password and turn on two-factor authentication now."))
    elif event == "account_security":
        change = _security_change(c["change"], c.get("detail", ""), vi)
        subject = (f"Bảo mật tài khoản: {change}" if vi else f"Account security: {change}")
        body = ((f"Tài khoản {c['username']}: {change} lúc {c['when']}" + (f" (từ {c['ip']})" if c.get("ip") else "") + ".\n\n"
                 "Nếu không phải bạn làm, hãy đổi mật khẩu và liên hệ quản trị viên.") if vi
                else (f"Account {c['username']}: {change} at {c['when']}" + (f" (from {c['ip']})" if c.get("ip") else "") + ".\n\n"
                      "If this was not you, change your password and contact your administrator."))
    elif event == "backup_job":
        ok = c["status"] == "done"
        subject = ((f"{c['title']} hoàn tất" if ok else f"{c['title']} thất bại") if vi
                   else (f"{c['title']} completed" if ok else f"{c['title']} failed"))
        body = "\n".join(part for part in (c.get("message"), c.get("error"), c.get("file")) if part)
    elif event == "account_status":
        suspended = c["state"] == "suspended"
        subject = (("Tài khoản đã bị tạm ngưng" if suspended else "Tài khoản đã được mở lại") if vi
                   else ("Your account has been suspended" if suspended else "Your account has been reactivated"))
        body = ((f"Tài khoản {c['username']} đã bị tạm ngưng. Website và đăng nhập tạm thời không hoạt động."
                 + (f"\nLý do: {c['reason']}" if c.get("reason") else "")) if suspended else
                f"Tài khoản {c['username']} đã hoạt động trở lại.") if vi else \
               ((f"Account {c['username']} has been suspended; its websites and sign-in are unavailable."
                 + (f"\nReason: {c['reason']}" if c.get("reason") else "")) if suspended else
                f"Account {c['username']} is active again.")
    elif event == "test":
        subject = ("Thông báo thử từ OPanel" if vi else "OPanel test notification")
        body = ("Kênh này đã được cấu hình đúng." if vi else "This channel is set up correctly.")
    else:
        subject, body = event, json.dumps(ctx, default=str)[:2000]

    host = _host()
    footer = f"\n\n-- \nOPanel · {host}" + (f"\n{link}" if link else "")
    return f"[OPanel {host}] {subject}"[:250], (body.strip() + footer)[:8000]


class _Ctx(dict):
    def __missing__(self, key):
        return ""


def _security_change(change: str, detail: str, vi: bool) -> str:
    names = {
        "password_changed": ("đã đổi mật khẩu", "password changed"),
        "password_reset_by_admin": ("quản trị viên đã đặt lại mật khẩu", "password reset by an administrator"),
        "2fa_enabled": ("đã bật xác thực 2 bước", "two-factor authentication turned on"),
        "2fa_disabled": ("đã tắt xác thực 2 bước", "two-factor authentication turned off"),
        "2fa_reset_by_admin": ("quản trị viên đã đặt lại xác thực 2 bước", "two-factor authentication reset by an administrator"),
        "passkey_added": ("đã thêm passkey", "passkey added"),
        "passkey_removed": ("đã xóa passkey", "passkey removed"),
        "api_token_created": ("đã tạo API token", "API token created"),
        "mcp_token_created": ("đã tạo MCP token", "MCP token created"),
    }
    text = names.get(change, (change, change))[0 if vi else 1]
    return f"{text} ({detail})" if detail else text


# ---------------------------------------------------------------------------
# Queueing
# ---------------------------------------------------------------------------
def _real_email(address: str) -> bool:
    address = (address or "").strip().lower()
    return bool(EMAIL_RE.match(address)) and not address.endswith(PLACEHOLDER_DOMAINS)


def admin_recipients(db, cfg: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if email_ready(cfg):
        emails = _split(cfg.get("admin_emails"))
        if not emails:
            emails = [u.email for u in db.query(User).filter(User.role.in_(["admin", "super_admin"]),
                                                             User.is_active == True).all()  # noqa: E712
                      if _real_email(u.email)]
        out += [("email", e) for e in dict.fromkeys(emails)]
    if telegram_ready(cfg):
        out += [("telegram", chat) for chat in _split(cfg.get("admin_telegram_chats"))]
    return out


def user_recipients(db, user: User, event: str, cfg: dict) -> list[tuple[str, str]]:
    pref = db.get(NotificationPreference, user.id)
    muted = set(_split(pref.muted_events)) if pref else set()
    if event in muted and event not in UNMUTABLE_USER_EVENTS:
        return []
    out: list[tuple[str, str]] = []
    if email_ready(cfg) and (pref is None or pref.email_enabled) and _real_email(user.email):
        out.append(("email", user.email.strip()))
    if telegram_ready(cfg) and pref and pref.telegram_enabled and pref.telegram_chat_id:
        out.append(("telegram", pref.telegram_chat_id))
    return out


def _enqueue(db, event: str, audience: str, recipients, subject: str, body: str, user_id=None) -> list[int]:
    ids = []
    for channel, recipient in recipients:
        row = NotificationMessage(event=event, audience=audience, user_id=user_id, channel=channel,
                                  recipient=recipient, subject=subject, body=body, status="pending",
                                  attempts=0, last_error="", created_at=datetime.utcnow())
        db.add(row)
        db.flush()
        ids.append(row.id)
    db.commit()
    return ids


def notify_admin(event: str, ctx: dict, *, deliver: bool = True) -> int:
    """Queue an admin event. Returns how many messages were queued."""
    try:
        if not enabled() or not EVENTS.get(event, {}).get("admin"):
            return 0
        cfg = config()
        if not cfg["admin_events"].get(event, True):
            return 0
        db = SessionLocal()
        try:
            recipients = admin_recipients(db, cfg)
            if not recipients:
                return 0
            subject, body = render(event, {**ctx, "_admin_copy": True}, cfg["language"])
            ids = _enqueue(db, event, "admin", recipients, subject, body)
        finally:
            db.close()
        if deliver:
            _kick()
        return len(ids)
    except Exception:  # noqa: BLE001 - a notification must never fail the action
        log.exception("notify_admin %s failed", event)
        return 0


def notify_user(event: str, user_id: Optional[int], ctx: dict, *, deliver: bool = True) -> int:
    """Queue a user event for one account. Returns how many messages were queued."""
    try:
        if not user_id or not enabled() or not EVENTS.get(event, {}).get("user"):
            return 0
        cfg = config()
        db = SessionLocal()
        try:
            user = db.get(User, int(user_id))
            if not user:
                return 0
            recipients = user_recipients(db, user, event, cfg)
            if not recipients:
                return 0
            subject, body = render(event, {"username": user.username, **ctx}, cfg["language"])
            ids = _enqueue(db, event, "user", recipients, subject, body, user_id=user.id)
        finally:
            db.close()
        if deliver:
            _kick()
        return len(ids)
    except Exception:  # noqa: BLE001
        log.exception("notify_user %s failed", event)
        return 0


def _kick() -> None:
    thread = threading.Thread(target=deliver_pending, kwargs={"limit": 20}, daemon=True,
                              name="opanel-notify")
    thread.start()


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------
def send_email(cfg: dict, to: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = formataddr((cfg.get("from_name") or "OPanel", cfg["from_address"]))
    message["To"] = to
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=(cfg["from_address"].split("@")[-1] or None))
    message.set_content(body)
    host, port, security = cfg["smtp_host"], int(cfg.get("smtp_port") or 587), cfg.get("smtp_security")
    context = ssl_lib.create_default_context()
    if security == "ssl":
        server = smtplib.SMTP_SSL(host, port, timeout=20, context=context)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
    try:
        server.ehlo()
        if security == "starttls":
            server.starttls(context=context)
            server.ehlo()
        if cfg.get("smtp_username"):
            server.login(cfg["smtp_username"], cfg.get("smtp_password") or "")
        server.send_message(message)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001
            pass


def _telegram(token: str, method: str, params: Optional[dict] = None, timeout: int = 15) -> dict:
    data = urllib.parse.urlencode(params or {}).encode("utf-8") if params else None
    request = urllib.request.Request(f"{TELEGRAM_API}/bot{token}/{method}", data=data)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https host
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            raise RuntimeError(f"Telegram HTTP {exc.code}") from exc
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram: {payload.get('description') or 'request failed'}")
    return payload


def telegram_get_me(token: str) -> str:
    return _telegram(token, "getMe")["result"].get("username") or ""


def send_telegram(cfg: dict, chat_id: str, subject: str, body: str) -> None:
    _telegram(cfg["telegram_bot_token"], "sendMessage",
              {"chat_id": chat_id, "text": f"{subject}\n\n{body}"[:4000], "disable_web_page_preview": "true"})


def _send(cfg: dict, row: NotificationMessage) -> None:
    if row.channel == "email":
        if not email_ready(cfg):
            raise RuntimeError("Email is not configured")
        send_email(cfg, row.recipient, row.subject, row.body)
    elif row.channel == "telegram":
        if not telegram_ready(cfg):
            raise RuntimeError("Telegram is not configured")
        send_telegram(cfg, row.recipient, row.subject, row.body)
    else:
        raise RuntimeError(f"Unknown channel {row.channel}")


def deliver_pending(limit: int = 30) -> int:
    """Send what is due. Each row is claimed with a conditional update, so the
    API's threads and the timer never send the same message twice."""
    if not _deliver_lock.acquire(blocking=False):
        return 0
    sent = 0
    try:
        cfg = config()
        db = SessionLocal()
        try:
            now = datetime.utcnow()
            due = (db.query(NotificationMessage.id)
                   .filter(NotificationMessage.status == "pending")
                   .filter((NotificationMessage.next_attempt_at == None) | (NotificationMessage.next_attempt_at <= now))  # noqa: E711
                   .order_by(NotificationMessage.id).limit(limit).all())
            for (message_id,) in due:
                claimed = (db.query(NotificationMessage)
                           .filter(NotificationMessage.id == message_id, NotificationMessage.status == "pending")
                           .update({"status": "sending"}, synchronize_session=False))
                db.commit()
                if not claimed:
                    continue
                row = db.get(NotificationMessage, message_id)
                row.attempts = (row.attempts or 0) + 1
                try:
                    _send(cfg, row)
                    row.status, row.sent_at, row.last_error = "sent", datetime.utcnow(), ""
                    sent += 1
                except Exception as exc:  # noqa: BLE001 - recorded on the row and retried
                    row.last_error = str(exc)[:1000]
                    if row.attempts >= MAX_ATTEMPTS:
                        row.status = "failed"
                    else:
                        row.status = "pending"
                        wait = RETRY_MINUTES[min(row.attempts - 1, len(RETRY_MINUTES) - 1)]
                        row.next_attempt_at = datetime.utcnow() + timedelta(minutes=wait)
                db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        log.exception("notification delivery failed")
    finally:
        _deliver_lock.release()
    return sent


def send_test(channel: str, recipient: str) -> None:
    """Send one message straight away and raise with the server's reason."""
    cfg = config()
    subject, body = render("test", {}, cfg["language"])
    if channel == "email":
        if not (cfg.get("smtp_host") and cfg.get("from_address")):
            raise ValueError("Set the SMTP host and From address first")
        if not EMAIL_RE.match(recipient or ""):
            raise ValueError("Enter an email address to send the test to")
        send_email(cfg, recipient, subject, body)
    elif channel == "telegram":
        if not cfg.get("telegram_bot_token"):
            raise ValueError("Set the bot token first")
        if not CHAT_ID_RE.match(recipient or ""):
            raise ValueError("Enter a Telegram chat ID to send the test to")
        send_telegram(cfg, recipient, subject, body)
    else:
        raise ValueError("Unknown channel")


# ---------------------------------------------------------------------------
# A user's own preferences and Telegram link
# ---------------------------------------------------------------------------
def preferences(db, user: User) -> dict:
    cfg = _stored()
    pref = db.get(NotificationPreference, user.id)
    muted = set(_split(pref.muted_events)) if pref else set()
    return {
        "enabled": enabled(),
        "email_available": email_ready(cfg),
        "telegram_available": telegram_ready(cfg),
        "telegram_bot_username": cfg.get("telegram_bot_username") or "",
        "email": user.email,
        "email_usable": _real_email(user.email),
        "email_enabled": pref.email_enabled if pref else True,
        "telegram_enabled": pref.telegram_enabled if pref else True,
        "telegram_linked": bool(pref and pref.telegram_chat_id),
        "events": {key: key not in muted for key in USER_EVENTS},
        "event_keys": USER_EVENTS,
        "unmutable": sorted(UNMUTABLE_USER_EVENTS),
    }


def _pref(db, user: User) -> NotificationPreference:
    pref = db.get(NotificationPreference, user.id)
    if pref is None:
        pref = NotificationPreference(user_id=user.id, email_enabled=True, telegram_enabled=True,
                                      muted_events="", known_ips="")
        db.add(pref)
    return pref


def save_preferences(db, user: User, payload: dict) -> dict:
    pref = _pref(db, user)
    if "email_enabled" in payload:
        pref.email_enabled = bool(payload["email_enabled"])
    if "telegram_enabled" in payload:
        pref.telegram_enabled = bool(payload["telegram_enabled"])
    if isinstance(payload.get("events"), dict):
        muted = [key for key in USER_EVENTS
                 if key not in UNMUTABLE_USER_EVENTS and payload["events"].get(key) is False]
        pref.muted_events = ",".join(muted)
    pref.updated_at = datetime.utcnow()
    db.commit()
    return preferences(db, user)


def telegram_link_start(db, user: User) -> dict:
    cfg = config()
    if not telegram_ready(cfg) or not cfg.get("telegram_bot_username"):
        raise ValueError("Telegram is not set up on this panel")
    pref = _pref(db, user)
    pref.telegram_link_code = pysecrets.token_hex(8)
    pref.telegram_link_expires = datetime.utcnow() + timedelta(minutes=LINK_CODE_MINUTES)
    db.commit()
    return {"code": pref.telegram_link_code,
            "url": f"https://t.me/{cfg['telegram_bot_username']}?start={pref.telegram_link_code}",
            "expires_minutes": LINK_CODE_MINUTES}


def telegram_link_verify(db, user: User) -> bool:
    """Look for "/start <code>" among the bot's updates. Every pending code
    found is linked at once, then the updates are confirmed, so one user
    checking cannot eat another's message."""
    cfg = config()
    if not telegram_ready(cfg):
        raise ValueError("Telegram is not set up on this panel")
    updates = _telegram(cfg["telegram_bot_token"], "getUpdates", {"timeout": "0", "allowed_updates": '["message"]'})
    results = updates.get("result") or []
    now = datetime.utcnow()
    pending = {p.telegram_link_code: p for p in db.query(NotificationPreference)
               .filter(NotificationPreference.telegram_link_code != None).all()  # noqa: E711
               if p.telegram_link_expires and p.telegram_link_expires > now}
    last_id = None
    for update in results:
        last_id = update.get("update_id", last_id)
        message = update.get("message") or {}
        text = (message.get("text") or "").strip()
        chat = message.get("chat") or {}
        if not text.startswith("/start ") or chat.get("type") != "private":
            continue
        pref = pending.get(text.split(" ", 1)[1].strip())
        if pref:
            pref.telegram_chat_id = str(chat.get("id"))
            pref.telegram_enabled = True
            pref.telegram_link_code = None
            pref.telegram_link_expires = None
    db.commit()
    if last_id is not None:
        try:
            _telegram(cfg["telegram_bot_token"], "getUpdates", {"offset": str(last_id + 1), "timeout": "0"})
        except RuntimeError:
            pass
    linked = db.get(NotificationPreference, user.id)
    return bool(linked and linked.telegram_chat_id)


def telegram_unlink(db, user: User) -> None:
    pref = _pref(db, user)
    pref.telegram_chat_id = None
    pref.telegram_link_code = None
    db.commit()


def send_user_test(db, user: User) -> int:
    cfg = config()
    subject, body = render("test", {}, cfg["language"])
    recipients = []
    pref = db.get(NotificationPreference, user.id)
    if email_ready(cfg) and (pref is None or pref.email_enabled) and _real_email(user.email):
        recipients.append(("email", user.email))
    if telegram_ready(cfg) and pref and pref.telegram_enabled and pref.telegram_chat_id:
        recipients.append(("telegram", pref.telegram_chat_id))
    if not recipients:
        raise ValueError("No channel is turned on for your account")
    _enqueue(db, "test", "user", recipients, subject, body, user_id=user.id)
    deliver_pending(limit=5)
    return len(recipients)


# ---------------------------------------------------------------------------
# Hooks called from the rest of the panel
# ---------------------------------------------------------------------------
def record_login(user: User, ip: str) -> None:
    """After a successful sign-in: tell the user when it came from an address
    this account has not used recently. The first sign-in only records."""
    try:
        if not enabled() or not ip:
            return
        db = SessionLocal()
        try:
            pref = _pref(db, db.get(User, user.id) or user)
            known = _split(pref.known_ips)
            new_address = ip not in known
            pref.known_ips = ",".join([ip] + [k for k in known if k != ip][:19])
            db.commit()
        finally:
            db.close()
        if new_address and known:
            notify_user("login_new_ip", user.id, {"ip": ip, "when": _when(_stored()["language"])})
    except Exception:  # noqa: BLE001
        log.exception("record_login failed")


def client_ip(request) -> str:
    client = getattr(request, "client", None)
    return getattr(client, "host", "") or ""


def security_change(user_id: int, change: str, ip: str = "", detail: str = "") -> None:
    notify_user("account_security", user_id,
                {"change": change, "ip": ip, "detail": detail, "when": _when(_stored()["language"])})


def backup_schedule_failed(schedule, errors: list, warnings: list, now: datetime) -> None:
    try:
        notify_admin("backup_failed", {
            "schedule_id": getattr(schedule, "id", ""), "schedule": getattr(schedule, "schedule", ""),
            "status": "error" if errors else "warning", "detail": "\n".join(errors + warnings)[:3000],
            "when": _when(_stored()["language"], now),
        })
    except Exception:  # noqa: BLE001
        log.exception("backup notification failed")


def backup_job_finished(job: dict) -> None:
    kind = job.get("kind") or ""
    vi = _stored()["language"] == "vi"
    titles = {
        "site_backup": ("Backup website", "Website backup"),
        "user_backup": ("Backup tài khoản", "Account backup"),
        "sftp_backup": ("Backup lên SFTP", "SFTP backup"),
        "user_restore_batch": ("Khôi phục backup", "Restore"),
        "schedule_run": ("Chạy lịch backup", "Backup schedule run"),
    }
    title = titles.get(kind, ("Tác vụ backup", "Backup task"))[0 if vi else 1]
    notify_user("backup_job", job.get("request_user_id"), {
        "status": job.get("status"), "title": title, "message": job.get("message") or "",
        "error": job.get("error") or "", "file": job.get("remote_file") or job.get("backup_file") or "",
    })


def malware_scan_finished(job: dict) -> None:
    """A scan found threats: each affected website's owner hears about their
    own files, the admin recipients get the whole list."""
    try:
        threats = job.get("threats") or []
        if not threats:
            return
        lang = _stored()["language"]
        when = _when(lang)
        files = [f"{t.get('path', '')} ({t.get('signature', '')})" for t in threats]
        website_scan = job.get("scope") in {"all", "website"}
        scope = ("website" if website_scan else ("toàn máy chủ" if lang == "vi" else "server-wide"))
        notify_admin("malware_found", {"count": len(threats), "files": files, "scope": scope, "when": when})
        by_domain: dict[str, list[str]] = {}
        for threat in threats:
            domain = threat.get("domain") or ""
            if domain:
                by_domain.setdefault(domain, []).append(f"{threat.get('path', '')} ({threat.get('signature', '')})")
        if not by_domain:
            return
        from app.models.entities import Website

        db = SessionLocal()
        try:
            owners = {w.domain: w.owner_id for w in db.query(Website).filter(Website.domain.in_(list(by_domain))).all()}
        finally:
            db.close()
        for domain, domain_files in by_domain.items():
            if owners.get(domain):
                notify_user("malware_found", owners[domain],
                            {"domain": domain, "count": len(domain_files), "files": domain_files, "when": when})
    except Exception:  # noqa: BLE001
        log.exception("malware notification failed")


# ---------------------------------------------------------------------------
# The minute tick: retries, and the checks nothing else triggers
# ---------------------------------------------------------------------------
def _read_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _write_state(state: dict) -> None:
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError:
        pass


def _due(state: dict, key: str, minutes: int, now: datetime) -> bool:
    last = state.get(f"last_{key}")
    try:
        if last and now - datetime.fromisoformat(last) < timedelta(minutes=minutes):
            return False
    except ValueError:
        pass
    state[f"last_{key}"] = now.isoformat(timespec="seconds")
    return True


def check_services(state: dict) -> None:
    from app.services.shell import shell
    from app.services.system import BASE_SERVICES

    services = state.setdefault("services", {})
    lang = _stored()["language"]
    for name in BASE_SERVICES:
        result = shell.run(["systemctl", "is-active", name], check=False)
        active = (result.stdout or "").strip() == "active"
        entry = services.setdefault(name, {"fails": 0, "down": False})
        if active:
            if entry.get("down"):
                notify_admin("service_status", {"service": name, "state": "up", "when": _when(lang)}, deliver=False)
            entry.update(fails=0, down=False)
        else:
            entry["fails"] = int(entry.get("fails", 0)) + 1
            if entry["fails"] >= 2 and not entry.get("down"):
                entry["down"] = True
                notify_admin("service_status", {"service": name, "state": "down", "when": _when(lang)}, deliver=False)


def check_disk(state: dict) -> None:
    usage = shutil.disk_usage("/")
    percent = round(usage.used * 100 / usage.total) if usage.total else 0
    level = 95 if percent >= 95 else 90 if percent >= 90 else 0
    previous = int(state.get("disk_level", 0))
    if level > previous:
        notify_admin("disk_low", {"percent": percent, "free_gb": round(usage.free / 1024 ** 3, 1)}, deliver=False)
    # Re-arm only once it has clearly dropped back, so 89/90/89 does not chatter.
    if level > previous or percent < 85:
        state["disk_level"] = level


def check_updates(state: dict, daily: bool) -> None:
    from app.services import updates

    status_file = _read_json(updates.UPDATE_STATE_FILE)
    finished = status_file.get("last_update_finished_at") or ""
    result = status_file.get("last_update_status") or ""
    if finished and finished != state.get("update_finished_at"):
        if state.get("update_finished_at") is not None and result in {"completed", "failed"}:
            lang = _stored()["language"]
            notify_admin("update_result", {"status": result, "version": updates.APP_VERSION, "when": _when(lang),
                                           "message": status_file.get("last_update_message") or ""}, deliver=False)
        state["update_finished_at"] = finished
    state.setdefault("update_finished_at", finished)
    if daily:
        release = updates.panel_release_status()
        latest = release.get("latest_version") or ""
        if release.get("update_available") and latest and latest != state.get("update_notified_version"):
            notify_admin("update_available", {"current": updates.APP_VERSION, "latest": latest}, deliver=False)
            state["update_notified_version"] = latest


def _read_json(path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def check_ssl_expiry(state: dict) -> None:
    """Warn at 14, 7, 3 and 1 day(s) left. Let's Encrypt renews at 30, so a
    certificate this close has a renewal that keeps failing."""
    from app.models.entities import Website
    from app.services import ssl as ssl_service

    thresholds = (1, 3, 7, 14)
    notified = state.setdefault("ssl", {})
    lang = _stored()["language"]
    expiring = []
    db = SessionLocal()
    try:
        sites = db.query(Website).filter(Website.ssl_enabled == True).all()  # noqa: E712
        for site in sites:
            try:
                material = ssl_service.read_site_certificate(
                    site.domain, site.ssl_mode, site.ssl_cert_path, site.ssl_key_path,
                    site.ssl_ca_path, getattr(site, "ssl_reuse_name", None))
                expires = ssl_service.certificate_expiry(material["certificate"]) if material else None
            except Exception:  # noqa: BLE001 - an unreadable certificate is not an expiring one
                continue
            if not expires:
                continue
            days = (expires.replace(tzinfo=None) - datetime.utcnow()).days
            if days > 14:
                notified.pop(site.domain, None)
                continue
            level = min(t for t in thresholds if days <= t) if days <= 14 else None
            if level is None or notified.get(site.domain) == level:
                continue
            notified[site.domain] = level
            expiring.append({"domain": site.domain, "days": max(days, 0)})
            notify_user("ssl_expiring", site.owner_id, {"domain": site.domain, "days": max(days, 0),
                                                        "expires": _when(lang, expires.replace(tzinfo=None))}, deliver=False)
    finally:
        db.close()
    if expiring:
        notify_admin("ssl_expiring", {"sites": expiring}, deliver=False)


def check_storage(state: dict) -> None:
    from app.services import storage_quota

    levels = state.setdefault("storage", {})
    db = SessionLocal()
    try:
        users = db.query(User).filter(User.is_active == True).all()  # noqa: E712
        for user in users:
            if not user.storage_limit_mb:
                continue
            try:
                summary = storage_quota.storage_usage_summary(db, user)
            except Exception:  # noqa: BLE001
                continue
            percent = summary.get("storage_percent")
            if not isinstance(percent, (int, float)) or not summary.get("storage_limit_bytes"):
                continue
            level = 100 if percent >= 100 else 90 if percent >= 90 else 0
            previous = int(levels.get(str(user.id), 0))
            if level > previous:
                ctx = {"percent": round(percent), "used": _size(summary.get("storage_used_bytes")),
                       "limit": _size(summary.get("storage_limit_bytes"))}
                notify_user("storage_quota", user.id, ctx, deliver=False)
                notify_admin("storage_quota", {**ctx, "username": user.username}, deliver=False)
            if level > previous or percent < 85:
                levels[str(user.id)] = level
    finally:
        db.close()


def _size(value) -> str:
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "?"
    return f"{size / 1024 ** 3:.1f} GB" if size >= 1024 ** 3 else f"{size / 1024 ** 2:.0f} MB"


def cleanup(now: datetime) -> None:
    db = SessionLocal()
    try:
        db.query(NotificationMessage).filter(NotificationMessage.created_at < now - timedelta(days=KEEP_DAYS)) \
            .delete(synchronize_session=False)
        # A sender that died mid-send leaves its row claimed; put it back.
        db.query(NotificationMessage).filter(NotificationMessage.status == "sending",
                                             NotificationMessage.created_at < now - timedelta(minutes=10)) \
            .update({"status": "pending"}, synchronize_session=False)
        db.commit()
    finally:
        db.close()


def tick(now: Optional[datetime] = None) -> None:
    """Called every minute by the backup scheduler's timer."""
    if not enabled():
        return
    now = now or datetime.utcnow()
    state = _read_state()
    checks = (
        ("services", 5, check_services),
        ("disk", 10, check_disk),
        ("ssl", 24 * 60, check_ssl_expiry),
        ("storage", 6 * 60, check_storage),
    )
    for key, minutes, check in checks:
        if _due(state, key, minutes, now):
            try:
                check(state)
            except Exception:  # noqa: BLE001
                log.exception("notification check %s failed", key)
    try:
        check_updates(state, daily=_due(state, "update_check", 24 * 60, now))
    except Exception:  # noqa: BLE001
        log.exception("notification check updates failed")
    if _due(state, "cleanup", 24 * 60, now):
        try:
            cleanup(now)
        except Exception:  # noqa: BLE001
            log.exception("notification cleanup failed")
    _write_state(state)
    deliver_pending(limit=30)
