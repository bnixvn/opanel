"""An update leaves exactly one OpenLiteSpeed master, the one systemd runs.

A package upgrade (1.9.3 -> 1.9.4, production box, 2026-11-20) left the old
master running outside systemd: it kept the ports and its old configuration,
every restart died with "Address already in use", and the sites answered 404
once their vhosts were rewritten. ensure_single_ols_master leaves a healthy
OLS alone and restarts a stuck one cleanly.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
UPDATE = (PROJECT_ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
FUNCTION = UPDATE[UPDATE.index("ensure_single_ols_master() {"):]
FUNCTION = FUNCTION[:FUNCTION.index("\n}\n") + 3]

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _run(tmp_path, state: str, unit_pid: str, masters: str):
    calls = tmp_path / "calls"
    ctl = tmp_path / "lswsctrl"
    ctl.write_text(f'#!/usr/bin/env bash\necho "lswsctrl $*" >>"{calls.as_posix()}"\n', encoding="utf-8")
    ctl.chmod(0o755)
    script = (
        "set -euo pipefail\n"
        f'CALLS="{calls.as_posix()}"\n'
        "sleep() { :; }\n"
        f'systemctl() {{ echo "systemctl $*" >>"$CALLS"; case "$1" in is-active) echo {state};; show) echo {unit_pid};; esac; }}\n'
        f'pgrep() {{ [[ -n "{masters}" ]] && echo "{masters}" | tr " " "\\n"; }}\n'
        'pkill() { echo "pkill $*" >>"$CALLS"; }\n'
        f'OLS_CTL="{ctl.as_posix()}"\n'
        + FUNCTION + "ensure_single_ols_master\n"
    )
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    log = calls.read_text(encoding="utf-8") if calls.exists() else ""
    return done.stdout, log


def test_a_healthy_ols_is_left_alone(tmp_path):
    out, log = _run(tmp_path, "active", "100", "100")
    assert out == ""
    assert "systemctl stop" not in log and "lswsctrl stop" not in log and "pkill" not in log


@pytest.mark.parametrize("state, unit_pid, masters", [
    ("activating", "0", "12291"),        # the box: systemd looping, a stray master serving
    ("active", "100", "12291"),          # systemd runs another one than the master
    ("active", "100", "100 12291"),      # two masters
    ("failed", "0", ""),                 # nothing running at all
])
def test_a_stuck_ols_is_restarted_cleanly(tmp_path, state, unit_pid, masters):
    out, log = _run(tmp_path, state, unit_pid, masters)
    assert "systemctl stop lshttpd" in log and "lswsctrl stop" in log
    assert "systemctl start lshttpd" in log
    assert log.index("systemctl stop lshttpd") < log.index("systemctl start lshttpd")
    assert "OpenLiteSpeed restarted cleanly" in out


def test_it_runs_right_after_the_final_restart():
    tail = UPDATE[UPDATE.index("systemctl restart lshttpd.service || /usr/local/lsws/bin/lswsctrl restart"):]
    assert tail.split("\n", 2)[1] == "ensure_single_ols_master"
