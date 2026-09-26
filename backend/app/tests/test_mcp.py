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


def test_only_delete_file_is_marked_destructive(env):
    """Clients ask the user before a destructive tool; exactly one tool destroys data."""
    tools = rpc(env, env.token(env.admin, can_write=True), "tools/list").json()["result"]["tools"]
    assert [t["name"] for t in tools if t["annotations"]["destructiveHint"]] == ["delete_file"]
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

    ok = mcp_api.McpTokenCreate(name="laptop", can_write=True)

    # the signed-in session is enough -- but only while the addon is on
    addons.set_running("mcp", False)
    with pytest.raises(HTTPException) as off:
        mcp_api.create_mcp_token(ok, None, env.db, env.alice)
    assert off.value.status_code == 409
    addons.set_running("mcp", True)

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


# ---------------------------------------------------------------------------
# Traffic monitoring and the admin's blocking tools
# ---------------------------------------------------------------------------
def _entry(ip, path="/", status=200, verdict="allow", domain="alice.test", ua="curl/8"):
    return {"domain": domain, "ip": ip, "path": path, "status": status, "verdict": verdict,
            "user_agent": ua, "timestamp": "2026-09-23T10:00:00Z"}


def test_users_get_traffic_tools_but_not_blocking(env):
    names = tool_names(env, env.token(env.alice, can_write=True))
    assert {"read_waf_access_log", "traffic_summary"} <= names
    assert not names & {"block_ip", "add_waf_rule", "list_firewall_rules", "list_waf_rules"}


def test_an_admin_action_token_gets_blocking(env):
    assert {"block_ip", "add_waf_rule", "list_firewall_rules", "list_waf_rules"} <= tool_names(
        env, env.token(env.admin, can_write=True))
    # read-only admin: sees the rules, cannot change them
    names = tool_names(env, env.token(env.admin))
    assert {"list_firewall_rules", "list_waf_rules"} <= names
    assert not names & {"block_ip", "add_waf_rule"}


def test_traffic_summary_counts_and_is_scoped(env, monkeypatch):
    from app.services import waf

    seen = {}

    def fake_entries(domains, domain="", verdict="", query="", lines=5000):
        seen["domains"] = list(domains)
        return [_entry("198.51.100.9", "/wp-login.php", 403, "block"),
                _entry("198.51.100.9", "/wp-login.php", 403, "block"),
                _entry("203.0.113.5", "/", 200)], list(domains), {}

    monkeypatch.setattr(waf, "access_log_entries", fake_entries)
    summary = payload(call(env, env.token(env.alice), "traffic_summary"))
    assert seen["domains"] == ["alice.test"]
    assert summary["requests"] == 3 and summary["blocked"] == 2
    assert summary["status_classes"] == {"4xx": 2, "2xx": 1}
    assert summary["top_ips"][0]["ip"] == "198.51.100.9"
    assert summary["top_ips"][0]["top_paths"] == ["/wp-login.php"]

    other = call(env, env.token(env.alice), "traffic_summary", {"domain": "bob.test"})
    assert other["result"]["isError"] is True


@pytest.fixture
def firewall_calls(monkeypatch):
    from app.services import firewall, mcp as mcp_service

    class _Calls(list):
        kwargs: list

    blocked = _Calls()
    monkeypatch.setattr(firewall, "list_rules", lambda: [{"id": 3, "action": "deny", "type": "ip",
                                                           "network": "91.92.93.0/24"}])
    monkeypatch.setattr(firewall, "block_ip", lambda network, *a, **k: blocked.append(network) or blocked_kwargs.append(k))
    blocked_kwargs = []
    blocked.kwargs = blocked_kwargs
    monkeypatch.setattr(mcp_service, "_server_addresses", lambda: {"15.235.155.243"})
    return blocked


@pytest.mark.parametrize("ip, why", [
    ("0.0.0.0/0", "too wide"),
    ("15.0.0.0/8", "too wide"),
    ("10.0.0.5", "private"),
    ("127.0.0.1", "private"),
    ("15.235.155.0/24", "this server"),
    ("not-an-ip", "address"),
])
def test_block_ip_refuses_what_would_lock_people_out(env, firewall_calls, ip, why):
    reply = call(env, env.token(env.admin, can_write=True), "block_ip", {"ip": ip})
    assert reply["result"]["isError"] is True
    assert why in reply["result"]["content"][0]["text"]
    assert firewall_calls == []


