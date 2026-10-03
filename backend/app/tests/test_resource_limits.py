"""Resource limits addon: CPU, memory, processes and disk I/O per account.

Plan of 2026-10-04, tried by hand on .41 (OpenLiteSpeed) and .88 (PHP-FPM)
before any of this was written: a systemd slice per account, nested under its
reseller's, and a root agent that moves each account's processes into it.
"""
import hashlib
import json
import re
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agents import opanel_limits_agent as agent
from app.api import auth as auth_api
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.entities import User
from app.services import resource_limits, site_users

ROOT = Path(__file__).resolve().parents[3]
HELPER = (ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
PASSWORD = "PasswordLongEnough1"
NO_LIMITS = {"cpu_percent": 0, "memory_mb": 0, "process_limit": 0, "io_read_mbps": 0, "io_write_mbps": 0}


# --- the agent -------------------------------------------------------------------------

def _config(**overrides):
    data = {
        "version": 1, "io_path": "/home", "sources": ["lshttpd.service", "php*-fpm.service"],
        "groups": [{"slice": "hosting-r5.slice", "limits": {**NO_LIMITS, "cpu_percent": 200}}],
        "accounts": [
            {"slice": "hosting-r5-a5.slice", "uids": [1005], "limits": NO_LIMITS},
            {"slice": "hosting-r5-a12.slice", "uids": [1012], "limits": {**NO_LIMITS, "memory_mb": 512}},
            {"slice": "hosting-a7.slice", "uids": [1007, 1008], "limits": NO_LIMITS},
        ],
    }
    data.update(overrides)
    return data


def _load(tmp_path, data):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return agent.load_config(str(path))


def test_the_agent_reads_a_valid_configuration(tmp_path):
    config = _load(tmp_path, _config())
    assert config["uids"] == {1005: "hosting-r5-a5.slice", 1012: "hosting-r5-a12.slice",
                              1007: "hosting-a7.slice", 1008: "hosting-a7.slice"}
    assert config["groups"]["hosting-r5.slice"]["cpu_percent"] == 200
    assert config["accounts"]["hosting-r5-a12.slice"]["limits"]["memory_mb"] == 512


@pytest.mark.parametrize("change", [
    {"groups": [{"slice": "hosting.slice", "limits": NO_LIMITS}]},            # a cap on every account at once
    {"groups": [{"slice": "hosting-r5-a5.slice", "limits": NO_LIMITS}]},      # an account is not a group
    {"accounts": [{"slice": "hosting-r5.slice", "uids": [1005], "limits": NO_LIMITS}]},
    {"accounts": [{"slice": "system.slice", "uids": [1005], "limits": NO_LIMITS}]},
    {"accounts": [{"slice": "hosting-a1.slice", "uids": [0], "limits": NO_LIMITS}]},       # root
    {"accounts": [{"slice": "hosting-a1.slice", "uids": [33], "limits": NO_LIMITS}]},      # www-data
    {"accounts": [{"slice": "hosting-a1.slice", "uids": [1001], "limits": NO_LIMITS},
                  {"slice": "hosting-a2.slice", "uids": [1001], "limits": NO_LIMITS}]},    # one uid, two accounts
    {"accounts": [{"slice": "hosting-a1.slice", "uids": [1001], "limits": {**NO_LIMITS, "cpu_percent": -1}}]},
    {"accounts": [{"slice": "hosting-a1.slice", "uids": [1001], "limits": {**NO_LIMITS, "memory_mb": "1G"}}]},
    {"sources": ["sshd; rm -rf /"]},
    {"io_path": "home"},
    {"version": 2},
])
def test_the_agent_refuses_a_bad_configuration_whole(tmp_path, change):
    with pytest.raises(ValueError):
        _load(tmp_path, _config(**change))


def test_slices_nest_by_their_dashes():
    assert agent.slice_path("hosting-r5-a12.slice") == "hosting.slice/hosting-r5.slice/hosting-r5-a12.slice"
    assert agent.slice_path("hosting-a7.slice") == "hosting.slice/hosting-a7.slice"
    assert agent.scope_name("hosting-r5-a12.slice", 1012) == "limits-hosting-r5-a12-u1012.scope"


def test_limits_become_systemd_properties():
    props = agent.properties({"cpu_percent": 150, "memory_mb": 1000, "process_limit": 40,
                              "io_read_mbps": 20, "io_write_mbps": 0}, "/home")
    assert "CPUQuota=150%" in props and "TasksMax=40" in props
    # High throttles before Max kills; no swap to escape into.
    assert "MemoryHigh=900M" in props and "MemoryMax=1000M" in props and "MemorySwapMax=0" in props
    assert props.index("IOReadBandwidthMax=") < props.index("IOReadBandwidthMax=/home 20M")
    assert not any(p.startswith("IOWriteBandwidthMax=/") for p in props)
    unlimited = agent.properties(agent.UNLIMITED, "/home")
    assert {"CPUQuota=", "MemoryMax=infinity", "MemoryHigh=infinity", "TasksMax=infinity"} <= set(unlimited)


@pytest.mark.parametrize("cgroup, movable", [
    ("/system.slice/lshttpd.service", True),
    ("/system.slice/php8.3-fpm.service", True),
    ("/system.slice/lshttpd.service/sub", True),
    ("/hosting.slice/hosting-r5.slice/hosting-r5-a5.slice/limits-hosting-r5-a5-u1012.scope", True),
    ("/hosting.slice/hosting-r5.slice/hosting-r5-a12.slice/limits-hosting-r5-a12-u1012.scope", False),
    ("/user.slice/user-1012.slice/session-3.scope", False),     # a login keeps its logind session
    ("/system.slice/bpanel-app-shop.service", False),           # an application's own unit keeps it
    ("/system.slice/cron.service", False),                      # not in this configuration's sources
    (None, False),
])
def test_only_processes_from_shared_services_are_moved(cgroup, movable):
    assert agent.movable(cgroup, ["lshttpd.service", "php*-fpm.service"],
                         "limits-hosting-r5-a12-u1012.scope") is movable


class _Socket:
    def __init__(self, *messages):
        self.messages = list(messages)

    def recv(self, _size):
        if not self.messages:
            raise BlockingIOError
        return self.messages.pop(0)


def _uid_event(tgid, ruid, euid):
    event = struct.pack("=IIQ", agent.PROC_EVENT_UID, 0, 0) + struct.pack("=iiII", tgid, tgid, ruid, euid)
    cn = struct.pack("=IIIIHH", 1, 1, 0, 0, len(event), 0) + event
    return struct.pack("=IHHII", 16 + len(cn), 3, 0, 0, 0) + cn


def test_uid_events_of_hosting_accounts_are_picked_out():
    sock = _Socket(_uid_event(4242, 0, 1012), _uid_event(4243, 33, 33), _uid_event(4244, 1007, 1007))
    # Everything queued is read in one go; a root or www-data change is not ours.
    assert agent.uid_events(sock, {1012: "x", 1007: "y"}) == [4242, 4244]
    assert agent.uid_events(sock, {1012: "x", 1007: "y"}) == []


def test_the_helper_carries_the_agents_hash():
    """The panel user can write the copy under /opt/opanel, so the helper
    installs the agent only if it hashes to the value the helper ships with."""
    source = (ROOT / "backend" / "app" / "agents" / "opanel_limits_agent.py").read_bytes().replace(b"\r\n", b"\n")
    match = re.search(r'^LIMITS_AGENT_SHA256="([0-9a-f]{64})"$', HELPER, re.M)
    assert match, "LIMITS_AGENT_SHA256 not found in opanel-helper.sh"
    assert match.group(1) == hashlib.sha256(source).hexdigest(), (
        "the agent changed: update LIMITS_AGENT_SHA256 in opanel-helper.sh to its new sha256"
    )


def test_the_helper_checks_the_configuration_and_the_hash_before_using_them():
    install = HELPER.split("limits_agent_install() {", 1)[1].split("\n}\n", 1)[0]
    assert 'sha256sum "$tmp"' in install and '!= "$LIMITS_AGENT_SHA256"' in install
    assert install.index("deny") < install.index('mv -f "$tmp" "$LIMITS_AGENT"')
    apply = HELPER.split("limits_apply() {", 1)[1].split("\n}\n", 1)[0]
    assert '--check "$tmp"' in apply and 'cmp -s "$tmp"' in apply


# --- the panel ---------------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    db.add(User(username="root_admin", email="a@example.com", role="admin", is_active=True,
                hashed_password=hash_password(PASSWORD)))
    db.add(User(username="direct", email="d@example.com", role="end_user", is_active=True,
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
    monkeypatch.setattr(site_users, "ensure_panel_user", lambda *a, **k: None)
    monkeypatch.setattr(resource_limits, "sync_in_background", lambda: None)
    client = TestClient(app)
    try:
        yield db, client
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def _login(client, name):
    client.cookies.clear()
    assert client.post("/api/auth/login", data={"username": name, "password": PASSWORD}).status_code == 200


def _call(client, method, path, **kwargs):
    return client.request(method, path, headers={"X-CSRF-Token": client.cookies.get("opanel_csrf", "")}, **kwargs)


def _user(db, name):
    db.expire_all()
    return db.query(User).filter(User.username == name).one()


ACCOUNT = {"email": "x@example.com", "password": PASSWORD}


def test_limits_are_set_by_the_admin_and_by_a_reseller_for_its_customers(env):
    db, client = env
    _login(client, "root_admin")
    shop = _call(client, "POST", "/api/users", json={
        "username": "shop", "role": "reseller", **ACCOUNT,
        "cpu_percent": 100, "memory_mb": 1024, "group_cpu_percent": 400, "group_memory_mb": 8192})
    assert shop.status_code == 200, shop.text
    assert shop.json()["group_cpu_percent"] == 400 and shop.json()["memory_mb"] == 1024
    # A group cap means nothing on an end user and is not kept.
    plain = _call(client, "POST", "/api/users", json={"username": "plain", **ACCOUNT, "group_memory_mb": 4096})
    assert plain.json()["group_memory_mb"] == 0
    # Too little to run a website is refused, not stored.
    assert _call(client, "POST", "/api/users", json={"username": "tiny", **ACCOUNT, "memory_mb": 64}).status_code == 422

    _login(client, "shop")
    cust = _call(client, "POST", "/api/users", json={"username": "cust1", **ACCOUNT, "cpu_percent": 50,
                                                     "group_cpu_percent": 9999}).json()
    assert cust["cpu_percent"] == 50 and cust["group_cpu_percent"] == 0
    assert _call(client, "PATCH", f"/api/users/{cust['id']}", json={"memory_mb": 768}).status_code == 200
    assert _user(db, "cust1").memory_mb == 768
    # Its group's caps and its own limits are the administrator's.
    assert _call(client, "PATCH", f"/api/users/{cust['id']}", json={"group_memory_mb": 4096}).status_code == 403
    assert _call(client, "PATCH", f"/api/users/{_user(db, 'shop').id}", json={"cpu_percent": 800}).status_code == 403


def test_the_configuration_nests_customers_under_their_reseller(env, monkeypatch):
    db, client = env
    _login(client, "root_admin")
    _call(client, "POST", "/api/users", json={"username": "shop", "role": "reseller", **ACCOUNT, "group_cpu_percent": 300})
    _login(client, "shop")
    _call(client, "POST", "/api/users", json={"username": "cust1", **ACCOUNT, "memory_mb": 512})
    uids = {"root_admin": 1000, "shop": 1001, "cust1": 1002, "direct": 1003}
    monkeypatch.setattr(site_users, "linux_user_for_panel_username", lambda name: name)
    monkeypatch.setattr(resource_limits, "_uid_of", lambda name: uids.get(name))
    shop, cust, direct = _user(db, "shop"), _user(db, "cust1"), _user(db, "direct")

    config = resource_limits.build_config(db)
    accounts = {a["slice"]: a for a in config["accounts"]}
    assert set(accounts) == {f"hosting-r{shop.id}-a{shop.id}.slice", f"hosting-r{shop.id}-a{cust.id}.slice",
                             f"hosting-a{direct.id}.slice"}, "the administrator has no slice"
    assert accounts[f"hosting-r{shop.id}-a{cust.id}.slice"]["uids"] == [1002]
    assert accounts[f"hosting-r{shop.id}-a{cust.id}.slice"]["limits"]["memory_mb"] == 512
    assert config["groups"] == [{"slice": f"hosting-r{shop.id}.slice",
                                 "limits": {**NO_LIMITS, "cpu_percent": 300}}]
    assert "lshttpd.service" in config["sources"] and "opanel-api.service" in config["sources"]
    # What the panel hands over is exactly what the agent accepts.
    path = Path(resource_limits.__file__).parent / "_limits_test_config.json"
    try:
        path.write_text(json.dumps(config), encoding="utf-8")
        assert agent.load_config(str(path))["uids"][1002] == f"hosting-r{shop.id}-a{cust.id}.slice"
    finally:
        path.unlink(missing_ok=True)


def test_nothing_is_handed_over_until_the_addon_is_installed(env, monkeypatch):
    db, _client = env
    calls = []
    monkeypatch.setattr(resource_limits.shell, "privileged", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(resource_limits, "installed", lambda: False)
    resource_limits.sync(db)
    assert calls == []

    monkeypatch.setattr(resource_limits, "installed", lambda: True)
    monkeypatch.setattr(resource_limits, "agent_current", lambda: False)
    monkeypatch.setattr(resource_limits.shell, "privileged",
                        lambda *a, **k: calls.append((a, k)) or SimpleNamespace(returncode=0, stdout="", stderr=""))
    resource_limits.sync(db)
    # A new agent first, then the configuration.
    assert [c[0][0] for c in calls] == ["limits-agent-install", "limits-apply"]
    assert json.loads(calls[1][1]["input"])["version"] == 1


def test_each_caller_sees_the_accounts_it_manages(env, monkeypatch, tmp_path):
    db, client = env
    _login(client, "root_admin")
    _call(client, "POST", "/api/users", json={"username": "shop", "role": "reseller", **ACCOUNT})
    _login(client, "shop")
    cust = _call(client, "POST", "/api/users", json={"username": "cust1", **ACCOUNT}).json()
    shop, direct = _user(db, "shop"), _user(db, "direct")
    usage = {"time": 0, "slices": {f"hosting-r{shop.id}-a{cust['id']}.slice": {"cpu_percent": 12.5, "memory_mb": 300}}}
    monkeypatch.setattr(resource_limits, "USAGE_FILE", tmp_path / "usage.json")
    (tmp_path / "usage.json").write_text(json.dumps(usage), encoding="utf-8")

    seen = _call(client, "GET", "/api/resource-limits").json()
    assert set(seen["accounts"]) == {str(shop.id), str(cust["id"])}
    assert seen["accounts"][str(cust["id"])]["usage"]["cpu_percent"] == 12.5
    assert "group_limits" in seen["accounts"][str(shop.id)]
    assert _call(client, "GET", f"/api/resource-limits/{direct.id}/history").status_code == 404
    assert _call(client, "GET", f"/api/resource-limits/{cust['id']}/history").status_code == 200

    _login(client, "direct")
    assert set(_call(client, "GET", "/api/resource-limits").json()["accounts"]) == {str(direct.id)}
    _login(client, "root_admin")
    assert set(_call(client, "GET", "/api/resource-limits").json()["accounts"]) == {
        str(shop.id), str(cust["id"]), str(direct.id)}


def test_a_plan_carries_resource_limits(env):
    _db, client = env
    _login(client, "root_admin")
    made = _call(client, "POST", "/api/plans", json={"slug": "pro", "name": "Pro", "cpu_percent": 200, "memory_mb": 2048})
    assert made.status_code == 200, made.text
    assert made.json()["cpu_percent"] == 200 and made.json()["memory_mb"] == 2048
    assert _call(client, "PATCH", f"/api/plans/{made.json()['id']}", json={"process_limit": 5}).status_code == 422
    assert _call(client, "PATCH", f"/api/plans/{made.json()['id']}", json={"process_limit": 50}).json()["process_limit"] == 50


def test_administrators_hear_when_an_account_is_stopped_at_its_memory_limit(env, monkeypatch, tmp_path):
    from app.services import notifications

    db, _client = env
    direct = _user(db, "direct")
    usage = tmp_path / "usage.json"
    monkeypatch.setattr(resource_limits, "USAGE_FILE", usage)
    monkeypatch.setattr(resource_limits, "installed", lambda: True)
    monkeypatch.setattr(notifications, "SessionLocal", lambda: db.__class__(bind=db.get_bind()))
    sent = []
    monkeypatch.setattr(notifications, "notify_admin", lambda event, ctx, **k: sent.append((event, ctx)))

    def counts(kills):
        usage.write_text(json.dumps({"slices": {f"hosting-a{direct.id}.slice": {"oom_kills": kills}}}), encoding="utf-8")

    state = {}
    counts(4)
    notifications.check_resource_limits(state)
    assert sent == [], "the first look only learns the count"
    counts(7)
    notifications.check_resource_limits(state)
    assert sent == [("resource_limit", {"username": "direct", "count": 3, "memory": 0})]
    counts(0)   # the slice was made again
    notifications.check_resource_limits(state)
    assert len(sent) == 1
