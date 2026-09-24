"""Extra SFTP logins: who may create them, where they may point, and teardown."""
from pathlib import Path
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
from app.models.entities import SftpAccount, User, Website
from app.services import sftp_accounts

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER = PROJECT_ROOT / "installer" / "files" / "opanel-helper.sh"
PASSWORD = "SftpPasswordLong1"


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
    sites = {}
    for owner, domain in (("alice", "alice.test"), ("bob", "bob.test")):
        sites[owner] = Website(domain=domain, owner_id=people[owner].id, root_path=f"/home/{owner}/{domain}",
                               document_root="public_html", linux_user=owner, php_version="8.3",
                               app_type="wordpress", status="active")
        db.add(sites[owner])
    db.commit()

    calls = []

    def fake_privileged(command, helper_args=None, input=None, sensitive=False, fallback=None, check=True):
        calls.append(SimpleNamespace(command=command, args=list(helper_args or []), input=input, sensitive=sensitive))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(sftp_accounts.shell, "privileged", fake_privileged)

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
        return client

    try:
        yield SimpleNamespace(db=db, client=client, people=people, sites=sites, calls=calls, as_user=as_user)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


# ---------------------------------------------------------------------------
# creating
# ---------------------------------------------------------------------------
def test_a_login_for_one_website_calls_the_helper_with_its_folder(env):
    res = env.as_user("alice").post("/api/sftp/accounts", json={
        "suffix": "dev", "website_id": env.sites["alice"].id, "subpath": "public_html", "password": PASSWORD})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["username"] == "alice_dev"
    assert body["directory"] == "/alice.test/public_html"
    call = env.calls[-1]
    assert call.command == "sftp-sub-create"
    assert call.args == ["alice", "alice_dev", "/home/alice/alice.test/public_html"]
    assert call.input == f"{PASSWORD}\n" and call.sensitive


def test_a_login_for_the_whole_account_is_its_home(env):
    res = env.as_user("alice").post("/api/sftp/accounts", json={"suffix": "all", "password": PASSWORD})
    assert res.status_code == 200, res.text
    assert env.calls[-1].args[2] == "/home/alice"
    assert res.json()["directory"] == "/"


@pytest.mark.parametrize("subpath", ["../bob", "public_html/../../bob", "a b", "x;rm", "./x"])
def test_a_folder_cannot_leave_the_website(env, subpath):
    res = env.as_user("alice").post("/api/sftp/accounts", json={
        "suffix": "dev", "website_id": env.sites["alice"].id, "subpath": subpath, "password": PASSWORD})
    assert res.status_code == 400
    assert not env.calls


def test_another_accounts_website_is_refused(env):
    res = env.as_user("alice").post("/api/sftp/accounts", json={
        "suffix": "dev", "website_id": env.sites["bob"].id, "password": PASSWORD})
    assert res.status_code == 400
    assert not env.calls


@pytest.mark.parametrize("suffix", ["Dev!", "a_b", "x" * 17])
def test_names_are_short_lowercase_words(env, suffix):
    res = env.as_user("alice").post("/api/sftp/accounts", json={"suffix": suffix, "password": PASSWORD})
    assert res.status_code in (400, 422)
    assert not env.calls


def test_passwords_with_a_colon_are_refused(env):
    res = env.as_user("alice").post("/api/sftp/accounts", json={"suffix": "dev", "password": "abc:defghijklmn"})
    assert res.status_code == 400
    assert not env.calls


def test_an_account_is_capped(env):
    for index in range(sftp_accounts.MAX_ACCOUNTS_PER_OWNER):
        env.db.add(SftpAccount(owner_id=env.people["alice"].id, username=f"alice_x{index}", directory="/home/alice"))
    env.db.commit()
    res = env.as_user("alice").post("/api/sftp/accounts", json={"suffix": "more", "password": PASSWORD})
    assert res.status_code == 400
    assert not env.calls


def test_an_administrator_creates_on_behalf_of_an_account_not_for_itself(env):
    client = env.as_user("root_admin")
    assert client.post("/api/sftp/accounts", json={"suffix": "dev", "password": PASSWORD}).status_code == 400
    res = client.post("/api/sftp/accounts", json={"suffix": "dev", "password": PASSWORD, "owner_id": env.people["bob"].id})
    assert res.status_code == 200, res.text
    assert res.json()["username"] == "bob_dev"


def test_a_customer_cannot_create_for_someone_else(env):
    res = env.as_user("alice").post("/api/sftp/accounts", json={"suffix": "dev", "password": PASSWORD, "owner_id": env.people["bob"].id})
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# listing, password, delete
# ---------------------------------------------------------------------------
def _seed(env, owner, username, directory):
    account = SftpAccount(owner_id=env.people[owner].id, username=username, directory=directory)
    env.db.add(account)
    env.db.commit()
    return account


