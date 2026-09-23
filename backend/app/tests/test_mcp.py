"""The MCP endpoint: who gets in, what each token can see, what it can do."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.entities import AuditLog, McpToken, User, Website
from app.services import addons, mcp


@pytest.fixture
def env(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()

    def users(*specs):
        out = []
        for name, role in specs:
            user = User(username=name, email=f"{name}@example.test", role=role, is_active=True,
                        hashed_password=hash_password("PasswordLongEnough1"))
            db.add(user)
            out.append(user)
        db.commit()
        return out

    admin, alice, bob = users(("root_admin", "admin"), ("alice", "end_user"), ("bob", "end_user"))
    for owner, domain in ((alice, "alice.test"), (bob, "bob.test")):
        db.add(Website(domain=domain, owner_id=owner.id, root_path=f"/home/{owner.username}/{domain}",
                       document_root="public_html", linux_user=owner.username, php_version="8.3",
                       app_type="wordpress", status="active", waf_enabled=True))
    db.commit()

    def get_test_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = get_test_db
    monkeypatch.setattr(addons, "STATE_FILE", tmp_path / "addons.json")
    addons.install("mcp", actor="test")

    def token(user, can_write=False):
        return mcp.create_token(db, user, f"{user.username}-token", can_write, 30)[1]

    client = TestClient(app)
    try:
        yield SimpleNamespace(db=db, client=client, admin=admin, alice=alice, bob=bob, token=token)
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()


def rpc(env, token, method, params=None, msg_id=1, headers=None):
    body = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    return env.client.post("/api/mcp", json=body,
                           headers={"Authorization": f"Bearer {token}", **(headers or {})})


def call(env, token, name, arguments=None):
    reply = rpc(env, token, "tools/call", {"name": name, "arguments": arguments or {}}).json()
    return reply


def tool_names(env, token):
    return {tool["name"] for tool in rpc(env, token, "tools/list").json()["result"]["tools"]}


def payload(reply):
    assert "result" in reply, reply
    return json.loads(reply["result"]["content"][0]["text"])


# ---------------------------------------------------------------------------
# Getting in
# ---------------------------------------------------------------------------
def test_no_token_is_401_with_a_bearer_challenge(env):
    response = env.client.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


def test_a_wrong_token_is_401(env):
    assert rpc(env, "opmcp_not-a-real-token", "ping").status_code == 401


def test_an_expired_token_is_401(env):
    raw = env.token(env.alice)
    row = env.db.query(McpToken).one()
    row.expires_at = datetime.utcnow() - timedelta(minutes=1)
    env.db.commit()
    assert rpc(env, raw, "ping").status_code == 401


def test_a_suspended_account_is_401(env):
    raw = env.token(env.alice)
    env.alice.is_active = False
    env.db.commit()
    assert rpc(env, raw, "ping").status_code == 401


def test_the_endpoint_is_off_until_the_addon_is_on(env):
    raw = env.token(env.alice)
    addons.set_running("mcp", False)
    assert rpc(env, raw, "ping").status_code == 404
    addons.set_running("mcp", True)
    assert rpc(env, raw, "ping").status_code == 200


def test_a_page_on_another_site_is_refused(env):
    raw = env.token(env.alice)
    response = rpc(env, raw, "ping", headers={"Origin": "https://evil.example"})
    assert response.status_code == 403


def test_get_offers_no_stream(env):
    assert env.client.get("/api/mcp").status_code == 405


# ---------------------------------------------------------------------------
# The protocol
# ---------------------------------------------------------------------------
def test_initialize_negotiates_the_version(env):
    raw = env.token(env.alice)
    result = rpc(env, raw, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                          "clientInfo": {"name": "t", "version": "1"}}).json()["result"]
    assert result["protocolVersion"] == "2025-03-26"
    assert result["capabilities"]["tools"] == {"listChanged": False}
    assert result["serverInfo"]["name"] == "opanel"

    unknown = rpc(env, raw, "initialize", {"protocolVersion": "1999-01-01"}).json()["result"]
    assert unknown["protocolVersion"] == mcp.SUPPORTED_PROTOCOL_VERSIONS[0]


def test_a_notification_is_accepted_with_no_body(env):
    raw = env.token(env.alice)
    response = env.client.post("/api/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                               headers={"Authorization": f"Bearer {raw}"})
    assert response.status_code == 202
    assert response.content == b""


def test_a_batch_gets_one_reply_per_request(env):
    raw = env.token(env.alice)
    response = env.client.post("/api/mcp", json=[
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 7, "method": "ping"},
    ], headers={"Authorization": f"Bearer {raw}"})
    assert [item["id"] for item in response.json()] == [7]


def test_an_unknown_method_is_a_jsonrpc_error(env):
    raw = env.token(env.alice)
    assert rpc(env, raw, "resources/list").json()["error"]["code"] == mcp.METHOD_NOT_FOUND


# ---------------------------------------------------------------------------
# What each token sees
# ---------------------------------------------------------------------------
def test_a_read_only_user_token_sees_no_action_and_no_admin_tool(env):
    names = tool_names(env, env.token(env.alice))
    assert {"whoami", "list_websites", "read_site_log", "list_backups"} <= names
    assert not names & {"create_backup", "issue_ssl_certificate", "set_website_waf"}
    assert not names & {"list_users", "list_services", "restart_service", "run_backup_schedule"}


def test_an_action_token_gets_the_actions_but_still_no_admin_tool(env):
    names = tool_names(env, env.token(env.alice, can_write=True))
    assert {"create_backup", "issue_ssl_certificate", "set_website_waf"} <= names
    assert not names & {"list_users", "restart_service", "run_backup_schedule"}


def test_an_admin_action_token_gets_everything(env):
    names = tool_names(env, env.token(env.admin, can_write=True))
    assert {"list_users", "list_services", "restart_service", "run_backup_schedule", "create_backup"} <= names


def test_every_tool_says_it_deletes_nothing(env):
    tools = rpc(env, env.token(env.admin, can_write=True), "tools/list").json()["result"]["tools"]
    assert all(tool["annotations"]["destructiveHint"] is False for tool in tools)
    assert all(tool["inputSchema"]["additionalProperties"] is False for tool in tools)


def test_a_user_sees_only_their_own_websites(env):
    sites = payload(call(env, env.token(env.alice), "list_websites"))
    assert [site["domain"] for site in sites] == ["alice.test"]


def test_an_admin_sees_every_website(env):
    sites = payload(call(env, env.token(env.admin), "list_websites"))
    assert {site["domain"] for site in sites} == {"alice.test", "bob.test"}


def test_someone_elses_website_is_not_found(env):
    reply = call(env, env.token(env.alice), "get_website", {"domain": "bob.test"})
    assert reply["result"]["isError"] is True
    assert "No website" in reply["result"]["content"][0]["text"]


def test_a_user_cannot_name_another_account(env):
    reply = call(env, env.token(env.alice), "list_backups", {"username": "bob"})
    assert reply["result"]["isError"] is True


def test_a_hidden_tool_cannot_be_called_either(env):
    reply = call(env, env.token(env.alice), "set_website_waf", {"domain": "alice.test", "enabled": False})
    assert reply["error"]["code"] == mcp.INVALID_PARAMS
    assert "Unknown tool" in reply["error"]["message"]


def test_arguments_are_checked(env):
    raw = env.token(env.alice)
    assert call(env, raw, "get_website", {})["result"]["isError"] is True
    assert call(env, raw, "get_website", {"domain": "alice.test", "sql": "drop"})["result"]["isError"] is True
    assert call(env, raw, "read_site_log", {"domain": "alice.test", "lines": 10_000})["result"]["isError"] is True


def test_an_action_runs_as_the_token_owner_and_is_audited(env, monkeypatch):
    from app.api import websites as websites_api

    seen = {}

    def fake_set_waf(website_id, payload, request, db, current_user):
        seen.update(website_id=website_id, enabled=payload.waf_enabled, user=current_user.username)
        return SimpleNamespace(domain="alice.test", waf_enabled=payload.waf_enabled)

    monkeypatch.setattr(websites_api, "set_website_waf", fake_set_waf)
    reply = call(env, env.token(env.alice, can_write=True), "set_website_waf",
                 {"domain": "alice.test", "enabled": False})

    assert payload(reply) == {"domain": "alice.test", "waf_enabled": False}
    assert seen["user"] == "alice" and seen["enabled"] is False
    audit = env.db.query(AuditLog).filter(AuditLog.action == "mcp_tool").one()
    assert audit.target == "set_website_waf" and audit.user_id == env.alice.id


def test_a_failing_tool_is_an_error_result_not_a_500(env, monkeypatch):
    from app.api import websites as websites_api

    def boom(**kwargs):
        raise RuntimeError("certbot said no")

    monkeypatch.setattr(websites_api, "enable_ssl", boom)
    reply = call(env, env.token(env.alice, can_write=True), "issue_ssl_certificate", {"domain": "alice.test"})
    assert reply["result"]["isError"] is True
    assert "certbot said no" in reply["result"]["content"][0]["text"]


def test_the_restart_tool_never_stops_anything(env):
    tools = {t["name"]: t for t in rpc(env, env.token(env.admin, can_write=True), "tools/list").json()["result"]["tools"]}
    assert tools["restart_service"]["inputSchema"]["properties"]["action"]["enum"] == ["restart", "reload"]


# ---------------------------------------------------------------------------
# Token management from a panel session
# ---------------------------------------------------------------------------
def test_token_management_rules(env, monkeypatch):
    from app.api import mcp as mcp_api
    from fastapi import HTTPException

    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"), headers={})
    payload_in = mcp_api.McpTokenCreate(name="laptop", can_write=True, current_password="wrong")
    with pytest.raises(HTTPException):
        mcp_api.create_mcp_token(payload_in, request, env.db, env.alice)

    ok = mcp_api.McpTokenCreate(name="laptop", can_write=True, current_password="PasswordLongEnough1")
    created = mcp_api.create_mcp_token(ok, None, env.db, env.alice)
    assert created["token"].startswith("opmcp_") and created["can_write"] is True

    # someone else's token is not theirs to revoke
    with pytest.raises(HTTPException) as denied:
        mcp_api.revoke_mcp_token(created["id"], None, env.db, env.bob)
    assert denied.value.status_code == 403
    # an admin sees and revokes anyone's
    assert [t["username"] for t in mcp_api.list_mcp_tokens(True, env.db, env.admin)] == ["alice"]
    mcp_api.revoke_mcp_token(created["id"], None, env.db, env.admin)
    assert env.db.query(McpToken).count() == 0


def test_the_raw_token_is_never_stored(env):
    raw = env.token(env.alice)
    row = env.db.query(McpToken).one()
    assert raw not in (row.token_hash, row.prefix) and len(row.token_hash) == 64
    assert raw.startswith(row.prefix)


def test_removing_the_addon_revokes_every_token(env):
    from app.api import addons as addons_api

    env.token(env.alice)
    env.token(env.bob)
    addons_api.uninstall_addon("mcp", None, env.admin, env.db)
    assert env.db.query(McpToken).count() == 0
    assert addons.is_enabled("mcp") is False
