"""Back to admin after "Login as", without logging in again.

Logging in as a user used to replace the admin's session outright; the only
way back was to log out and log in again as admin (2026-10-03). The admin's
session now waits in an HttpOnly cookie sent only to /api/auth, and
/auth/impersonation/return puts it back.
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import auth as auth_api
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.entities import AuditLog, User

PASSWORD = "PasswordLongEnough1"


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    for name, role in (("root_admin", "admin"), ("alice", "end_user"), ("other_admin", "admin")):
        db.add(User(username=name, email=f"{name}@example.test", role=role, is_active=True,
                    hashed_password=hash_password(PASSWORD)))
    db.commit()

    def get_test_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = get_test_db
    monkeypatch.setattr(auth_api, "_enforce_rate_limit", lambda key: None)
    client = TestClient(app)
    try:
        yield db, client
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _user(db, name):
    db.expire_all()
    return db.query(User).filter(User.username == name).one()


def _login(client, name):
    response = client.post("/api/auth/login", data={"username": name, "password": PASSWORD})
    assert response.status_code == 200, response.text


def _post(client, path):
    return client.post(path, headers={"X-CSRF-Token": client.cookies.get("opanel_csrf", "")})


def _session(client):
    return client.get("/api/auth/session").json()["user"]


def test_an_admin_logs_in_as_a_user_and_goes_back(env):
    db, client = env
    _login(client, "root_admin")
    alice = _user(db, "alice")
    versions_before = alice.token_version

    assert _post(client, f"/api/auth/impersonate/{alice.id}").status_code == 200
    me = _session(client)
    assert me["username"] == "alice" and me["impersonator"] == "root_admin"

    back = _post(client, "/api/auth/impersonation/return")
    assert back.status_code == 200, back.text
    me = _session(client)
    assert me["username"] == "root_admin" and me["impersonator"] is None
    # The customer's own sessions elsewhere are untouched.
    assert _user(db, "alice").token_version == versions_before
    assert db.query(AuditLog).filter(AuditLog.action == "auth.impersonate_return").count() == 1
    # The borrowed session is spent: going back twice does nothing.
    assert _post(client, "/api/auth/impersonation/return").status_code == 400


def test_an_ordinary_session_has_nowhere_to_go_back_to(env):
    db, client = env
    _login(client, "alice")
    assert _session(client)["impersonator"] is None
    assert _post(client, "/api/auth/impersonation/return").status_code == 400


def test_without_the_saved_session_the_admin_logs_in_again(env):
    db, client = env
    _login(client, "root_admin")
    assert _post(client, f"/api/auth/impersonate/{_user(db, 'alice').id}").status_code == 200
    client.cookies.delete("opanel_impersonator", path="/api/auth")
    response = _post(client, "/api/auth/impersonation/return")
    assert response.status_code == 401
    # Not left logged in as the customer either.
    assert client.get("/api/auth/session").json()["authenticated"] is False


def test_a_saved_session_that_was_logged_out_is_not_brought_back(env):
    db, client = env
    _login(client, "root_admin")
    assert _post(client, f"/api/auth/impersonate/{_user(db, 'alice').id}").status_code == 200
    # The admin's sessions were all revoked meanwhile (password change, logout elsewhere).
    admin = _user(db, "root_admin")
    admin.token_version = (admin.token_version or 0) + 1
    db.commit()
    assert _post(client, "/api/auth/impersonation/return").status_code == 401


def test_logging_in_as_someone_else_needs_the_way_back_first(env):
    db, client = env
    _login(client, "root_admin")
    assert _post(client, f"/api/auth/impersonate/{_user(db, 'other_admin').id}").status_code == 200
    nested = _post(client, f"/api/auth/impersonate/{_user(db, 'alice').id}")
    assert nested.status_code == 409


def test_logging_out_of_a_borrowed_session_does_not_sign_the_customer_out(env):
    db, client = env
    _login(client, "root_admin")
    alice = _user(db, "alice")
    before = alice.token_version
    assert _post(client, f"/api/auth/impersonate/{alice.id}").status_code == 200
    assert _post(client, "/api/auth/logout").status_code == 200
    assert _user(db, "alice").token_version == before
    assert "opanel_impersonator" not in client.cookies


def test_a_normal_login_drops_a_stale_saved_session(env):
    db, client = env
    _login(client, "root_admin")
    assert _post(client, f"/api/auth/impersonate/{_user(db, 'alice').id}").status_code == 200
    _login(client, "alice")
    assert _session(client)["impersonator"] is None
    assert _post(client, "/api/auth/impersonation/return").status_code == 400
