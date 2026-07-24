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
