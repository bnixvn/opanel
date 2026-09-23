"""Addons, and the boundary that keeps installing one from meaning root.

Installing an addon runs apt as root. The whole design rests on the panel being
able to *name* an addon and nothing else: the registry is the allowlist, the
helper keeps its own copy, and no value the admin types is ever interpolated
into a command line. These tests pin that, the Fail2ban addon's own validation,
and the one thing that would silently make it useless -- a ban rule sitting
below the panel's blanket port allowances.
"""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

from app.api import addons as addons_api
from app.services import addons

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER = (PROJECT_ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
APP_JSX = (PROJECT_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")


class _Result:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(addons, "STATE_FILE", tmp_path / "addons.json")
    yield


@pytest.fixture
def calls(monkeypatch):
    """Capture every privileged call instead of running one."""
    recorded: list[dict] = []

    def fake(command, helper_args=None, check=True, input=None, sensitive=False, fallback=None):
        recorded.append({
            "command": command, "args": list(helper_args or []),
            "input": input, "sensitive": sensitive,
        })
        if command == "addon-status":
            return _Result("installed=1 running=1 enabled=1 version=1.0.2")
        if command == "addon-fail2ban-banned":
            return _Result("sshd: 203.0.113.9 203.0.113.10\nopanel-panel: 198.51.100.4\n")
        return _Result("ok")

    monkeypatch.setattr(addons.shell, "privileged", fake)
    return recorded


# --------------------------------------------------------------------------
# the registry is the allowlist
# --------------------------------------------------------------------------
def test_only_a_registered_addon_is_known():
    assert addons.is_known("fail2ban")
    for candidate in ("nginx", "fail2ban-extra", "unknown"):
        assert not addons.is_known(candidate)


def test_an_id_that_could_reach_a_shell_is_not_known():
    for candidate in (
        "fail2ban; rm -rf /", "fail2ban && id", "../../etc/passwd",
        "fail2ban\nmalware", "FAIL2BAN", "", "-", "a" * 64,
    ):
        assert not addons.is_known(candidate), candidate


def test_every_operation_refuses_an_unregistered_id(calls):
    for operation in (addons.status, addons.install, addons.uninstall):
        with pytest.raises(ValueError, match="Unknown addon"):
            operation("nginx")
    with pytest.raises(ValueError, match="Unknown addon"):
        addons.set_running("nginx", True)
    assert calls == [], "nothing may reach the helper for an unknown id"


def test_the_id_is_all_that_crosses_the_boundary(calls):
    addons.status("fail2ban")
    assert calls[0]["command"] == "addon-status"
    assert calls[0]["args"] == ["fail2ban"], (
        "the helper receives a key and never a command fragment"
    )


# --------------------------------------------------------------------------
# the helper keeps its own copy of the allowlist
# --------------------------------------------------------------------------
def test_the_helper_validates_the_id_itself():
    assert "require_addon_id()" in HELPER
    assert 'ADDON_IDS=("fail2ban")' in HELPER, (
        "the helper must not take the panel's word for which addons exist"
    )


def test_the_two_allowlists_agree():
    """Two lists that must match is the cost of this design; a drift would
    either break an addon or leave a stale one installable."""
    match = re.search(r"ADDON_IDS=\(([^)]*)\)", HELPER)
    assert match, "helper allowlist not found"
    helper_ids = set(re.findall(r'"([^"]+)"', match.group(1)))
    assert helper_ids == set(addons.ADDONS), (
        f"helper has {helper_ids}, panel registry has {set(addons.ADDONS)}"
    )


def test_every_addon_case_validates_before_dispatching():
    for case in ("addon-status", "addon-install", "addon-uninstall",
                 "addon-enable", "addon-disable"):
        index = HELPER.index(f"  {case})")
        body = HELPER[index:index + 400]
        assert "require_addon_id" in body, f"{case} dispatches without validating"


def test_the_addon_cases_read_the_argument_the_dispatcher_leaves_them():
    """The helper does cmd="${1:-}" then shift, so a subcommand's first argument
    is $1. Written as $2 these cases validated an empty string and rejected
    every id including the real one -- the whole feature was dead, and a test
    that only looked for the presence of require_addon_id passed anyway."""
    assert 'cmd="${1:-}"' in HELPER and "\nshift || true" in HELPER, (
        "the convention this test depends on has moved"
    )
    block = HELPER[HELPER.index("  addon-status)"):HELPER.index("  clamav-install)")]
    offenders = re.findall(r'\$\{?2\b[^}]*\}?', block)
    assert not offenders, f"addon cases reading the wrong positional: {offenders}"
    assert 'require_addon_id "${1:-}"' in block


# --------------------------------------------------------------------------
# install / uninstall bookkeeping
# --------------------------------------------------------------------------
def test_installing_something_already_installed_is_refused(calls):
    with pytest.raises(ValueError, match="already installed"):
        addons.install("fail2ban")


def test_a_second_install_cannot_land_on_a_running_one(monkeypatch, calls):
    monkeypatch.setattr(addons, "_background", lambda *a, **k: None)
    monkeypatch.setattr(addons.shell, "privileged", lambda *a, **k: _Result("installed=0 running=0"))
    addons.install("fail2ban")
    with pytest.raises(ValueError, match="already"):
        addons.install("fail2ban")


def test_uninstalling_something_absent_is_refused(monkeypatch, calls):
    monkeypatch.setattr(addons.shell, "privileged", lambda *a, **k: _Result("installed=0 running=0"))
    with pytest.raises(ValueError, match="not installed"):
        addons.uninstall("fail2ban")


def test_status_reports_what_the_box_says_not_what_we_hoped(calls):
    result = addons.status("fail2ban")
    assert result["installed"] is True
    assert result["running"] is True
    assert result["version"] == "1.0.2"


def test_a_helper_failure_becomes_a_detail_not_an_exception(monkeypatch):
    monkeypatch.setattr(
        addons.shell, "privileged",
        lambda *a, **k: _Result(stderr="helper missing", returncode=1),
    )
    result = addons.status("fail2ban")
    assert result["installed"] is False
    assert "helper missing" in result["detail"], (
        "the Addons page must still render when the helper cannot answer"
    )


# --------------------------------------------------------------------------
# Fail2ban settings
# --------------------------------------------------------------------------
def test_settings_travel_on_stdin_never_as_arguments(calls):
    addons.fail2ban_save_settings({"maxretry": 7})
    call = next(c for c in calls if c["command"] == "addon-fail2ban-configure")
    assert call["args"] == [], "no setting may appear on the command line"
    assert json.loads(call["input"])["maxretry"] == 7


def test_out_of_range_values_are_refused(calls):
    for payload, message in (
        ({"maxretry": 0}, "Max retries"),
        ({"maxretry": 101}, "Max retries"),
        ({"findtime": 1}, "Find time"),
        ({"bantime": 5}, "Ban time"),
    ):
        with pytest.raises(ValueError, match=message):
            addons.fail2ban_save_settings(payload)


def test_a_permanent_ban_is_allowed(calls):
    result = addons.fail2ban_save_settings({"bantime": -1})
    assert result["bantime"] == -1


def test_never_ban_accepts_addresses_and_refuses_anything_else(calls):
    saved = addons.fail2ban_save_settings({"ignoreip": "203.0.113.7, 198.51.100.0/24 ::1"})
    assert saved["ignoreip"] == "203.0.113.7 198.51.100.0/24 ::1"
    for bad in ("203.0.113.7; rm -rf /", "$(id)", "a.b.c.d`id`", "10.0.0.1 || id"):
        with pytest.raises(ValueError, match="Not an address"):
            addons.fail2ban_save_settings({"ignoreip": bad})


def test_a_newline_in_never_ban_becomes_two_entries_not_a_config_line(calls):
    """The value is written into a file read by root, so the thing that matters
    is not that a newline is rejected but that one cannot survive into it.
    Splitting on any whitespace means it is read as a list, and the entries are
    re-joined with single spaces."""
    saved = addons.fail2ban_save_settings({"ignoreip": "10.0.0.1\n10.0.0.2"})
    assert saved["ignoreip"] == "10.0.0.1 10.0.0.2"
    assert "\n" not in saved["ignoreip"]

    with pytest.raises(ValueError, match="Not an address"):
        addons.fail2ban_save_settings({"ignoreip": "10.0.0.1\nbantime = -1"})


def test_never_ban_is_bounded(calls):
    with pytest.raises(ValueError, match="At most 64"):
        addons.fail2ban_save_settings({"ignoreip": " ".join(["10.0.0.1"] * 65)})


def test_turning_off_both_jails_is_refused(calls):
    with pytest.raises(ValueError, match="at least one jail"):
        addons.fail2ban_save_settings({"jail_sshd": False, "jail_panel": False})


def test_unban_validates_the_address(calls):
    addons.fail2ban_unban("203.0.113.9")
    call = next(c for c in calls if c["command"] == "addon-fail2ban-unban")
    assert call["args"] == ["203.0.113.9"]
    for bad in ("203.0.113.9; reboot", "$(id)", "", "x" * 60):
        with pytest.raises(ValueError, match="Not an address"):
            addons.fail2ban_unban(bad)


def test_banned_addresses_are_parsed_per_jail(calls):
    banned = addons.fail2ban_banned()
    assert {"jail": "sshd", "address": "203.0.113.9"} in banned
    assert {"jail": "opanel-panel", "address": "198.51.100.4"} in banned
    assert len(banned) == 3


# --------------------------------------------------------------------------
# a ban that does nothing is worse than no ban
# --------------------------------------------------------------------------
def test_the_firewall_rebuild_reasserts_addon_precedence():
    """OPANEL_INPUT accepts 22/80/443/panel from any source, and an ACCEPT in a
    user chain ends INPUT traversal. iptables_reorder_managed_jumps re-inserts
    the panel's jumps at 1/2/3 on every firewall change, which pushes a f2b jump
    below that blanket allow -- fail2ban then keeps listing bans that block
    nothing."""
    assert "iptables_restore_addon_precedence()" in HELPER
    index = HELPER.index("  iptables-enable)")
    body = HELPER[index:index + 1200]
    assert "iptables_restore_addon_precedence" in body, (
        "without this the next firewall change silently disables every ban"
    )
    assert body.index("iptables_reorder_managed_jumps") < body.index(
        "iptables_restore_addon_precedence"
    ), "it has to run after the reorder that caused the problem"


def test_bans_are_placed_above_the_default_allowances_but_below_admin_rules():
    index = HELPER.index("iptables_restore_addon_precedence()")
    body = HELPER[index:index + 2200]
    assert 'awk \'$2 == "OPANEL_INPUT" { print $1; exit }\'' in body, (
        "the insert position is taken from where the default-allow jump sits, "
        "so bans land directly above it"
    )
    # Only f2b chains are moved. Re-inserting OPANEL_USER or OPANEL_BLOCKLIST
    # here would put an automatic ban ahead of an admin's explicit rule.
    moved = re.findall(r'-[DI] INPUT (?:"\$target" )?-j "?\$?\{?(\w+)', body)
    assert moved, "no insert/delete found -- the anchor probably moved"
    assert all(name == "jump" for name in moved), (
        f"this function may only reposition the collected f2b jumps, saw {set(moved)}"
    )
    assert "f2b-" in body, "the collection filter must select only f2b chains"


def test_a_ban_covers_every_port():
    """A jail that banned only the port it caught still let the address reach
    every other service."""
    assert "banaction = iptables-allports" in HELPER


# --------------------------------------------------------------------------
# where the jails read from
# --------------------------------------------------------------------------
def test_the_jails_read_the_journal_so_no_log_file_has_to_exist():
    assert "backend = systemd" in HELPER
    assert "journalmatch = _SYSTEMD_UNIT=opanel-api.service" in HELPER
    assert "python3-systemd" in HELPER, (
        "the journal backend reads nothing without it, and both jails would sit "
        "enabled and blind"
    )


def test_loopback_is_always_exempt():
    index = HELPER.index('ignore = ["127.0.0.1/8", "::1"]')
    assert index > 0, "an admin operating from the box must not be bannable"


# --------------------------------------------------------------------------
# the panel's own failed-login line
# --------------------------------------------------------------------------
def test_a_failed_login_is_logged_where_the_jail_can_see_it():
    from app.api import auth as auth_api

    source = inspect.getsource(auth_api.login)
    assert source.count("_log_auth_failure(") == 2, (
        "both a wrong password and a wrong second factor are worth banning on"
    )


def test_a_username_cannot_forge_a_log_entry():
    """The name is attacker-supplied and lands on the line fail2ban parses to
    decide who gets blocked."""
    from app.api.auth import _safe_log_name

    assert _safe_log_name("admin") == "admin"
    assert "\n" not in _safe_log_name("admin\nopanel-auth: authentication failure")
    assert "'" not in _safe_log_name("a'b")
    assert " " not in _safe_log_name("x from 203.0.113.1"), (
        "a spelled-out 'from <address>' must not survive to offer the filter a "
        "second candidate"
    )
    assert len(_safe_log_name("z" * 400)) <= 64
    assert _safe_log_name("") == "_unknown"
    assert _safe_log_name("!#$%^&*()") == "_unknown"
    # `@` stays: accounts are often email addresses, and it cannot break out of
    # the quoted name the filter expects.
    assert _safe_log_name("who@example.test") == "who@example.test"
    # Whatever survives, these never do.
    for hostile in ("a\nb", "a\rb", "a'b", 'a"b', "a b", "a;b", "a$b", "a`b"):
        cleaned = _safe_log_name(hostile)
        assert not any(char in cleaned for char in "\n\r'\" ;$`"), cleaned


def test_the_filter_anchors_the_address_at_the_end():
    index = HELPER.index("failregex = ")
    line = HELPER[index:HELPER.index("\n", index)]
    assert "<HOST>" in line and line.rstrip().endswith("$"), (
        "an unanchored address could match something earlier in the line"
    )


# --------------------------------------------------------------------------
# admin only, all of it
# --------------------------------------------------------------------------
def _role_in(func) -> str:
    match = re.search(r"ensure_role\([^,]+,\s*Role\.(\w+)\)", inspect.getsource(func))
    return match.group(1) if match else ""


def test_every_addon_endpoint_requires_admin():
    endpoints = [
        addons_api.list_addons, addons_api.get_addon, addons_api.install_addon,
        addons_api.uninstall_addon, addons_api.toggle_addon_service,
        addons_api.get_fail2ban_settings, addons_api.save_fail2ban_settings,
        addons_api.get_fail2ban_banned, addons_api.post_fail2ban_unban,
        addons_api.get_fail2ban_log,
    ]
    for func in endpoints:
        source = inspect.getsource(func)
        assert "_admin(current_user)" in source, f"{func.__name__} is not gated"
    # _admin itself is what "admin" means here.
    assert _role_in(addons_api._admin) == "admin"


def test_the_page_is_hidden_and_not_reachable_by_url():
    index = APP_JSX.index("['addons', 'Addons', PackageOpen]")
    assert "isAdmin ?" in APP_JSX[max(0, index - 120):index]
    line_start = APP_JSX.index("if (page === 'addons')")
    line = APP_JSX[line_start:APP_JSX.index("\n", line_start)]
    assert "isAdmin" in line, "a deep link would paint a page whose requests all 403"


# --------------------------------------------------------------------------
# catalog shape the page depends on
# --------------------------------------------------------------------------
def test_the_catalog_carries_what_the_page_renders(calls):
    entries = addons.catalog()
    assert entries, "a release with no addons would show an empty page"
    for entry in entries:
        for key in ("id", "name", "summary", "description", "category", "version",
                    "installed", "running", "enabled", "busy", "features", "notes"):
            assert key in entry, f"{entry.get('id')} is missing {key}"


def test_the_registry_is_handed_out_as_a_copy():
    """A caller mutating what it was given must not edit the allowlist."""
    snapshot = addons.registry()
    snapshot["fail2ban"]["name"] = "changed"
    snapshot["nginx"] = {"id": "nginx"}
    assert addons.ADDONS["fail2ban"]["name"] == "Fail2ban"
    assert not addons.is_known("nginx")
