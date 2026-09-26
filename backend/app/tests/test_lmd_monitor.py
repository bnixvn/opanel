"""One real-time malware monitor, and it is opanel's.

LMD's installer ships and enables its own maldet.service (``--monitor users``).
Beside opanel-maldet-monitor.service it ran as a second supervisor on the same
inotify log, read cursor and monitor.pid: the two split one event queue, both
got past maldet's one-monitor check by starting in the same second at boot,
and the panel neither showed nor controlled the second one. Its "users" mode
looks for ~/public_html, which opanel's layout never has, so it only ever
watched the temp dirs -- which opanel's unit now covers itself.

Most of what either of them handled was noise: OpenLiteSpeed's per-second
status report under /dev/shm, and clamd's own HTML temp dirs in /tmp.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[3] / "installer"
HELPER = (INSTALLER / "files" / "opanel-helper.sh").read_text(encoding="utf-8")


def _function(name: str) -> str:
    start = HELPER.index(f"{name}() {{")
    return HELPER[start: HELPER.index("\n}\n", start)]


def _unit() -> str:
    body = _function("write_lmd_monitor_unit")
    return body[body.index("<<'UNIT'\n") + len("<<'UNIT'\n"): body.index("\nUNIT\n")]


def _code(text: str) -> str:
    """*text* without its comment lines."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def _ignore_block() -> str:
    block = HELPER[HELPER.index("LMD_IGNORE_INOTIFY_ENTRIES=("):]
    return block[: block.index("\n)\n") + len("\n)\n")]


def _ignore_entries() -> list[str]:
    return re.findall(r"(?m)^  '([^']+)'$", _ignore_block())


def test_the_unit_watches_the_sites_and_the_temp_dirs():
    assert "ExecStart=/usr/local/sbin/maldet --monitor /home,/tmp,/var/tmp,/dev/shm" in _unit()


def test_no_command_that_lmd_does_not_have():
    # LMD 2.x has no "--monitor stop": it took "stop" for a path, logged "no
    # valid option" and did nothing -- at every start, stop and disable.
    assert "--monitor stop" not in _code(HELPER)
    assert "ExecStop=" not in _code(_unit())


def test_prestart_goes_through_the_helper_as_opanel():
    assert (
        "ExecStartPre=-/usr/bin/env SUDO_USER=opanel /usr/local/sbin/opanel-helper maldet-monitor prestart"
        in _unit()
    )
    dispatch = HELPER[HELPER.index("  maldet-monitor)"):][:600]
    assert "prestart) lmd_stop_stray_monitor ;;" in dispatch


def test_a_stale_pid_file_is_dropped_not_killed():
    # After a reboot monitor.pid can name an unrelated process that got the
    # old pid; only a pid that really is a maldet monitor gets `maldet -k`.
    body = _function("lmd_stop_stray_monitor")
    assert body.index("grep -q 'maldet --monitor'") < body.index("maldet -k")
    assert 'rm -f "$LMD_MONITOR_PID"' in body


def test_the_stock_unit_is_masked_wherever_lmd_is_set_up():
    body = _function("mask_stock_lmd_unit")
    assert "systemctl disable --now maldet.service" in body
    # disabled alone, LMD's self-update (install.sh) would re-enable it
    assert "systemctl mask maldet.service" in body
    for caller in ("install_lmd_engine", "enable_lmd_monitor", "ensure_lmd_monitor_layout"):
        assert "mask_stock_lmd_unit" in _function(caller), caller


def test_every_update_brings_existing_boxes_to_the_new_layout():
    hygiene = HELPER[HELPER.index("  log-hygiene)"):]
    assert "ensure_lmd_monitor_layout" in hygiene[: hygiene.index(";;")]
    body = _function("ensure_lmd_monitor_layout")
    assert "systemctl restart opanel-maldet-monitor.service" in body
    # log-hygiene runs under set -e: a box with nothing to restart must not
    # fail the whole subcommand.
    assert body.rstrip().endswith("return 0")


def test_enable_restarts_so_a_running_monitor_picks_up_the_change():
    body = _function("enable_lmd_monitor")
    assert "systemctl restart opanel-maldet-monitor.service" in body
    assert "enable --now" not in _code(body)


@pytest.mark.parametrize("path, ignored", [
    ("/dev/shm/ols/status/.rtreport.3.tmp", True),
    ("/dev/shm/ols/status/.rtreport", True),
    ("/tmp/html-tmp.b9f5c33e85", True),
    ("/tmp/html-tmp.b9f5c33e85/notags.html", True),
    ("/dev/shm/ols/shell.php", False),
    ("/dev/shm/x.sh", False),
    ("/tmp/html-tmp.php", False),
    ("/tmp/shell.php", False),
    ("/var/tmp/html-tmp.ab12/x.php", False),
    ("/home/u/example.com/public_html/tmp/html-tmp.ab12/x.php", False),
])
def test_only_the_known_noise_is_ignored(path, ignored):
    entries = _ignore_entries()
    assert len(entries) == 2
    # LMD joins the entries into one inotifywait --exclude ERE, searched
    # against the full path.
    combined = re.compile("(" + "|".join(entries) + ")")
    assert bool(combined.search(path)) is ignored


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_ignore_entries_are_added_once_and_report_a_change(tmp_path):
    ignore = tmp_path / "ignore_inotify"
    ignore.write_bytes(b"# site exclusions\n/srv/cache")  # no trailing newline
    script = "\n".join([
        "set -euo pipefail",
        f'LMD_DIR="{tmp_path.as_posix()}"',
        'LMD_IGNORE_INOTIFY="$LMD_DIR/ignore_inotify"',
        _ignore_block(),
        _function("lmd_ignore_inotify_add_missing") + "\n}",
        "if lmd_ignore_inotify_add_missing; then echo first=changed; else echo first=same; fi",
        "if lmd_ignore_inotify_add_missing; then echo second=changed; else echo second=same; fi",
    ])
    result = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert result.stdout.split() == ["first=changed", "second=same"], result.stderr
    lines = ignore.read_text(encoding="utf-8").splitlines()
    assert lines[:2] == ["# site exclusions", "/srv/cache"]
    assert lines[2:] == _ignore_entries()
