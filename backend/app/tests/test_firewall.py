import pytest

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


def test_blocklist_url_requires_http_url():
    with pytest.raises(ValueError, match="URL must start"):
        firewall.add_blocklist_url("file:///tmp/list.txt")

    with pytest.raises(ValueError, match="URL must start"):
        firewall.delete_blocklist_url("file:///tmp/list.txt")


def test_parse_iptables_status_and_open_ports(monkeypatch):
    monkeypatch.setattr(firewall.settings, "panel_port", 2222)

    rules = firewall.parse_numbered_rules(
        "Chain OPANEL_INPUT (1 references)\n"
        "num  target     prot opt source               destination\n"
        "1    ACCEPT     tcp  --  0.0.0.0/0            0.0.0.0/0            tcp dpt:22 /* opanel:PanelZone */\n"
        "2    ACCEPT     tcp  --  0.0.0.0/0            0.0.0.0/0            tcp dpt:2222 /* opanel:PanelZone */\n"
        "3    ACCEPT     tcp  --  0.0.0.0/0            0.0.0.0/0            multiport dports 465,587 /* opanel:PanelZone */\n"
        "Chain OPANEL_USER (1 references)\n"
        "num  target     prot opt source               destination\n"
        "1    ACCEPT     udp  --  203.0.113.10         0.0.0.0/0            udp dpt:53 /* opanel:UserZone */\n"
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
