import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api import firewall as firewall_api
from app.services import firewall
from app.services.shell import CommandResult


def test_add_blocklist_url_uses_privileged_helper(monkeypatch):
    calls = []

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        calls.append((helper_command, helper_args, kwargs))
        return CommandResult(helper_command, 0, "Blocklist URL added", "")

    monkeypatch.setattr(firewall.shell, "privileged", fake_privileged)

    result = firewall.add_blocklist_url("https://example.test/list.txt")

    assert result.returncode == 0
    assert calls[0][0] == "iptables-blocklist-add"
    assert calls[0][1] == ["https://example.test/list.txt"]
    assert calls[0][2]["check"] is False


def test_delete_blocklist_url_uses_privileged_helper(monkeypatch):
    calls = []

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        calls.append((helper_command, helper_args, kwargs))
        return CommandResult(helper_command, 0, "Blocklist URL removed", "")

    monkeypatch.setattr(firewall.shell, "privileged", fake_privileged)

    result = firewall.delete_blocklist_url("https://example.test/list.txt")

    assert result.returncode == 0
    assert calls[0][0] == "iptables-blocklist-delete"
    assert calls[0][1] == ["https://example.test/list.txt"]
    assert calls[0][2]["check"] is False


def test_firewall_api_result_raises_clear_error_on_command_failure():
    with pytest.raises(HTTPException) as exc:
        firewall_api._result(CommandResult("iptables-deny-ip", 1, "", "permission denied"))

    assert exc.value.status_code == 400
    assert exc.value.detail == "permission denied"


def test_block_ip_ensures_rule_store_before_writing(monkeypatch, tmp_path):
    calls = []
    rules_file = tmp_path / "firewall" / "rules.json"

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        calls.append((helper_command, helper_args, kwargs))
        return CommandResult(helper_command, 0, "", "")

    monkeypatch.setattr(firewall, "RULES_FILE", rules_file)
    monkeypatch.setattr(firewall.shell, "privileged", fake_privileged)

    result = firewall.block_ip("198.51.100.10")

    assert result.returncode == 0
    assert calls[0][0] == "iptables-rules-store-ensure"
    assert any(call[0] == "iptables-run" for call in calls)
    assert rules_file.exists()


def test_blocklist_url_requires_http_url():
    with pytest.raises(ValueError, match="URL must start"):
        firewall.add_blocklist_url("file:///tmp/list.txt")

    with pytest.raises(ValueError, match="URL must start"):
        firewall.delete_blocklist_url("file:///tmp/list.txt")


def test_helper_blocklist_apply_ensures_iptables_jump():
    helper = Path(__file__).resolve().parents[3] / "installer" / "files" / "opanel-helper.sh"
    content = helper.read_text(encoding="utf-8")
    start = content.index("firewall_blocklist_apply()")
    end = content.index("firewall_blocklist_status()", start)
    block = content[start:end]

    assert "iptables -N OPANEL_BLOCKLIST" in block
    assert "iptables -C INPUT -j OPANEL_BLOCKLIST" in block
    assert "ip6tables -C INPUT -j OPANEL_BLOCKLIST" in block
    assert "--match-set \"$BLOCKLIST_IPSET_V4\" src -j DROP" in block


def test_parse_iptables_status_and_open_ports(monkeypatch):
    monkeypatch.setattr(firewall.settings, "panel_port", 2222)

    rules = firewall.parse_numbered_rules(
        "Chain OPANEL_INPUT (1 references)\n"
        "num  target     prot opt source               destination\n"
        "1    ACCEPT     6    --  0.0.0.0/0            0.0.0.0/0            tcp dpt:22 /* opanel:PanelZone */\n"
        "2    ACCEPT     6    --  0.0.0.0/0            0.0.0.0/0            tcp dpt:2222 /* opanel:PanelZone */\n"
        "3    ACCEPT     6    --  0.0.0.0/0            0.0.0.0/0            multiport dports 465,587 /* opanel:PanelZone */\n"
        "Chain OPANEL_USER (1 references)\n"
        "num  target     prot opt source               destination\n"
        "1    ACCEPT     17   --  203.0.113.10         0.0.0.0/0            udp dpt:53 /* opanel:UserZone */\n"
        "2    DROP       tcp  --  198.51.100.0/24      0.0.0.0/0            tcp dpt:443 /* opanel:UserZone */\n"
    )

    assert [rule["to"] for rule in rules] == ["22/tcp", "2222/tcp", "465,587/tcp", "53/udp", "443/tcp"]
    assert rules[0]["protected"] is True
    assert rules[3]["from"] == "203.0.113.10"

    open_ports = firewall.open_ports_from_rules(rules)

    assert [f"{item['port']}/{item['protocol']}" for item in open_ports] == [
        "22/tcp",
        "465/tcp",
        "587/tcp",
        "2222/tcp",
        "53/udp",
    ]
    assert open_ports[0]["zone"] == "PanelZone"
    assert open_ports[-1]["source"] == "203.0.113.10"


