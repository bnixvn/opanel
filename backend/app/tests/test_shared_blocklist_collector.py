"""The shared blocklist collector (tools/shared-blocklist/collector.py), run
for real on a loopback port: what gets listed, and who may list it."""
import base64
import importlib.util
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

COLLECTOR = Path(__file__).resolve().parents[3] / "tools" / "shared-blocklist" / "collector.py"


@pytest.fixture
def collector(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("bnix_collector", COLLECTOR)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "DATA", tmp_path)
    monkeypatch.setattr(mod, "DB_PATH", tmp_path / "c.sqlite3")
    monkeypatch.setattr(mod, "PUBLIC_DIR", tmp_path / "public")
    token = tmp_path / "admin-token"
    token.write_text("s3cret\n", encoding="utf-8")
    monkeypatch.setattr(mod, "ADMIN_TOKEN_FILE", token)
    mod.init_db()
    server = ThreadingHTTPServer(("127.0.0.1", 0), mod.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def call(method, path, body=None, key=None, admin=False, real_ip=None):
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if admin:
            headers["Authorization"] = "Basic " + base64.b64encode(b"admin:s3cret").decode()
            headers["X-Requested-With"] = "bnix-admin"
        if real_ip:
            headers["X-Real-IP"] = real_ip
        req = urllib.request.Request(base + path, method=method, headers=headers,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    mod.call = call
    yield mod
    server.shutdown()


def _listed(mod):
    mod.build_list()
    text = (mod.PUBLIC_DIR / "blocklist.txt").read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line and not line.startswith("#")]


def _approved(mod, name, address):
    status, reg = mod.call("POST", "/v1/register", {"hostname": name, "panel": "opanel"}, real_ip=address)
    assert status == 201 and reg["status"] == "pending"
    assert mod.call("POST", "/admin/api/reporter", {"id": reg["id"], "action": "approve"}, admin=True)[0] == 200
    return reg["key"]


def test_a_new_server_reports_nothing_until_approved(collector):
    status, reg = collector.call("POST", "/v1/register", {"hostname": "vps1"}, real_ip="198.51.100.10")
    assert status == 201
    status, answer = collector.call("POST", "/v1/report", {"items": [{"ip": "45.33.32.156", "source": "fail2ban:sshd"}]},
                                    key=reg["key"])
    assert status == 403 and "approval" in answer["detail"]
    assert collector.call("GET", "/v1/me", key=reg["key"])[1]["status"] == "pending"


def test_one_server_alone_lists_nobody_two_do(collector):
    one = _approved(collector, "vps1", "198.51.100.10")
    two = _approved(collector, "vps2", "198.51.100.11")
    item = {"items": [{"ip": "45.33.32.156", "source": "fail2ban:sshd"}]}
    assert collector.call("POST", "/v1/report", item, key=one) == (200, {"accepted": 1})
    assert _listed(collector) == []
    collector.call("POST", "/v1/report", {"items": [{"ip": "45.33.32.156", "source": "waf:1003001"}]}, key=two)
    assert _listed(collector) == ["45.33.32.156"]


def test_what_is_never_listed(collector):
    one = _approved(collector, "vps1", "198.51.100.10")
    two = _approved(collector, "vps2", "198.51.100.11")
    with collector.db() as conn:
        conn.execute("INSERT INTO protected(name, cidrs, fetched_at) VALUES('cloudflare-v4', ?, 0)",
                     (json.dumps(["104.16.0.0/13"]),))
    collector.call("POST", "/admin/api/allow", {"cidr": "192.0.2.0/24", "note": "office"}, admin=True)
    ips = ["10.0.0.1", "127.0.0.1", "104.16.1.1", "198.51.100.11", "192.0.2.9", "2001:db8::1", "8.8.4.4"]
    items = {"items": [{"ip": ip, "source": "fail2ban"} for ip in ips] + [{"ip": "8.8.8.8", "source": "rumour"}]}
    for key in (one, two):
        collector.call("POST", "/v1/report", items, key=key)
    # Private and documentation ranges, Cloudflare, a reporting server, the
    # allow list and an unknown source are all left out.
    assert _listed(collector) == ["8.8.4.4"]


def test_revoking_a_server_drops_what_it_reported(collector):
    one = _approved(collector, "vps1", "198.51.100.10")
    two = _approved(collector, "vps2", "198.51.100.11")
    for key in (one, two):
        collector.call("POST", "/v1/report", {"items": [{"ip": "8.8.4.4", "source": "fail2ban"}]}, key=key)
    assert _listed(collector) == ["8.8.4.4"]
    reporters = collector.call("GET", "/admin/api/state", admin=True)[1]["reporters"]
    victim = next(r for r in reporters if r["hostname"] == "vps2")
    collector.call("POST", "/admin/api/reporter", {"id": victim["id"], "action": "revoke"}, admin=True)
    assert _listed(collector) == []
    assert collector.call("POST", "/v1/report", {"items": []}, key=two)[0] == 401


def test_the_admin_side_needs_the_token_and_the_header(collector):
    assert collector.call("GET", "/admin/api/state")[0] == 401
    status, _ = collector.call("POST", "/admin/api/allow", {"cidr": "192.0.2.0/24"})
    assert status == 401


def test_registrations_from_one_address_are_limited(collector):
    codes = [collector.call("POST", "/v1/register", {}, real_ip="198.51.100.77")[0] for _ in range(6)]
    assert codes == [201] * 5 + [429]
