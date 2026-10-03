"""The Notifications addon: settings, rendering, recipients, delivery, checks."""
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.security import hash_password
from app.models.entities import NotificationMessage, NotificationPreference, User
from app.services import addons, notifications


@pytest.fixture
def env(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(notifications, "SessionLocal", Session)
    state = {"installed": True, "running": True, "settings": {}}
    monkeypatch.setattr(addons, "_entry", lambda addon_id: dict(state) if addon_id == "notifications" else {})
    monkeypatch.setattr(addons, "is_enabled", lambda addon_id: addon_id == "notifications" and state["running"])

    def update_state(addon_id, **fields):
        state.update(fields)
        return dict(state)

    monkeypatch.setattr(addons, "_update_state", update_state)
    monkeypatch.setattr(notifications, "STATE_FILE", tmp_path / "notify-state.json")
    monkeypatch.setattr(notifications, "_kick", lambda: None)
    monkeypatch.setattr(notifications.app_settings, "panel_url", "https://panel.example.net:2222")
    sent = []
    monkeypatch.setattr(notifications, "send_email", lambda cfg, to, subject, body: sent.append(("email", to, subject, body)))
    monkeypatch.setattr(notifications, "send_telegram", lambda cfg, chat, subject, body: sent.append(("telegram", chat, subject, body)))
    db = Session()
    users = {}
    for name, role, email in (("root", "admin", "ops@company.vn"), ("alice", "end_user", "alice@shop.vn"),
                              ("bob", "end_user", "bob@import.local")):
        users[name] = User(username=name, email=email, role=role, is_active=True,
                           hashed_password=hash_password("PasswordLongEnough1"))
        db.add(users[name])
    db.commit()
    yield SimpleNamespace(db=db, Session=Session, state=state, users=users, sent=sent)
    db.close()


def _configure(env, **extra):
    env.state["settings"] = {
        "email_enabled": True, "smtp_host": "smtp.company.vn", "from_address": "panel@company.vn",
        "telegram_enabled": True, "telegram_bot_token": notifications.encrypt("123456:" + "A" * 35),
        "telegram_bot_username": "opanel_bot", "admin_telegram_chats": "-1001234567", **extra,
    }


def _rows(env):
    env.db.expire_all()
    return env.db.query(NotificationMessage).order_by(NotificationMessage.id).all()


# ---------------------------------------------------------------------------
# settings
# ---------------------------------------------------------------------------
def test_secrets_are_encrypted_and_never_returned(env, monkeypatch):
    monkeypatch.setattr(notifications, "telegram_get_me", lambda token: "opanel_bot")
    out = notifications.save_settings({
        "email_enabled": True, "smtp_host": "smtp.company.vn", "smtp_port": 587, "from_address": "panel@company.vn",
        "smtp_password": "hunter2-long", "telegram_enabled": True, "telegram_bot_token": "123456:" + "B" * 35,
    })
    stored = env.state["settings"]
    assert stored["smtp_password"].startswith("fernet:") and stored["telegram_bot_token"].startswith("fernet:")
    assert "smtp_password" not in out and "telegram_bot_token" not in out
    assert out["smtp_password_set"] and out["telegram_bot_token_set"] and out["telegram_bot_username"] == "opanel_bot"
    assert notifications.config()["smtp_password"] == "hunter2-long"
    # Leaving a secret blank keeps it.
    notifications.save_settings({"smtp_password": "", "from_name": "Ops"})
    assert notifications.config()["smtp_password"] == "hunter2-long"


@pytest.mark.parametrize("payload", [
    {"smtp_host": "smtp.example.com; rm -rf /"}, {"smtp_port": 70000}, {"smtp_security": "tls1"},
    {"from_address": "not-an-address"}, {"admin_emails": "ok@x.vn, nope"}, {"admin_telegram_chats": "@group"},
    {"language": "fr"}, {"email_enabled": True}, {"telegram_enabled": True}, {"telegram_bot_token": "abc"},
])
def test_bad_settings_are_refused(env, payload):
    with pytest.raises(ValueError):
        notifications.save_settings(payload)


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------
CONTEXTS = {
    "backup_failed": {"schedule_id": 3, "schedule": "0 2 * * *", "status": "error", "detail": "alice: S3 unreachable", "when": "x"},
    "malware_found": {"domain": "shop.vn", "count": 2, "files": ["/home/a/x.php (Php.Webshell)"], "when": "x"},
    "ssl_expiring": {"domain": "shop.vn", "days": 7, "expires": "04/10/2026"},
    "service_status": {"service": "mariadb", "state": "down", "when": "x"},
    "disk_low": {"percent": 92, "free_gb": 8.1},
    "storage_quota": {"percent": 100, "used": "10.0 GB", "limit": "10.0 GB", "username": "alice"},
    "resource_limit": {"username": "alice", "count": 3, "memory": 512},
    "update_available": {"current": "1.20.0", "latest": "1.21.0"},
    "update_result": {"status": "failed", "version": "1.20.0", "when": "x", "message": "npm ci failed"},
    "login_lockout": {"ip": "203.0.113.9", "username": "root"},
    "da_import_done": {"status": "done", "file": "user.admin.tar.zst", "message": "30 accounts"},
    "login_new_ip": {"username": "alice", "ip": "198.51.100.4", "when": "x"},
    "account_security": {"username": "alice", "change": "2fa_disabled", "ip": "198.51.100.4", "when": "x"},
    "backup_job": {"status": "error", "title": "Website backup", "message": "failed", "error": "disk full"},
    "account_status": {"username": "alice", "state": "suspended", "reason": "Overdue invoice"},
}


@pytest.mark.parametrize("lang", ["vi", "en"])
@pytest.mark.parametrize("event", list(notifications.EVENTS))
def test_every_event_renders_in_both_languages(env, event, lang):
    subject, body = notifications.render(event, CONTEXTS[event], lang)
    assert subject.startswith("[OPanel panel.example.net] ")
    assert body and "panel.example.net" in body and len(subject) <= 250
    fact = {"backup_failed": "S3 unreachable", "malware_found": "shop.vn", "ssl_expiring": "shop.vn",
            "service_status": "mariadb", "disk_low": "92", "storage_quota": "10.0 GB", "resource_limit": "512 MB", "update_available": "1.21.0",
            "update_result": "npm ci failed", "login_lockout": "203.0.113.9", "da_import_done": "30 accounts",
            "login_new_ip": "198.51.100.4", "account_security": "198.51.100.4", "backup_job": "disk full",
            "account_status": "Overdue invoice"}[event]
    assert fact in subject + body


def test_the_admin_ssl_digest_lists_every_site(env):
    subject, body = notifications.render("ssl_expiring", {"sites": [{"domain": "a.vn", "days": 3}, {"domain": "b.vn", "days": 1}]}, "vi")
    assert "2 chứng chỉ SSL" in subject and "a.vn: 3 ngày" in body and "b.vn: 1 ngày" in body


# ---------------------------------------------------------------------------
# recipients
# ---------------------------------------------------------------------------
def test_nothing_is_queued_while_the_addon_is_off(env):
    _configure(env)
    env.state["running"] = False
    assert notifications.notify_admin("disk_low", {"percent": 91}) == 0
    assert notifications.notify_user("login_new_ip", env.users["alice"].id, {"ip": "1.2.3.4"}) == 0
    assert _rows(env) == []


def test_admin_events_go_to_the_admin_recipients_and_can_be_turned_off(env):
    _configure(env)
    assert notifications.notify_admin("disk_low", {"percent": 91}) == 2
    assert {(r.channel, r.recipient) for r in _rows(env)} == {("email", "ops@company.vn"), ("telegram", "-1001234567")}
    env.state["settings"]["admin_events"] = {"disk_low": False}
    assert notifications.notify_admin("disk_low", {"percent": 96}) == 0


def test_explicit_admin_emails_replace_the_admin_accounts(env):
    _configure(env, admin_emails="noc@company.vn, boss@company.vn", admin_telegram_chats="")
    notifications.notify_admin("login_lockout", {"ip": "203.0.113.9", "username": "root"})
    assert sorted(r.recipient for r in _rows(env)) == ["boss@company.vn", "noc@company.vn"]


def test_own_account_events_follow_the_admins_choices_and_skip_customers(env):
    _configure(env)
    root, alice, bob = env.users["root"], env.users["alice"], env.users["bob"]
    # Hosting customers are never notified, whatever their address.
    assert notifications.notify_user("login_new_ip", alice.id, {"ip": "1.2.3.4"}) == 0
    assert notifications.notify_user("account_security", bob.id, {"change": "password_changed"}) == 0
    assert notifications.notify_user("login_new_ip", root.id, {"ip": "1.2.3.4"}) == 1
    env.db.add(NotificationPreference(user_id=root.id, email_enabled=True, telegram_enabled=True,
                                      telegram_chat_id="555123", muted_events="login_new_ip", known_ips=""))
    env.db.commit()
    assert notifications.notify_user("login_new_ip", root.id, {"ip": "1.2.3.5"}) == 0
    assert notifications.notify_user("account_security", root.id, {"change": "password_changed"}) == 2
    # Server events are never sent as a personal copy.
    assert notifications.notify_user("disk_low", root.id, {"percent": 99}) == 0
    assert notifications.notify_user("malware_found", root.id, {"domain": "a.vn"}) == 0


def test_saving_preferences_mutes_only_own_account_events(env):
    _configure(env)
    out = notifications.save_preferences(env.db, env.users["root"], {"email_enabled": False,
                                                                     "events": {"login_new_ip": False, "disk_low": False}})
    assert out["email_enabled"] is False and out["events"] == {"login_new_ip": False, "account_security": True, "backup_job": True}
    assert out["unmutable"] == []


# ---------------------------------------------------------------------------
# delivery
# ---------------------------------------------------------------------------
def test_delivery_sends_retries_and_gives_up(env, monkeypatch):
    _configure(env)
    notifications.notify_admin("disk_low", {"percent": 91})
    assert notifications.deliver_pending() == 2
    assert [r.status for r in _rows(env)] == ["sent", "sent"] and len(env.sent) == 2

    def refuse(cfg, to, subject, body):
        raise OSError("Connection refused")

    monkeypatch.setattr(notifications, "send_email", refuse)
    env.state["settings"]["admin_telegram_chats"] = ""
    notifications.notify_admin("disk_low", {"percent": 96})
    for attempt in range(1, notifications.MAX_ATTEMPTS + 1):
        row = _rows(env)[-1]
        row.next_attempt_at = None
        env.db.commit()
        notifications.deliver_pending()
        row = _rows(env)[-1]
        assert row.attempts == attempt and "Connection refused" in row.last_error
    assert row.status == "failed"


def test_a_claimed_message_is_not_sent_twice(env):
    _configure(env, admin_telegram_chats="")
    notifications.notify_admin("disk_low", {"percent": 91})
    row = _rows(env)[0]
    row.status = "sending"
    env.db.commit()
    assert notifications.deliver_pending() == 0 and env.sent == []


# ---------------------------------------------------------------------------
# hooks
# ---------------------------------------------------------------------------
def test_a_new_sign_in_address_is_reported_but_the_first_only_recorded(env):
    _configure(env)
    root, alice = env.users["root"], env.users["alice"]
    notifications.record_login(root, "198.51.100.4")
    assert _rows(env) == []
    notifications.record_login(root, "198.51.100.4")
    assert _rows(env) == []
    notifications.record_login(root, "203.0.113.50")
    rows = _rows(env)
    assert rows and rows[0].event == "login_new_ip" and "203.0.113.50" in rows[0].body
    # A customer's sign-ins are not even recorded.
    notifications.record_login(alice, "198.51.100.9")
    notifications.record_login(alice, "203.0.113.99")
    env.db.expire_all()
    assert env.db.get(NotificationPreference, alice.id) is None


def test_malware_goes_to_the_admins_not_to_the_site_owner(env):
    from app.models.entities import Website

    _configure(env)
    alice = env.users["alice"]
    env.db.add(Website(domain="shop.vn", owner_id=alice.id, root_path="/home/alice/shop.vn", document_root="public_html",
                       linux_user="alice", php_version="8.3", app_type="wordpress", status="active"))
    env.db.commit()
    notifications.malware_scan_finished({"scope": "all", "threats": [
        {"path": "/home/alice/shop.vn/public_html/x.php", "signature": "Php.Webshell", "domain": "shop.vn"},
        {"path": "/home/other/y.php", "signature": "Js.Miner", "domain": "gone.vn"},
    ]})
    rows = _rows(env)
    assert rows and {r.audience for r in rows} == {"admin"}
    assert all("x.php" in r.body and "y.php" in r.body for r in rows)
    assert "alice@shop.vn" not in {r.recipient for r in rows}


def test_the_login_lockout_trips_once(monkeypatch):
    from app.api import auth

    monkeypatch.setattr(auth, "_rate_limit_backend", lambda: "memory")
    auth._login_failures.clear()
    auth._login_lockouts.clear()
    tripped = [auth._record_failure("203.0.113.77", apply_lockout=True) for _ in range(auth._LOGIN_LOCKOUT_THRESHOLD)]
    assert tripped[-1] is True and not any(tripped[:-1])
    assert auth._record_failure("user:root", apply_lockout=False) is False


# ---------------------------------------------------------------------------
# periodic checks
# ---------------------------------------------------------------------------
def test_a_service_is_reported_after_two_failed_checks_and_again_when_back(env, monkeypatch):
    _configure(env, admin_telegram_chats="")
    status = {"mariadb": "failed"}
    monkeypatch.setattr("app.services.shell.shell.run",
                        lambda args, check=False: SimpleNamespace(stdout=status.get(args[-1], "active")))
    state = {}
    notifications.check_services(state)
    assert _rows(env) == []
    notifications.check_services(state)
    notifications.check_services(state)
    rows = _rows(env)
    assert len(rows) == 1 and "mariadb" in rows[0].subject
    status["mariadb"] = "active"
    notifications.check_services(state)
    assert len(_rows(env)) == 2


def test_disk_warnings_step_up_and_rearm_below_85(env, monkeypatch):
    _configure(env, admin_telegram_chats="")
    usage = {"used": 91}
    monkeypatch.setattr(notifications.shutil, "disk_usage",
                        lambda path: SimpleNamespace(total=100, used=usage["used"], free=100 - usage["used"]))
    state = {}
    for used, expected in ((91, 1), (92, 1), (96, 2), (90, 2), (80, 2), (91, 3)):
        usage["used"] = used
        notifications.check_disk(state)
        assert len(_rows(env)) == expected, used


def test_ssl_warnings_fire_once_per_threshold(env, monkeypatch):
    from app.models.entities import Website
    from app.services import ssl as ssl_service

    _configure(env, admin_telegram_chats="")
    alice = env.users["alice"]
    env.db.add(Website(domain="shop.vn", owner_id=alice.id, root_path="/home/alice/shop.vn", document_root="public_html",
                       linux_user="alice", php_version="8.3", app_type="wordpress", status="active", ssl_enabled=True))
    env.db.commit()
    expiry = {"at": datetime.utcnow() + timedelta(days=13, hours=12)}
    monkeypatch.setattr(ssl_service, "read_site_certificate", lambda *a, **k: {"certificate": b"x"})
    monkeypatch.setattr(ssl_service, "certificate_expiry", lambda cert: expiry["at"])
    state = {}
    notifications.check_ssl_expiry(state)
    assert [r.audience for r in _rows(env)] == ["admin"]
    notifications.check_ssl_expiry(state)
    assert len(_rows(env)) == 1
    expiry["at"] = datetime.utcnow() + timedelta(days=6, hours=12)
    notifications.check_ssl_expiry(state)
    assert len(_rows(env)) == 2
    # Renewed: forget it, so the next run-up warns again.
    expiry["at"] = datetime.utcnow() + timedelta(days=89)
    notifications.check_ssl_expiry(state)
    assert "shop.vn" not in state["ssl"]


def test_an_update_result_is_reported_once_and_not_on_first_sight(env, monkeypatch, tmp_path):
    from app.services import updates

    _configure(env, admin_telegram_chats="")
    status_file = tmp_path / "update-status.json"
    monkeypatch.setattr(updates, "UPDATE_STATE_FILE", status_file)
    status_file.write_text('{"last_update_status": "completed", "last_update_finished_at": "2026-09-26T10:00:00Z"}')
    state = {}
    notifications.check_updates(state, daily=False)
    assert _rows(env) == []
    status_file.write_text('{"last_update_status": "failed", "last_update_finished_at": "2026-09-27T10:00:00Z", "last_update_message": "npm ci failed"}')
    notifications.check_updates(state, daily=False)
    notifications.check_updates(state, daily=False)
    rows = _rows(env)
    assert len(rows) == 1 and "npm ci failed" in rows[0].body


# ---------------------------------------------------------------------------
# Telegram link
# ---------------------------------------------------------------------------
def test_telegram_link_codes_are_matched_for_everyone_waiting(env, monkeypatch):
    _configure(env)
    alice, root = env.users["alice"], env.users["root"]
    link = notifications.telegram_link_start(env.db, alice)
    assert link["url"] == f"https://t.me/opanel_bot?start={link['code']}"
    other = notifications.telegram_link_start(env.db, root)
    calls = []

    def fake(token, method, params=None, timeout=15):
        calls.append((method, params))
        if method == "getUpdates" and "offset" not in (params or {}):
            return {"ok": True, "result": [
                {"update_id": 10, "message": {"text": f"/start {link['code']}", "chat": {"id": 111, "type": "private"}}},
                {"update_id": 11, "message": {"text": f"/start {other['code']}", "chat": {"id": 222, "type": "private"}}},
                {"update_id": 12, "message": {"text": "/start nope", "chat": {"id": 333, "type": "group"}}},
            ]}
        return {"ok": True, "result": []}

    monkeypatch.setattr(notifications, "_telegram", fake)
    assert notifications.telegram_link_verify(env.db, alice) is True
    env.db.expire_all()
    assert env.db.get(NotificationPreference, alice.id).telegram_chat_id == "111"
    assert env.db.get(NotificationPreference, root.id).telegram_chat_id == "222"
    assert calls[-1] == ("getUpdates", {"offset": "13", "timeout": "0"})


def test_a_new_bot_forgets_chats_linked_to_the_old_one(env, monkeypatch):
    _configure(env)
    env.db.add(NotificationPreference(user_id=env.users["alice"].id, telegram_chat_id="111", email_enabled=True,
                                      telegram_enabled=True, muted_events="", known_ips=""))
    env.db.commit()
    monkeypatch.setattr(notifications, "telegram_get_me", lambda token: "new_bot")
    notifications.save_settings({"telegram_bot_token": "654321:" + "C" * 35})
    env.db.expire_all()
    assert env.db.get(NotificationPreference, env.users["alice"].id).telegram_chat_id is None


# ---------------------------------------------------------------------------
# wiring
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[3]


def test_the_minute_timer_drives_the_tick():
    source = (ROOT / "backend" / "app" / "services" / "backup_scheduler.py").read_text(encoding="utf-8")
    main = source[source.index('if __name__ == "__main__":'):]
    assert "notifications.tick()" in main


def test_the_addon_is_a_panel_addon_the_helper_never_sees():
    assert addons.ADDONS["notifications"]["kind"] == "panel"
    assert "notifications" not in addons.helper_addon_ids()


def test_the_page_is_offered_once_the_addon_is_on():
    app = (ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert "  notifications: '/notifications'," in app
    # Administrators only, and only while the addon is on.
    assert "...(isAdmin && notifyInfo?.enabled ? [['notifications', tr(\"Notifications\"), Bell]] : [])" in app
    assert "if (page === 'notifications') return isAdmin ? renderNotificationCenter() : renderDashboard();" in app
    # The send log is a sub-page with a way back, not a dialog.
    assert "if (showNotifyLog && isAdmin) return renderNotifyLog();" in app
    # Secrets are never echoed back into the form.
    assert "setNotifyForm({ ...data, smtp_password: '', telegram_bot_token: '' });" in app
