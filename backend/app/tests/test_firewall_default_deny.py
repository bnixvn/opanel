"""A firewall that comes back after a reboot, and default deny on new installs.

Found on a fresh VPS (2026-09-30): the saved rules match on the blocklist
ipsets, which are not saved, so at boot iptables-restore refused the whole
file and the box ran with no OPanel firewall until the daily blocklist run.
And even when enabled, the firewall only blocked addresses: every port that
anything listened on was reachable. New installs now drop what the panel's
chains do not accept (operator, 2026-09-30); existing servers are unchanged.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HELPER = (ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
INSTALL = (ROOT / "installer" / "install.sh").read_text(encoding="utf-8")


def _function(source: str, name: str) -> str:
    return source.split(f"\n{name}() {{", 1)[1].split("\n}\n", 1)[0]


def _command(name: str) -> str:
    return HELPER.split(f"\n  {name})\n", 1)[1].split("\n    ;;\n", 1)[0]


def test_the_saved_rules_can_load_at_boot():
    body = _function(HELPER, "ensure_firewall_restorable")
    assert "netfilter-persistent.service.d" in body
    assert "ExecStartPre=-${ipset_bin} create ${BLOCKLIST_IPSET_V4} hash:net family inet " in body
    assert "ExecStartPre=-${ipset_bin} create ${BLOCKLIST_IPSET_V6} hash:net family inet6 " in body
    assert "ensure_firewall_restorable" in _command("log-hygiene")
    # The sets are filled again soon after a boot, not at the next 01:00.
    timer = HELPER.split("[Timer]\nOnCalendar=*-*-* ${time_value}:00", 1)[1].split("[Install]", 1)[0]
    assert "OnBootSec=1min" in timer


def test_default_deny_is_last_accepts_what_keeps_the_box_reachable_then_drops():
    body = _function(HELPER, "firewall_apply_default_deny")
    assert '[[ -f "$FIREWALL_DENY_MARKER" ]] || return 0' in body
    # Old jumps go before the marker is read: turning it off removes it.
    assert body.index('-D INPUT -j OPANEL_DENY') < body.index("FIREWALL_DENY_MARKER")
    order = [body.index(marker) for marker in (
        '"opanel:established"', '"opanel:loopback"', '"opanel:ssh"', '"opanel:http3"',
        '"opanel:icmp"', '"opanel:default-deny" -j DROP', '"$binary" -A INPUT -j OPANEL_DENY')]
    assert order == sorted(order), "the DROP and the jump must come after every ACCEPT"
    assert "for port in $(firewall_ssh_ports); do" in body
    assert "--dport 68" in body and "--dport 546" in body  # DHCP, both families
    # An ACCEPT that fails stops the function before the DROP.
    rule = _function(HELPER, "_deny_rule")
    assert '|| "$binary" -A OPANEL_DENY "$@"' in rule and "|| true" not in rule


def test_ssh_ports_come_from_sshd_itself():
    body = _function(HELPER, "firewall_ssh_ports")
    assert "echo 22" in body and '-T 2>/dev/null | awk \'$1 == "port"' in body
    assert "ss -Hltnp" in body and '"sshd"' in body


def test_enable_builds_it_and_disable_takes_it_away_first():
    enable = _command("iptables-enable")
    assert enable.index("firewall_apply_default_deny") < enable.index("firewall_persist_rules")
    disable = _command("iptables-disable")
    assert disable.index("iptables -D INPUT -j OPANEL_DENY") < disable.index("iptables -D INPUT -j OPANEL_BLOCKLIST")
    assert "ip6tables -D INPUT -j OPANEL_DENY" in disable
    assert "firewall_apply_default_deny" in _function(HELPER, "iptables_refresh_standard_ports")


def test_it_can_be_switched_by_hand():
    command = _command("firewall-default-deny")
    assert 'touch "$FIREWALL_DENY_MARKER"' in command and 'rm -f "$FIREWALL_DENY_MARKER"' in command
    # Turned on while the firewall is off, it waits for the firewall.
    assert "iptables -C INPUT -j OPANEL_INPUT" in command


def test_new_installs_start_with_it_existing_servers_do_not():
    firewall = _function(INSTALL, "setup_firewall")
    assert '"${FIREWALL_DEFAULT_DENY:-yes}" != "no"' in firewall
    assert "touch /var/lib/opanel/firewall-default-deny" in firewall
    assert "opanel-helper iptables-enable" in firewall
    update = (ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
    assert "firewall-default-deny" not in update


def test_the_firewall_page_says_which_mode_it_is_in(monkeypatch, tmp_path):
    from app.services import firewall

    monkeypatch.setattr(firewall, "DEFAULT_DENY_MARKER", tmp_path / "firewall-default-deny")
    assert firewall.default_deny_enabled() is False
    (tmp_path / "firewall-default-deny").write_text("")
    assert firewall.default_deny_enabled() is True
    api = (ROOT / "backend" / "app" / "api" / "firewall.py").read_text(encoding="utf-8")
    assert 'data["default_deny"] = firewall.default_deny_enabled()' in api
    app = (ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    assert "firewallStatus.default_deny" in app
