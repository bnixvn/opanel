"""The browser terminal (operator, 2026-10-17): a real shell, a sandbox per session.

What must hold:
- only a hosting customer whose package has it opens one; an administrator or
  a reseller only by logging in as that customer, never as itself, never root;
- at most two at once per account, closed when idle for 30 minutes, after
  eight hours, when the browser goes away, and when the account loses it;
- the sandbox is the helper's: the account's own user and slice, its own home
  only, a private /tmp, its own processes only, a read-only system, no way up;
- opening and closing are audited, keystrokes are not.
"""
import inspect
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import terminal as terminal_api
from app.api.deps import get_current_user
from app.core.database import Base, get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.entities import AuditLog, HostingPlan, User, Website
from app.services import provisioning, terminal

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER = (PROJECT_ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
SESSION_FN = HELPER[HELPER.index("terminal_session() {"):HELPER.index("require_terminal_cwd() {")]


def account(role="end_user", **extra):
    defaults = dict(username="alice", email="a@example.test", role=role, is_active=True,
                    hashed_password="x", terminal_enabled=False, token_version=0)
    defaults.update(extra)
    return User(**defaults)


@pytest.fixture(autouse=True)
def no_sessions():
    terminal._sessions.clear()
    yield
    terminal._sessions.clear()


# --- who may open one ------------------------------------------------------------

def test_a_customer_whose_package_has_it():
    assert terminal.access_problem(account(terminal_enabled=True)) is None
    assert terminal.access_problem(account()) == "disabled", "off unless the package turns it on"


@pytest.mark.parametrize("role", ["admin", "reseller"])
def test_administrators_and_resellers_log_in_as_the_customer(role):
    assert terminal.access_problem(account(role=role, terminal_enabled=True)) == "impersonate"


def test_not_a_disabled_account_nor_the_demo(monkeypatch):
    assert terminal.access_problem(account(terminal_enabled=True, is_active=False)) == "inactive"
    monkeypatch.setattr(terminal.demo_mode, "is_demo_account", lambda user: True)
    assert terminal.access_problem(account(terminal_enabled=True)) == "demo"


# --- how many, how long ---------------------------------------------------------------

def test_two_sessions_at_once_per_account():
    first = terminal.reserve(1, "alice", "home")
    second = terminal.reserve(1, "alice", "site.test")
    assert first and second and first.unit != second.unit
    assert terminal.reserve(1, "alice", "home") is None
    assert terminal.reserve(2, "bob", "home") is not None, "another account has its own two"
    terminal.release(first)
    assert terminal.reserve(1, "alice", "home") is not None


def test_the_limits():
    assert terminal.MAX_SESSIONS_PER_ACCOUNT == 2
    assert terminal.IDLE_SECONDS == 30 * 60
    assert terminal.MAX_SECONDS == 8 * 3600
    assert "-p RuntimeMaxSec=8h" in SESSION_FN, "the unit ends at eight hours even without the panel"


def test_ending_an_accounts_sessions_stops_their_units(monkeypatch):
    stopped, told = [], []
    monkeypatch.setattr(terminal, "stop_unit", stopped.append)
    one = terminal.reserve(1, "alice", "home")
    one.close_request = told.append
    terminal.reserve(2, "bob", "home")
    assert terminal.end_sessions(1, "access-changed") == 1
    assert stopped == [one.unit] and told == ["access-changed"]


def test_the_socket_closes_on_idle_time_access_and_disconnect():
    bridge = inspect.getsource(terminal_api._bridge)
    for reason in ('"idle"', '"max-time"', '"access-changed"', '"closed"', '"exit"'):
        assert reason in bridge
    serve = inspect.getsource(terminal_api._serve)
    assert serve.index("terminal.hang_up") < serve.rindex("terminal.release"), "the shell ends before the slot frees"


def test_a_session_is_ended_when_its_browser_session_is():
    still = inspect.getsource(terminal_api._still_allowed)
    assert "token_version" in still and "RevokedToken" in still and "access_problem" in still


# --- the sandbox -------------------------------------------------------------------

def test_unit_names_are_the_helpers():
    pattern = re.search(r"TERMINAL_UNIT_RE='([^']+)'", HELPER).group(1)
    pattern = pattern.replace("[[:alnum:]]", "[A-Za-z0-9]")
    assert re.fullmatch(pattern, terminal.unit_name("alice_01", "0123456789ab"))
    assert '[[ "$unit" == "opanel-term-$user-"* ]]' in SESSION_FN, "a unit carries the name of its own user"


@pytest.mark.parametrize("prop", [
    "User=$user", "ProtectSystem=strict", "ProtectHome=tmpfs", "BindPaths=$home", "PrivateTmp=yes",
    "PrivateIPC=yes", "ProtectProc=invisible", "ProcSubset=pid", "NoNewPrivileges=yes",
    "RestrictSUIDSGID=yes", '"CapabilityBoundingSet="', "PrivateDevices=yes", "RestrictNamespaces=yes",
    "InaccessiblePaths=", '--slice="$slice"',
])
def test_the_sandbox(prop):
    assert prop in SESSION_FN


def test_only_a_hosting_accounts_user():
    check = HELPER[HELPER.index("require_terminal_user() {"):HELPER.index("terminal_prepare_home() {")]
    assert "require_linux_user" in check, "root and the system users are refused by name"
    assert "uid >= 1000 && uid < 60000" in check
    assert "$opanel_SFTP_GROUP" in check
    hidden = re.search(r'TERMINAL_HIDDEN_PATHS="([^"]+)"', HELPER).group(1).split()
    for path in ("/opt/opanel", "/etc/opanel", "/var/lib/opanel-sftp", "/var/log", "/var/lib/mysql"):
        assert path in hidden


def test_the_panel_starts_the_helper_on_a_pty_and_never_a_shell():
    spawn = inspect.getsource(terminal.spawn)
    assert '["setsid", "-c", "sudo", "-n", HELPER_PATH, "terminal-session"' in spawn
    assert "shell=True" not in spawn


def test_the_old_command_list_is_gone():
    assert not hasattr(terminal, "ALLOWED_COMMANDS")
    assert not hasattr(terminal, "exec_command")
    assert not any(getattr(route, "path", "").startswith("/api/terminal/exec") for route in app.routes)


def test_a_window_size_stays_sane():
    assert terminal.clamp_size(10, 1) == (20, 5)
    assert terminal.clamp_size(9999, 9999) == (500, 200)
    assert terminal.clamp_size("x", None) == (terminal.DEFAULT_COLS, terminal.DEFAULT_ROWS)


# --- the socket -----------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    people = {
        "root_admin": account(role="admin", username="root_admin"),
        "reseller": account(role="reseller", username="reseller", terminal_enabled=True),
        "alice": account(username="alice", terminal_enabled=True),
        "bob": account(username="bob"),
    }
    db.add_all(people.values())
    db.commit()
    db.add(Website(domain="bob.test", owner_id=people["bob"].id, root_path="/home/bob/bob.test", linux_user="bob"))
    db.commit()
    monkeypatch.setattr(terminal_api, "SessionLocal", Session)
    monkeypatch.setattr(terminal, "available", lambda: True)
    client = TestClient(app)

    def connect(name, path="/api/terminal/ws"):
        token = create_access_token(name, {"tv": 0})
        client.cookies.set("opanel_session", token)
        with client.websocket_connect(path) as ws:
            return ws.receive_json()

    yield SimpleNamespace(db=db, people=people, connect=connect, Session=Session)
    db.close()


