"""Demo mode: accounts with a public password that can look at everything and
change nothing -- and that never become full accounts when the addon is off.
"""
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import auth as auth_api
from app.api import deps, maintenance, terminal
from app.core.database import SessionLocal
from app.core.security import hash_password, verify_password
from app.models.entities import User
from app.services import addons, demo_mode, site_users

ROOT = Path(__file__).resolve().parents[3]
APP_JSX = (ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
PUBLIC = "Demo-Public-Pass-1"


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(addons, "STATE_FILE", tmp_path / "addons.json")


@pytest.fixture
def people(monkeypatch):
    linux = {}
    monkeypatch.setattr(site_users, "set_panel_user_password", lambda username, pw: linux.__setitem__(username, pw))
    db = SessionLocal()
    admin = User(username="realadmin", email="a@example.test", hashed_password=hash_password("Admin-Pass-Long-1"),
                 role="admin")
    viewer = User(username="demoview", email="d@example.test", hashed_password=hash_password("Old-Pass-Long-12"),
                  role="admin", totp_enabled=True, totp_secret="SECRET")
    db.add_all([admin, viewer])
    db.commit()
    try:
        yield db, admin, viewer, linux
    finally:
        db.rollback()
        db.query(User).filter(User.username.in_(["realadmin", "demoview"])).delete()
        db.commit()
        db.close()


def _turn_on():
    addons.install("demo", actor="test")


def _request(method, path):
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path), state=SimpleNamespace())


def _save(db, admin, viewer, password=PUBLIC):
    return demo_mode.save(db, [{"user_id": viewer.id, "password": password}], True, admin)


# --- the account -----------------------------------------------------------------

def test_choosing_an_account_publishes_its_panel_password_but_not_its_sftp_one(people):
    db, admin, viewer, linux = people
    before = viewer.token_version or 0
    _save(db, admin, viewer)
    db.refresh(viewer)
    assert verify_password(PUBLIC, viewer.hashed_password)
    assert linux["demoview"] != PUBLIC and len(linux["demoview"]) >= 24
    assert viewer.totp_enabled is False and viewer.totp_secret is None
    assert viewer.token_version == before + 1


def test_you_cannot_make_yourself_a_demo_account(people):
    db, admin, viewer, _ = people
    with pytest.raises(ValueError):
        demo_mode.save(db, [{"user_id": admin.id, "password": PUBLIC}], True, admin)


def test_an_account_taken_off_the_list_loses_the_public_password(people):
    db, admin, viewer, _ = people
    _save(db, admin, viewer)
    demo_mode.save(db, [], True, admin)
    db.refresh(viewer)
    assert not verify_password(PUBLIC, viewer.hashed_password)
    assert demo_mode.demo_user_ids() == set()


# --- read only -------------------------------------------------------------------

@pytest.mark.parametrize("method, path, allowed", [
    ("GET", "/api/websites", True),
    ("GET", "/api/firewall/status", True),
    ("POST", "/api/auth/logout", True),
    ("POST", "/api/maintenance/restore/list", True),
    ("POST", "/api/websites", False),
    ("PUT", "/api/demo/settings", False),
    ("PATCH", "/api/users/me", False),
    ("DELETE", "/api/websites/1", False),
    ("POST", "/api/users/5/password", False),
    ("POST", "/api/auth/impersonate/1", False),
    ("GET", "/api/databases/3/download", False),
    ("GET", "/api/maintenance/backups/2/download", False),
    ("GET", "/api/maintenance/user-backups-download", False),
    ("GET", "/api/maintenance/files/4/download", False),
])
def test_a_demo_session_reads_and_never_writes(people, method, path, allowed):
    db, admin, viewer, _ = people
    _save(db, admin, viewer)
    _turn_on()
    request = _request(method, path)
    if allowed:
        demo_mode.enforce(request, viewer)
        assert request.state.demo is True
    else:
        with pytest.raises(HTTPException) as exc:
            demo_mode.enforce(request, viewer)
        assert exc.value.status_code == 403


def test_other_accounts_are_untouched(people):
    db, admin, viewer, _ = people
    _save(db, admin, viewer)
    _turn_on()
    demo_mode.enforce(_request("POST", "/api/websites"), admin)


def test_with_demo_mode_off_a_demo_account_opens_nothing(people):
    """Off must never mean "a full admin account with a published password"."""
    db, admin, viewer, _ = people
    _save(db, admin, viewer)
    with pytest.raises(HTTPException) as exc:
        demo_mode.enforce(_request("GET", "/api/websites"), viewer)
    assert exc.value.status_code == 401
    login = inspect.getsource(auth_api.login)
    assert "demo_mode.is_demo_account(user) and not demo_mode.is_active()" in login


def test_every_authenticated_request_goes_through_the_guard():
    assert "demo_mode.enforce(request, user)" in inspect.getsource(deps.get_current_user)
    assert "demo_mode.enforce(request, user)" in inspect.getsource(deps.get_current_user_optional)


def test_the_terminal_and_remote_restore_listing_are_refused():
    assert "demo_mode.is_demo_account(user)" in inspect.getsource(terminal.terminal_websocket)
    listing = inspect.getsource(maintenance.list_restore_source)
    assert 'payload.source != "local" and demo_mode.is_demo_request(request)' in listing


# --- the login page ----------------------------------------------------------------

def test_the_login_page_lists_accounts_only_while_on_and_asked_to(people):
    db, admin, viewer, _ = people
    _save(db, admin, viewer)
    assert demo_mode.public_accounts(db) == []
    _turn_on()
    assert demo_mode.public_accounts(db) == [{"username": "demoview", "password": PUBLIC, "role": "admin"}]
    demo_mode.save(db, [{"user_id": viewer.id, "password": PUBLIC}], False, admin)
    assert demo_mode.public_accounts(db) == []


def test_the_panel_offers_the_one_click_sign_in_and_shows_the_banner():
    assert "demoInfo?.accounts?.length > 0" in APP_JSX
    assert "login(account)" in APP_JSX
    assert "currentUser?.demo && <div className=\"demo-banner\"" in APP_JSX
    assert "demo" not in addons.helper_addon_ids()