def test_a_customer_sees_only_its_own_logins_and_its_primary_login(env):
    _seed(env, "alice", "alice_dev", "/home/alice/alice.test")
    _seed(env, "bob", "bob_dev", "/home/bob/bob.test")
    body = env.as_user("alice").get("/api/sftp").json()
    assert [a["username"] for a in body["accounts"]] == ["alice_dev"]
    assert body["primary"]["username"] == "alice"
    assert body["port"] >= 1


def test_an_administrator_sees_every_login(env):
    _seed(env, "alice", "alice_dev", "/home/alice/alice.test")
    _seed(env, "bob", "bob_dev", "/home/bob/bob.test")
    body = env.as_user("root_admin").get("/api/sftp").json()
    assert {a["username"] for a in body["accounts"]} == {"alice_dev", "bob_dev"}
    assert body["primary"] is None


def test_another_accounts_login_is_not_found(env):
    account = _seed(env, "bob", "bob_dev", "/home/bob/bob.test")
    client = env.as_user("alice")
    assert client.delete(f"/api/sftp/accounts/{account.id}").status_code == 404
    assert client.post(f"/api/sftp/accounts/{account.id}/password", json={"password": PASSWORD}).status_code == 404
    assert not env.calls


def test_password_change_and_delete_reach_the_helper(env):
    account = _seed(env, "alice", "alice_dev", "/home/alice/alice.test")
    client = env.as_user("alice")
    assert client.post(f"/api/sftp/accounts/{account.id}/password", json={"password": PASSWORD}).status_code == 200
    assert env.calls[-1].command == "sftp-sub-password" and env.calls[-1].args == ["alice", "alice_dev"]
    assert client.delete(f"/api/sftp/accounts/{account.id}").status_code == 200
    assert env.calls[-1].command == "sftp-sub-delete" and env.calls[-1].args == ["alice", "alice_dev"]
    assert env.db.query(SftpAccount).count() == 0


def test_deleting_a_website_first_removes_the_logins_jailed_in_it(env):
    site = env.sites["alice"]
    account = _seed(env, "alice", "alice_dev", "/home/alice/alice.test")
    account.website_id = site.id
    _seed(env, "alice", "alice_all", "/home/alice")
    env.db.commit()
    removed = sftp_accounts.delete_for_website(env.db, site)
    env.db.commit()
    assert removed == ["alice_dev"]
    assert [c.args[1] for c in env.calls if c.command == "sftp-sub-delete"] == ["alice_dev"]
    assert {a.username for a in env.db.query(SftpAccount).all()} == {"alice_all"}


def test_deleting_an_account_removes_all_its_logins(env):
    _seed(env, "alice", "alice_dev", "/home/alice/alice.test")
    _seed(env, "alice", "alice_all", "/home/alice")
    removed = sftp_accounts.delete_for_owner(env.db, env.people["alice"])
    env.db.commit()
    assert sorted(removed) == ["alice_all", "alice_dev"]
    assert env.db.query(SftpAccount).count() == 0


# ---------------------------------------------------------------------------
# port and the helper itself
# ---------------------------------------------------------------------------
def test_the_port_is_the_first_global_port_line(tmp_path):
    config = tmp_path / "sshd_config"
    config.write_text("# Port 99\nPort 2200\nMatch Group x\n    Port 1\n", encoding="utf-8")
    assert sftp_accounts.sftp_port(config) == 2200
    config.write_text("Match Group x\nPort 2201\n", encoding="utf-8")
    assert sftp_accounts.sftp_port(config) == 22
    assert sftp_accounts.sftp_port(tmp_path / "missing") == 22


def _function(name: str) -> str:
    text = HELPER.read_text(encoding="utf-8")
    start = text.index(f"\n{name}() {{")
    return text[start:text.index("\n}\n", start)]


def test_the_helper_detaches_the_folder_before_removing_the_login():
    code = "\n".join(line for line in _function("sftp_sub_delete").splitlines() if not line.strip().startswith("#"))
    assert code.index("umount") < code.index("userdel")
    # The uid is shared with the owner: -u would kill the owner's PHP, and
    # -r would follow the jail into a folder still attached to it.
    assert "userdel -r" not in code and "pkill -u" not in code


def test_the_jail_is_root_owned_and_closed_to_other_tenants():
    body = _function("sftp_sub_create")
    assert 'install -d -o root -g "$gid" -m 0750 "$jail"' in body
    assert "useradd -o -u \"$uid\" -g \"$gid\"" in body
    assert "nosuid,nodev" in body


def test_the_owner_goes_after_its_extra_logins():
    body = _function("delete_panel_user_runtime")
    assert body.index("sftp_sub_delete_all_for_owner") < body.index('userdel "$user"')