def test_an_administrator_gets_no_shell_of_its_own(env):
    assert env.connect("root_admin") == {"type": "closed", "reason": "impersonate", "code": 4003}
    assert env.connect("reseller")["reason"] == "impersonate"


def test_a_customer_without_it_in_the_package(env):
    assert env.connect("bob")["reason"] == "disabled"


def test_someone_elses_website(env):
    site = env.db.query(Website).filter(Website.domain == "bob.test").one()
    assert env.connect("alice", f"/api/terminal/ws/{site.id}")["reason"] == "not-found"


def test_a_third_session(env):
    alice = env.people["alice"]
    terminal.reserve(alice.id, "alice", "home")
    terminal.reserve(alice.id, "alice", "home")
    assert env.connect("alice")["reason"] == "too-many"


def test_signed_out(env):
    with TestClient(app).websocket_connect("/api/terminal/ws") as ws:
        assert ws.receive_json()["reason"] == "signed-out"


def test_opening_and_closing_are_audited_not_the_keystrokes():
    serve = inspect.getsource(terminal_api._serve)
    assert '"terminal_open"' in serve and '"terminal_close"' in serve
    assert "by={impersonator}" in serve, "a session opened by an administrator says so"
    assert "log_action" not in inspect.getsource(terminal_api._bridge)


