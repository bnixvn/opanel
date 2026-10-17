"""An account's main SFTP login: on or off, panel password or its own (as SNPanel's, 2026-10-17).

Shut is the Linux password locked - only the SSH sessions end, the sites and
cron run on. It is open only while the account is active, its SFTP is on and
the panel knows the password it should take.
"""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.entities import User
from app.services import sftp_access, site_users

HELPER = open(__file__.replace("backend\\app\\tests\\test_sftp_access.py", "installer\\files\\opanel-helper.sh")
              .replace("backend/app/tests/test_sftp_access.py", "installer/files/opanel-helper.sh"), encoding="utf-8").read()


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    people = {}
    for name, role in (("boss", "admin"), ("seller", "reseller"), ("carol", "end_user"), ("dave", "end_user")):
        people[name] = User(username=name, email=f"{name}@example.test", role=role, is_active=True,
                            hashed_password=hash_password("PasswordLongEnough1"))
        db.add(people[name])
    db.commit()
    people["carol"].reseller_id = people["seller"].id
    db.commit()
    calls = []
    monkeypatch.setattr(sftp_access.shell, "privileged",
                        lambda verb, helper_args=None, **kw: calls.append((verb, list(helper_args or []), kw.get("input"))))
    monkeypatch.setattr(site_users.shell, "privileged",
                        lambda verb, helper_args=None, **kw: calls.append((verb, list(helper_args or []), kw.get("input"))))

    def get_test_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    state = {"user": people["boss"]}
    app.dependency_overrides[get_db] = get_test_db
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    client = TestClient(app)

    def as_user(name):
        state["user"] = db.get(User, people[name].id)
        return client

    try:
        yield SimpleNamespace(db=db, people=people, calls=calls, as_user=as_user)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


def _fresh(env, name):
    env.db.expire_all()
    return env.db.get(User, env.people[name].id)


def test_off_locks_the_login_and_on_opens_it(env):
    carol = env.people["carol"].id
    answer = env.as_user("boss").put(f"/api/users/{carol}/sftp", json={"enabled": False})
    assert answer.status_code == 200, answer.text
    assert answer.json()["enabled"] is False and answer.json()["open"] is False
    assert ("panel-user-sftp", ["carol", "off"], None) in env.calls
    env.calls.clear()
    assert env.as_user("boss").put(f"/api/users/{carol}/sftp", json={"enabled": True}).json()["open"] is True
    assert ("panel-user-sftp", ["carol", "on"], None) in env.calls


def test_a_password_of_its_own_is_shown_once_and_panel_changes_leave_it(env):
    carol = env.people["carol"].id
    answer = env.as_user("seller").put(f"/api/users/{carol}/sftp", json={"password_mode": "own", "generate": True})
    assert answer.status_code == 200, answer.text
    made = answer.json()["password"]
    assert len(made) >= 20 and answer.json()["password_mode"] == "own"
    assert ("panel-user-password", ["carol"], f"{made}\n") in env.calls
    env.calls.clear()
    # The panel password changes; the SFTP login keeps its own.
    assert env.as_user("boss").post(f"/api/users/{carol}/password", json={"password": "AnotherLongPass1"}).status_code == 200
    assert not [call for call in env.calls if call[0] == "panel-user-password"]


def test_back_on_the_panel_password_it_waits_shut_for_the_next_sign_in(env):
    carol = env.people["carol"].id
    env.as_user("boss").put(f"/api/users/{carol}/sftp", json={"password_mode": "own", "password": "OwnSftpPassword1"})
    env.calls.clear()
    answer = env.as_user("boss").put(f"/api/users/{carol}/sftp", json={"password_mode": "panel"})
    assert answer.json()["waiting_for_sign_in"] is True and answer.json()["open"] is False
    assert ("panel-user-sftp", ["carol", "off"], None) in env.calls
    env.calls.clear()

    user = _fresh(env, "carol")
    sftp_access.sync_panel_password(user, "PasswordLongEnough1")
    assert user.sftp_sync_pending is False
    assert ("panel-user-password", ["carol"], "PasswordLongEnough1\n") in env.calls


def test_the_sign_in_gives_a_waiting_login_its_password(env):
    user = _fresh(env, "carol")
    user.sftp_sync_pending = True
    env.db.commit()
    app.dependency_overrides.pop(get_current_user, None)
    answer = TestClient(app).post("/api/auth/login", data={"username": "carol", "password": "PasswordLongEnough1"})
    assert answer.status_code == 200, answer.text
    assert ("panel-user-password", ["carol"], "PasswordLongEnough1\n") in env.calls
    assert _fresh(env, "carol").sftp_sync_pending is False


def test_a_suspended_or_switched_off_account_stays_shut_through_a_password_change(env):
    carol = env.people["carol"].id
    env.as_user("boss").put(f"/api/users/{carol}/sftp", json={"enabled": False})
    env.calls.clear()
    env.as_user("boss").post(f"/api/users/{carol}/password", json={"password": "AnotherLongPass1"})
    assert ("panel-user-password", ["carol", "locked"], "AnotherLongPass1\n") in env.calls


def test_suspending_shuts_it_and_unsuspending_opens_it(env):
    carol = env.people["carol"].id
    assert env.as_user("boss").patch(f"/api/users/{carol}", json={"is_active": False}).status_code == 200
    assert ("panel-user-sftp", ["carol", "off"], None) in env.calls
    env.calls.clear()
    env.as_user("boss").patch(f"/api/users/{carol}", json={"is_active": True})
    assert ("panel-user-sftp", ["carol", "on"], None) in env.calls


def test_who_may_change_it(env):
    dave, carol = env.people["dave"].id, env.people["carol"].id
    assert env.as_user("seller").put(f"/api/users/{dave}/sftp", json={"enabled": False}).status_code == 404
    assert env.as_user("carol").put(f"/api/users/{carol}/sftp", json={"enabled": False}).status_code == 403
    assert env.as_user("boss").put(f"/api/users/{env.people['boss'].id}/sftp", json={"enabled": False}).status_code == 400


def test_the_account_sets_its_own_with_its_panel_password(env):
    client = env.as_user("carol")
    assert client.post("/api/users/me/sftp-password", json={"current_password": "wrong-password", "password": "OwnSftpPassword1"}).status_code in (400, 401, 403)
    answer = client.post("/api/users/me/sftp-password", json={"current_password": "PasswordLongEnough1", "password": "OwnSftpPassword1"})
    assert answer.status_code == 200, answer.text
    assert answer.json()["password_mode"] == "own" and "password" not in answer.json()
    env.calls.clear()
    back = client.post("/api/users/me/sftp-password", json={"current_password": "PasswordLongEnough1", "password_mode": "panel"})
    assert back.json()["password_mode"] == "panel" and back.json()["waiting_for_sign_in"] is False
    assert ("panel-user-password", ["carol"], "PasswordLongEnough1\n") in env.calls


def test_the_helper_shuts_only_the_ssh_sessions():
    body = HELPER.split("set_panel_user_sftp() {", 1)[1].split("\n}\n", 1)[0]
    assert "usermod -L" in body and "pkill -KILL -u \"$user\" -x sshd" in body
    assert "pkill -KILL -u \"$user\" 2" not in body  # never every process of the account
    assert "panel-user-sftp)" in HELPER
