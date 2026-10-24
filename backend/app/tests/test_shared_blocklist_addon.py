"""The Shared blocklist addon on the panel side: what it reads from the box,
what it sends, and that turning it off leaves nothing behind."""
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import addons, shared_blocklist

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER = (PROJECT_ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")


def _events_script() -> str:
    body = HELPER[HELPER.index("shared_blocklist_events() {"):]
    return body[body.index("<<'PY'\n") + len("<<'PY'\n"):body.index("\nPY\n")]


AUDIT = """---a1---A--
[24/Oct/2026:17:20:55 +0700] 179283725518.500281 45.33.32.156 37576 site.vn 443
---a1---H--
ModSecurity: Access denied with code 403 (phase 1). Matched ... [id "1001301"] [msg "opanel blocked PHP sensitive file probe"]

---a1---Z--
---a2---A--
[24/Oct/2026:17:21:10 +0700] 179283725518.500282 66.240.205.34 37577 site.vn 443
---a2---H--
ModSecurity: Access denied with code 403 (phase 2). Matched ... [id "1001501"] [msg "opanel blocked SQL injection"]

---a2---Z--
---a3---A--
[24/Oct/2026:17:22:00 +0700] 179283725518.500283 10.0.0.9 37578 site.vn 443
---a3---H--
ModSecurity: Access denied with code 403 (phase 2). Matched ... [id "1003002"] [msg "php2shell"]

---a3---Z--
"""


def test_the_helper_reads_bans_and_only_attack_only_waf_rules(tmp_path):
    db = tmp_path / "fail2ban.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE bans (jail TEXT, ip TEXT, timeofban INTEGER, bantime INTEGER)")
    conn.executemany("INSERT INTO bans VALUES (?,?,?,?)", [
        ("sshd", "80.94.95.115", 1792800000, 3600), ("sshd", "80.94.95.116", 1700000000, 3600)])
    conn.commit()
    conn.close()
    audit = tmp_path / "audit.log"
    audit.write_text(AUDIT, encoding="utf-8")
    out = subprocess.run([sys.executable, "-", "1750000000", str(audit), str(db)], input=_events_script(),
                         capture_output=True, text=True, check=True).stdout.splitlines()
    rows = [line.split("\t") for line in out]
    # The old ban, the SQL heuristic and the private address are left out.
    assert ["1792800000", "fail2ban:sshd", "80.94.95.115"] in rows
    assert any(row[1:] == ["waf:1001301", "45.33.32.156"] for row in rows)
    assert len(rows) == 2


def test_the_audit_log_keeps_no_request_content():
    conf = HELPER[HELPER.index("shared_blocklist_waf_audit() {"):]
    conf = conf[:conf.index("\n}\n")]
    assert "SecAuditLogParts AHZ" in conf
    assert "SecAuditEngine RelevantOnly" in conf
    # Every site includes the base file; the audit include follows the switch.
    assert '[[ -f "$SHARED_BLOCKLIST_AUDIT_CONF" ]] && echo "Include $SHARED_BLOCKLIST_AUDIT_CONF"' in HELPER


@pytest.fixture
def box(tmp_path, monkeypatch):
    """The addon against a fake collector and a fake firewall."""
    monkeypatch.setattr(shared_blocklist, "STATE_FILE", tmp_path / "shared-blocklist.json")
    on = {"value": True}
    monkeypatch.setattr(shared_blocklist, "enabled", lambda: on["value"])
    calls = []
    urls = set()
    from app.services import firewall

    def ok(text=""):
        return SimpleNamespace(returncode=0, stdout=text, stderr="")

    monkeypatch.setattr(firewall, "blocklists", lambda: ok("URLs:\n" + "\n".join(sorted(urls))))
    monkeypatch.setattr(firewall, "add_blocklist_url", lambda url: (urls.add(url), ok())[1])
    monkeypatch.setattr(firewall, "delete_blocklist_url", lambda url: (urls.discard(url), ok())[1])
    monkeypatch.setattr(firewall, "update_blocklists", lambda: (calls.append(("refresh",)), ok())[1])
    monkeypatch.setattr(shared_blocklist, "_waf_audit", lambda flag: calls.append(("audit", flag)))
    collector = {"status": "pending", "reports": []}

    def call(method, path, body=None, key=""):
        calls.append((method, path))
        if path == "/v1/register":
            return 201, {"id": 7, "key": "bbl_x", "status": "pending"}
        if path == "/v1/me":
            return 200, {"status": collector["status"]}
        if path == "/v1/report":
            if collector["status"] != "approved":
                return 403, {"detail": "waiting"}
            collector["reports"].append(body["items"])
            return 200, {"accepted": len(body["items"])}
        raise AssertionError(path)

    monkeypatch.setattr(shared_blocklist, "_call", call)
    found = [{"at": int(time.time()), "source": "fail2ban:sshd", "ip": "80.94.95.115"}]
    monkeypatch.setattr(shared_blocklist, "events", lambda since: list(found))
    return SimpleNamespace(on=on, calls=calls, urls=urls, collector=collector, found=found)


def test_turning_it_on_registers_subscribes_and_audits(box):
    shared_blocklist.reconcile()
    state = json.loads(shared_blocklist.STATE_FILE.read_text())
    assert state["key"] == "bbl_x" and state["status"] == "pending"
    assert shared_blocklist.LIST_URL in box.urls
    assert ("audit", True) in box.calls and ("refresh",) in box.calls
    # Nothing is sent while the server waits for approval.
    assert box.collector["reports"] == []


def test_once_approved_it_reports_what_it_saw(box, monkeypatch):
    shared_blocklist.reconcile()
    box.collector["status"] = "approved"
    shared_blocklist.check_now()
    shared_blocklist.reconcile()
    assert box.collector["reports"] == [[{"ip": "80.94.95.115", "source": "fail2ban:sshd"}]]
    # Not again on the next minute.
    shared_blocklist.reconcile()
    assert len(box.collector["reports"]) == 1


def test_turning_it_off_leaves_nothing_behind(box):
    shared_blocklist.reconcile()
    box.on["value"] = False
    shared_blocklist.reconcile()
    assert shared_blocklist.LIST_URL not in box.urls
    assert ("audit", False) in box.calls
    assert not shared_blocklist.STATE_FILE.exists()


def test_a_server_that_never_had_it_does_nothing(box):
    box.on["value"] = False
    shared_blocklist.reconcile()
    assert box.calls == []


def test_the_key_is_readable_by_the_panel_only(box):
    shared_blocklist.reconcile()
    if sys.platform != "win32":
        assert shared_blocklist.STATE_FILE.stat().st_mode & 0o077 == 0


def test_it_is_a_panel_addon_off_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(addons, "STATE_FILE", tmp_path / "addons.json")
    assert addons.ADDONS["sharedblocklist"]["kind"] == "panel"
    assert addons.is_enabled("sharedblocklist") is False
    assert "sharedblocklist" not in addons.helper_addon_ids()