def test_allow_port_does_not_duplicate_a_default_port(monkeypatch, tmp_path):
    """22, 25, 80, 443, 465, 587 and the panel port are opened for every
    install. Adding one from the panel used to append a second rule for the
    same port, so the firewall list showed it twice with no way to tell which
    rule was the admin's."""
    monkeypatch.setattr(firewall, "RULES_FILE", tmp_path / "rules.json")
    calls = []
    monkeypatch.setattr(firewall, "_iptables", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(firewall, "_ip6tables", lambda *a, **kw: calls.append(a))

    result = firewall.allow_port(587)

    assert "already open by default" in result.stdout
    assert calls == []
    assert firewall._read_rules() == []


def test_allow_port_is_idempotent_for_a_custom_port(monkeypatch, tmp_path):
    monkeypatch.setattr(firewall, "RULES_FILE", tmp_path / "rules.json")
    monkeypatch.setattr(firewall, "_iptables", lambda *a, **kw: None)
    monkeypatch.setattr(firewall, "_ip6tables", lambda *a, **kw: None)

    first = firewall.allow_port(8443)
    second = firewall.allow_port(8443)

    assert first.stdout == "Port allowed"
    assert "already allowed" in second.stdout
    assert len(firewall._read_rules()) == 1


def test_installer_and_helper_agree_on_the_jump_order():
    """The installer carries its own copy of the firewall setup. When the order
    was fixed in the helper, that copy was missed, so every fresh install since
    shipped with OPANEL_INPUT ahead of OPANEL_USER -- which means an admin's
    "block this IP" did nothing for 22, 80, 443 or the panel port. Found by
    installing on a clean box and reading the live chain.
    """
    root = Path(__file__).resolve().parents[3] / "installer"
    helper = (root / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
    install = (root / "install.sh").read_text(encoding="utf-8")

    def order(text: str, binary: str) -> list[str]:
        """The chains in the position each is inserted at."""
        found = re.findall(rf"(?<![6a-z]){binary} -I INPUT (\d) -j (OPANEL_\w+)", text)
        return [chain for _, chain in sorted(found, key=lambda pair: pair[0])]

    # Scope to the function that owns the managed jumps; the helper inserts the
    # blocklist jump on its own elsewhere too.
    start = helper.index("iptables_insert_managed_jumps() {")
    helper_block = helper[start:helper.index(chr(10) + "}", start)]

    expected = ["OPANEL_BLOCKLIST", "OPANEL_USER", "OPANEL_INPUT"]
    for binary in ("iptables", "ip6tables"):
        assert order(helper_block, binary) == expected, f"helper {binary}"
        assert order(install, binary) == expected, f"install.sh {binary}"


def test_iptables_enable_saves_what_it_builds():
    """It built the chains in memory and never wrote them out, so a reboot
    restored whatever netfilter-persistent last had. One live box came back
    with OPANEL_INPUT and OPANEL_USER at zero references and INPUT policy
    ACCEPT -- filtering nothing. Only the blocklist jump survived, because the
    blocklist timer saves its own.
    """
    helper = (Path(__file__).resolve().parents[3] / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
    start = helper.index("  iptables-enable)")
    block = helper[start:helper.index(";;", start)]

    assert "firewall_persist_rules" in block
    assert block.index("iptables_reorder_managed_jumps") < block.index("firewall_persist_rules")


def test_the_updater_repairs_missing_jumps_not_only_a_wrong_order():
    updater = (Path(__file__).resolve().parents[3] / "installer" / "update.sh").read_text(encoding="utf-8")

    # !i || !u covers the jumps being gone; i < u covers them being swapped.
    assert "END{exit !(!i || !u || i < u)}" in updater
    assert "END{exit !(i && u && i < u)}" not in updater


# ---------------------------------------------------------------------------
# Address rules are listed on the Firewall page with who/when/why
# ---------------------------------------------------------------------------
def _no_iptables(monkeypatch, tmp_path):
    monkeypatch.setattr(firewall, "RULES_FILE", tmp_path / "rules.json")
    monkeypatch.setattr(firewall, "_ensure_rules_dir", lambda: None)
    monkeypatch.setattr(firewall, "_iptables", lambda *a, **kw: None)
    monkeypatch.setattr(firewall, "_ip6tables", lambda *a, **kw: None)


def test_a_block_keeps_its_reason_source_and_time(monkeypatch, tmp_path):
    _no_iptables(monkeypatch, tmp_path)
    firewall.block_ip("216.73.217.0/24", note="ClaudeBot subnet\n 240 reqs", source="mcp")
    firewall.allow_ip("203.0.113.5")
    blocked, allowed = firewall.list_rules()
    assert blocked["network"] == "216.73.217.0/24" and blocked["action"] == "deny"
    assert blocked["note"] == "ClaudeBot subnet 240 reqs"
    assert blocked["source"] == "mcp"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", blocked["created_at"])
    assert allowed["source"] == "panel" and "note" not in allowed


def test_a_note_cannot_smuggle_control_characters_or_run_long(monkeypatch, tmp_path):
    _no_iptables(monkeypatch, tmp_path)
    firewall.block_ip("198.51.100.7", note="x" * 300 + "\x00\x1b[31m", source="somebody")
    rule = firewall.list_rules()[0]
    assert len(rule["note"]) == firewall.RULE_NOTE_MAX
    assert rule["source"] == "panel"


def test_the_status_lists_the_panels_address_rules_by_id(monkeypatch):
    """The page could only show the raw iptables text, whose line numbers drift
    from the rule ids DELETE /rules/{id} takes once a rule has been deleted."""
    monkeypatch.setattr(firewall, "is_enabled", lambda: True)
    monkeypatch.setattr(firewall, "list_rules", lambda: [
        {"id": 1, "action": "allow", "type": "port", "port": "8080", "protocol": "tcp"},
        {"id": 4, "action": "deny", "type": "ip", "network": "20.194.96.176/32", "source": "mcp"},
    ])
    data = firewall_api._status_result(CommandResult(command="status", returncode=0, stdout="", stderr=""))
    assert data["ip_rules"] == [{"id": 4, "action": "deny", "type": "ip", "network": "20.194.96.176/32", "source": "mcp"}]


def test_the_firewall_page_lists_blocked_addresses():
    app = (Path(__file__).resolve().parents[3] / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert "firewallStatus?.ip_rules" in app
    assert "deleteFirewallRule(rule.id, rule.network)" in app
    assert "note: firewallBlockNote.trim() || null" in app
