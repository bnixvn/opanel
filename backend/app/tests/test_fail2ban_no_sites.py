"""fail2ban starts on a server with no website yet (160.236.192.207, 2026-11-09).

The opanel-wordpress jail reads /var/log/openlitespeed/*.access.log; with no
site there is no such file, and fail2ban refuses the whole service ("Have not
found any log file for opanel-wordpress jail"), so the addon could not be
installed on a fresh server."""
import re
from pathlib import Path

HELPER = (Path(__file__).resolve().parents[3] / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")


def _function(name: str) -> str:
    start = HELPER.index(f"\n{name}() {{\n") + 1
    return HELPER[start:HELPER.index("\n}\n", start)]


def test_the_glob_always_finds_a_file():
    placeholder = re.search(r'^FAIL2BAN_LOG_PLACEHOLDER="([^"]+)"$', HELPER, re.M).group(1)
    assert placeholder.startswith("/var/log/openlitespeed/") and placeholder.endswith(".access.log")
    assert '"logpath = /var/log/openlitespeed/*.access.log",' in HELPER
    assert "ensure_fail2ban_log_placeholder" in _function("addon_fail2ban_write_jails")
    assert 'install -m 0640 -o www-data -g opanel-sites /dev/null "$FAIL2BAN_LOG_PLACEHOLDER"' in _function("ensure_fail2ban_log_placeholder")


def test_logrotate_never_moves_an_empty_file():
    stanza = HELPER[HELPER.index("/var/log/openlitespeed/*.access.log\n"):]
    stanza = stanza[:stanza.index("}")]
    assert "notifempty" in stanza and "copytruncate" in stanza


def test_the_update_restarts_a_fail2ban_that_died_of_it():
    hygiene = HELPER[HELPER.index("  log-hygiene)\n"):]
    assert "ensure_fail2ban_starts" in hygiene[:hygiene.index("\n    ;;\n")]
    starts = _function("ensure_fail2ban_starts")
    assert '[[ -f "$FAIL2BAN_JAIL_FILE" ]] || return 0' in starts
    assert "systemctl is-failed --quiet fail2ban" in starts and "systemctl restart fail2ban" in starts