def test_block_ip_blocks_once(env, firewall_calls):
    raw = env.token(env.admin, can_write=True)
    assert payload(call(env, raw, "block_ip", {"ip": "45.155.205.9", "reason": "wp-login brute force"})) == {
        "ip": "45.155.205.9/32", "blocked": True, "already": False}
    assert firewall_calls == ["45.155.205.9/32"]
    again = payload(call(env, raw, "block_ip", {"ip": "91.92.93.0/24"}))
    assert again["already"] is True and firewall_calls == ["45.155.205.9/32"]
    audit = env.db.query(AuditLog).filter(AuditLog.action == "mcp_tool", AuditLog.target == "block_ip").all()
    assert audit and "wp-login brute force" in audit[0].detail
    # The reason also travels with the rule, so the Firewall page can show why
    # an address nobody typed there is blocked.
    assert firewall_calls.kwargs[0] == {"note": "wp-login brute force", "source": "mcp"}


def test_block_ip_never_blocks_the_calling_client(env, firewall_calls):
    from app.services import mcp as mcp_service

    ctx = mcp_service.Context(db=env.db, user=env.admin,
                              token=env.db.query(McpToken).first() or SimpleNamespace(can_write=True),
                              client_ip="45.155.205.77")
    assert "MCP client" in mcp_service._refuse_block(ctx, "45.155.205.0/24")


def test_add_waf_rule_generates_a_safe_rule_and_saves_it(env, monkeypatch):
    from app.api import waf as waf_api
    from app.services import waf

    saved = {}
    monkeypatch.setattr(waf, "custom_rules", lambda: SimpleNamespace(stdout='SecRule X "@rx y" "id:1090004,phase:1"'))
    monkeypatch.setattr(waf_api, "save_waf_custom_rules",
                        lambda payload, current_user: saved.update(content=payload.content))
    raw = env.token(env.admin, can_write=True)
    result = payload(call(env, raw, "add_waf_rule", {"match": "user_agent", "value": "EvilScanner/2.0",
                                                     "note": "scanner seen in log"}))
    assert result["scope"] == "server-wide" and result["rule_id"] == 1090005
    assert 'SecRule REQUEST_HEADERS:User-Agent "@contains evilscanner/2.0"' in saved["content"]
    assert "phase:1" in result["rule"] and "exec" not in result["rule"]

    # raw ModSecurity and macro expansion never get through
    for bad in ('x" "id:1,exec:/bin/sh', "%{REMOTE_ADDR}"):
        reply = call(env, raw, "add_waf_rule", {"match": "user_agent", "value": bad})
        assert reply["result"]["isError"] is True


def test_add_waf_rule_to_one_site_keeps_its_other_rules(env, monkeypatch):
    from app.api import waf as waf_api
    from app.services import waf

    site = env.db.query(Website).filter_by(domain="alice.test").one()
    site.waf_custom_rules = "# hand-written rule kept"
    env.db.commit()
    captured = {}
    monkeypatch.setattr(waf, "custom_rules", lambda: SimpleNamespace(stdout=""))
    monkeypatch.setattr(waf_api, "save_website_waf",
                        lambda payload, website_id, db, current_user: captured.update(
                            rules=payload.custom_rules, website_id=website_id))
    result = payload(call(env, env.token(env.admin, can_write=True), "add_waf_rule",
                          {"match": "path", "value": "/xmlrpc.php", "domain": "alice.test"}))
    assert result["scope"] == "alice.test"
    assert captured["rules"].startswith("# hand-written rule kept")
    assert 'SecRule REQUEST_FILENAME "@beginsWith /xmlrpc.php"' in captured["rules"]


# ---------------------------------------------------------------------------
# Unblocking, and a website's files
# ---------------------------------------------------------------------------
def test_unblock_ip_removes_only_the_matching_block(env, monkeypatch):
    from app.services import firewall

    deleted = []
    monkeypatch.setattr(firewall, "list_rules", lambda: [
        {"id": 3, "action": "deny", "type": "ip", "network": "45.155.205.9/32"},
        {"id": 4, "action": "allow", "type": "ip", "network": "45.155.205.9/32"},
        {"id": 5, "action": "deny", "type": "ip", "network": "91.92.93.0/24"},
    ])
    monkeypatch.setattr(firewall, "delete_rule", lambda rule_id: deleted.append(rule_id))
    raw = env.token(env.admin, can_write=True)
    assert payload(call(env, raw, "unblock_ip", {"ip": "45.155.205.9"}))["removed_rule_ids"] == [3]
    assert deleted == [3]
    assert call(env, raw, "unblock_ip", {"ip": "8.8.8.8"})["result"]["isError"] is True
    assert "unblock_ip" not in tool_names(env, env.token(env.alice, can_write=True))