# --- the setting ----------------------------------------------------------------------

@pytest.fixture
def api(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    people = {
        "root_admin": account(role="admin", username="root_admin"),
        "plain_reseller": account(role="reseller", username="plain_reseller"),
        "alice": account(username="alice"),
    }
    db.add_all(people.values())
    db.commit()
    people["alice"].reseller_id = people["plain_reseller"].id
    db.commit()
    ended = []
    from app.api import users as users_api
    monkeypatch.setattr(users_api.terminal, "end_sessions", lambda user_id, reason: ended.append((user_id, reason)))
    monkeypatch.setattr(users_api.resource_limits, "sync_in_background", lambda: None)

    def get_test_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    state = {}
    app.dependency_overrides[get_db] = get_test_db
    app.dependency_overrides[get_current_user] = lambda: state["user"]

    def as_user(name):
        state["user"] = db.get(User, people[name].id)
        return TestClient(app)

    try:
        yield SimpleNamespace(db=db, people=people, as_user=as_user, ended=ended)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


def test_a_reseller_offers_it_only_if_the_administrator_gave_it(api):
    alice = api.people["alice"]
    response = api.as_user("plain_reseller").patch(f"/api/users/{alice.id}", json={"terminal_enabled": True})
    assert response.status_code == 403
    response = api.as_user("plain_reseller").post("/api/plans", json={"slug": "p", "name": "P", "terminal_enabled": True})
    assert response.status_code == 403


def test_turning_it_off_ends_the_open_sessions(api):
    alice = api.people["alice"]
    response = api.as_user("root_admin").patch(f"/api/users/{alice.id}", json={"terminal_enabled": True})
    assert response.status_code == 200 and response.json()["terminal_enabled"] is True
    assert api.ended == []
    response = api.as_user("root_admin").patch(f"/api/users/{alice.id}", json={"terminal_enabled": False})
    assert response.json()["terminal_enabled"] is False
    assert api.ended == [(alice.id, "access-changed")]


def test_a_package_carries_it_to_the_account(api):
    plan = provisioning.create_plan(api.db, slug="shell", name="Shell", terminal_enabled=True)
    assert plan.terminal_enabled is True
    source = inspect.getsource(provisioning)
    assert "terminal_enabled=bool(plan.terminal_enabled)" in source
    assert "user.terminal_enabled = bool(plan.terminal_enabled)" in source


def test_deleting_an_account_ends_its_sessions_before_userdel():
    from app.api import users as users_api
    delete = inspect.getsource(users_api.delete_user)
    assert delete.index("terminal.end_sessions") < delete.index("site_users.delete_panel_user")


def test_global_npm_and_composer_installs_land_in_the_account_home():
    # /usr is read-only in the sandbox: `npm install -g` failed with EACCES
    # (operator, 2026-10-24, on BPanel). The prefix is ~/.local, on PATH.
    assert '-E "npm_config_prefix=$home/.local"' in SESSION_FN
    assert 'path_value="$home/.local/bin:$home/.composer/vendor/bin:$path_value"' in SESSION_FN
