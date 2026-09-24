"""The dashboard status summary: scoping, admin-only parts, failing probes."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import dashboard
from app.api.deps import get_current_user
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.entities import DatabaseAccount, User, Website


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    people = {}
    for name, role in (("root_admin", "admin"), ("alice", "end_user"), ("bob", "end_user")):
        people[name] = User(username=name, email=f"{name}@example.test", role=role, is_active=True,
                            hashed_password=hash_password("PasswordLongEnough1"))
        db.add(people[name])
    db.commit()
    for owner, domain, ssl, status in (("alice", "a1.test", True, "active"), ("alice", "a2.test", False, "active"),
                                       ("bob", "b1.test", False, "suspended")):
        db.add(Website(domain=domain, owner_id=people[owner].id, root_path=f"/home/{owner}/{domain}",
                       document_root="public_html", linux_user=owner, php_version="8.3", app_type="php",
                       status=status, ssl_enabled=ssl))
    db.add(DatabaseAccount(owner_id=people["alice"].id, db_name="alice_db", db_user="alice_db", db_password="x"))
    db.commit()

    monkeypatch.setattr(dashboard.firewall, "is_enabled", lambda: True)
    monkeypatch.setattr(dashboard, "_waf_engine", lambda: "on")
    monkeypatch.setattr(dashboard, "_services", lambda: {"total": 4, "running": 3, "stopped": ["redis-server"]})
    monkeypatch.setattr(dashboard, "_malware", lambda: {"installed": True, "active": True, "last_scan": None})
    monkeypatch.setattr(dashboard.updates, "cached_release_summary",
                        lambda: {"current_version": "1.18.0", "latest_version": "1.19.0", "update_available": True})

    def get_test_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    state = {"user": people["alice"]}
    app.dependency_overrides[get_db] = get_test_db
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    client = TestClient(app)

    def as_user(name):
        state["user"] = db.get(User, people[name].id)
        return client.get("/api/dashboard/summary").json()

    try:
        yield SimpleNamespace(as_user=as_user)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


def test_a_customer_sees_its_own_numbers_and_no_server_state(env):
    body = env.as_user("alice")
    assert body["websites"] == {"total": 2, "active": 2, "suspended": 0}
    assert body["ssl"]["secured"] == 1 and body["ssl"]["unsecured"] == ["a2.test"]
    assert body["databases"]["total"] == 1
    for key in ("firewall", "waf", "services", "updates", "backups", "malware", "users"):
        assert key not in body


def test_an_administrator_sees_everything(env):
    body = env.as_user("root_admin")
    assert body["websites"] == {"total": 3, "active": 2, "suspended": 1}
    assert body["ssl"]["unsecured_count"] == 2
    assert body["firewall"] == {"enabled": True}
    assert body["services"]["stopped"] == ["redis-server"]
    assert body["updates"]["update_available"] is True
    assert body["users"]["total"] == 2
    assert body["backups"]["schedules"] == 0


def test_a_failing_probe_is_left_out_not_fatal(env, monkeypatch):
    def broken():
        raise RuntimeError("helper unavailable")

    monkeypatch.setattr(dashboard.firewall, "is_enabled", broken)
    body = env.as_user("root_admin")
    assert body["firewall"] == {"enabled": None}
    assert body["websites"]["total"] == 3