@pytest.fixture
def site(env, tmp_path):
    """A real website folder for alice, with no Linux user so writes stay in-process."""
    root = tmp_path / "files.test"
    (root / "public_html" / "uploads").mkdir(parents=True)
    (root / "public_html" / ".git").mkdir()
    (root / "public_html" / "index.php").write_text("<?php\necho 'hello';\n// TODO fix the header\n", encoding="utf-8")
    (root / "public_html" / "uploads" / "evil.php").write_text("<?php // TODO in uploads", encoding="utf-8")
    (root / "public_html" / ".git" / "config").write_text("TODO in git", encoding="utf-8")
    env.db.add(Website(domain="files.test", owner_id=env.alice.id, root_path=str(root), document_root="public_html",
                       linux_user=None, php_version="8.3", app_type="php", status="active"))
    env.db.commit()
    return root


def test_file_tools_follow_the_token(env):
    reader = tool_names(env, env.token(env.alice))
    assert {"list_files", "read_file", "search_files"} <= reader
    assert not reader & {"write_file", "delete_file", "move_file", "create_directory"}
    assert {"write_file", "delete_file", "move_file", "create_directory"} <= tool_names(
        env, env.token(env.alice, can_write=True))


def test_list_read_and_search_a_real_site(env, site):
    raw = env.token(env.alice)
    listing = payload(call(env, raw, "list_files", {"domain": "files.test"}))
    assert {e["name"] for e in listing["entries"]} >= {"index.php", "uploads"}

    part = payload(call(env, raw, "read_file", {"domain": "files.test", "path": "public_html/index.php",
                                                "start_line": 2, "line_count": 1}))
    assert part == {"path": "public_html/index.php", "total_lines": 3, "start_line": 2, "end_line": 2,
                    "content": "echo 'hello';\n"}

    found = payload(call(env, raw, "search_files", {"domain": "files.test", "text": "todo"}))
    assert [(m["path"], m["line"]) for m in found["matches"]] == [("public_html/index.php", 3)]


def test_another_users_files_are_not_found(env, site):
    reply = call(env, env.token(env.bob), "read_file", {"domain": "files.test", "path": "public_html/index.php"})
    assert reply["result"]["isError"] is True and "No website" in reply["result"]["content"][0]["text"]


def test_paths_cannot_escape_the_site(env, site):
    reply = call(env, env.token(env.alice), "read_file", {"domain": "files.test", "path": "../../etc/passwd"})
    assert reply["result"]["isError"] is True


def test_write_file_creates_through_the_file_manager_and_logs_no_content(env, site):
    raw = env.token(env.alice, can_write=True)
    secret = "<?php define('DB_PASSWORD', 'hunter2');"
    result = payload(call(env, raw, "write_file", {"domain": "files.test", "path": "public_html/lib/config.php",
                                                   "content": secret}))
    assert result["written"] is True
    assert (site / "public_html" / "lib" / "config.php").read_text(encoding="utf-8") == secret
    audit = env.db.query(AuditLog).filter(AuditLog.target == "write_file").one()
    assert "hunter2" not in audit.detail and "characters" in audit.detail


def test_delete_file_never_takes_the_site_or_its_web_root(env, site):
    raw = env.token(env.alice, can_write=True)
    for path in ("", "/", "public_html", "public_html/"):
        reply = call(env, raw, "delete_file", {"domain": "files.test", "path": path or "."})
        assert reply["result"]["isError"] is True
    assert (site / "public_html" / "index.php").exists()


def test_move_file_moves_then_renames(env, monkeypatch):
    from app.api import maintenance

    calls = []
    monkeypatch.setattr(maintenance, "move_entries",
                        lambda payload, db, current_user: calls.append(("move", payload.paths, payload.destination_path)))
    monkeypatch.setattr(maintenance, "rename_entry",
                        lambda payload, db, current_user: calls.append(("rename", payload.path, payload.new_name)))
    call(env, env.token(env.alice, can_write=True), "move_file",
         {"domain": "alice.test", "path": "public_html/a.php", "new_path": "public_html/old/b.php"})
    assert calls == [("move", ["public_html/a.php"], "public_html/old"), ("rename", "public_html/old/a.php", "b.php")]
