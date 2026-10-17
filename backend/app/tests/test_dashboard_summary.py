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
    assert body["websites"] == {"total": 2, "active": 2, "suspended": 0,
                                "with_ssl": 1, "waf_on": 2, "without_ssl": ["a2.test"]}
    assert body["ssl"]["secured"] == 1 and body["ssl"]["unsecured"] == ["a2.test"]
    assert body["databases"]["total"] == 1
    assert body["two_factor"] == {"totp": False, "passkeys": 0}
    for key in ("firewall", "waf", "services", "updates", "backups", "malware", "users"):
        assert key not in body


def test_an_administrator_sees_everything(env):
    body = env.as_user("root_admin")
    # A suspended site is not one waiting for a certificate (SNPanel's count).
    assert body["websites"] == {"total": 3, "active": 2, "suspended": 1,
                                "with_ssl": 1, "waf_on": 3, "without_ssl": ["a2.test"]}
    assert body["ssl"]["unsecured_count"] == 2
    assert body["firewall"] == {"enabled": True, "state": "enabled", "chain_active": None}
    assert body["waf"] == {"engine": True}
    assert body["backups"]["state"] == "none", "no schedule is a choice, not a fault"
    assert "two_factor" not in body
    assert body["services"]["stopped"] == ["redis-server"]
    assert body["updates"]["update_available"] is True
    assert body["users"]["total"] == 2
    assert body["backups"]["schedules"] == 0


def test_a_failing_probe_is_left_out_not_fatal(env, monkeypatch):
    def broken():
        raise RuntimeError("helper unavailable")

    monkeypatch.setattr(dashboard.firewall, "is_enabled", broken)
    body = env.as_user("root_admin")
    assert body["firewall"] == {"enabled": None, "state": None, "chain_active": None}
    assert body["websites"]["total"] == 3


def test_a_check_older_than_the_last_update_does_not_claim_an_update(monkeypatch):
    from app.services import updates
    monkeypatch.setattr(updates, "_read_update_state", lambda: {
        "installed_commit": "new", "latest_commit": "old", "remote_version": updates.APP_VERSION,
        "last_checked_at": "2026-09-24T21:04:43Z", "last_update_finished_at": "2026-09-24T22:15:01Z"})
    assert updates.cached_release_summary()["update_available"] is False
    monkeypatch.setattr(updates, "_read_update_state", lambda: {
        "installed_commit": "new", "latest_commit": "newer",
        "last_checked_at": "2026-09-24T23:00:00Z", "last_update_finished_at": "2026-09-24T22:15:01Z"})
    assert updates.cached_release_summary()["update_available"] is True


def test_only_the_always_on_daemons_are_checked(monkeypatch):
    checked = []
    monkeypatch.setattr(dashboard.shell, "run", lambda args, check=False: checked.append(args[-1]) or SimpleNamespace(stdout="active"))
    result = dashboard._services()
    assert result["stopped"] == [] and not any(name.startswith("lsphp") for name in checked)


def test_the_cpanel_style_columns_get_what_each_role_may_see(env, monkeypatch):
    """The dashboard's right-hand column, after cPanel's: a customer gets the
    address to point a domain at (its "Shared IP"); the server's own details
    and the counts across every account are an administrator's."""
    monkeypatch.setattr(dashboard.network, "detect_addresses", lambda: {"ipv4": ["203.0.113.5"], "ipv6": []})
    monkeypatch.setattr(dashboard.mail, "installed", lambda: True)
    monkeypatch.setattr(dashboard.dns_manager, "installed", lambda: False)

    body = env.as_user("alice")
    assert body["server"] == {"ipv4": "203.0.113.5"}
    for key in ("accounts", "mail", "dns"):
        assert key not in body

    body = env.as_user("root_admin")
    assert body["server"]["ipv4"] == "203.0.113.5"
    assert {"hostname", "os", "kernel", "uptime_seconds", "panel_version"} <= set(body["server"])
    assert body["accounts"] == {"end_users": 2, "resellers": 0}
    assert body["mail"] == {"domains": 0, "mailboxes": 0}
    assert "dns" not in body, "an addon that is not installed adds nothing"


def test_the_latest_scan_of_each_target_counts():
    """A clean scan of one site must not hide threats an earlier scan found on another."""
    jobs = [
        {"job_id": "c", "status": "done", "scope": "website", "domains": ["a.test"], "infected": 0, "finished_at": "3"},
        {"job_id": "b", "status": "done", "scope": "website", "domains": ["b.test"], "infected": 2, "quarantined": 1},
        {"job_id": "a", "status": "done", "scope": "website", "domains": ["a.test"], "infected": 5},
        {"job_id": "r", "status": "running", "scope": "server", "domains": []},
    ]
    state = dashboard._malware_state(jobs, installed=True)
    assert state == {"state": "threats", "threats": 2, "quarantined": 1, "last_scan_at": "3", "infected_job": "b"}
    assert dashboard._malware_state([], installed=True)["state"] == "never"
    assert dashboard._malware_state(jobs, installed=False)["state"] == "not_installed"


def test_os_updates_are_counted_for_the_dashboard():
    output = "Listing...\nbash/noble 5.2 amd64 [upgradable from: 5.1]\ncurl/noble 8 amd64 [upgradable from: 7]\n"
    assert dashboard._count_os_updates(output) == 2
