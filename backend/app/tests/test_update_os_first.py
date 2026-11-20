"""An update brings the operating system up to date, dpkg repaired, before the panel.

A provisioning script rebooted a new VM while cloud-init's first-boot
dist-upgrade was unpacking (163.61.73.54, 2026-11-20). nodejs was left
"reinst-required" with npm already deleted and ~30 packages unpacked but never
configured; the panel update that followed died at `npm: command not found`
after removing frontend/dist, and the panel served no UI.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
UPDATE = (PROJECT_ROOT / "installer" / "update.sh").read_text(encoding="utf-8")
FUNCTIONS = UPDATE[UPDATE.index("# --- Operating system before the panel"):UPDATE.index("harden_existing_panel_users() {")]
CALL_SITE = UPDATE[UPDATE.index('log "Updating the operating system"'):UPDATE.index("# --- Sync code into APP_DIR")]

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

# Stubs keep their state in files under $T:
#   audit       what `dpkg --audit` prints (empty = sound)
#   broken      `dpkg-query` lines, "iHR|nodejs" for a reinstall-required one
#   fixable     present: `dpkg --configure -a` / reinstall clear audit + broken
#   upgrade_rc  exit code of `apt-get upgrade`
#   active      units running now; `stop_on_upgrade` lists units the upgrade stops
STUBS = r'''
CALLS="$T/calls"
call() { echo "$*" >>"$CALLS"; }
sleep() { :; }
fuser() { return 1; }
getent() { return 1; }
install() { if [[ "$1" == "-m" ]]; then : >"$3"; else call "install $*"; fi; }
dpkg() {
  call "dpkg $*"
  case "$*" in
    --audit) cat "$T/audit" 2>/dev/null ;;
    *--configure\ -a) [[ -e "$T/fixable" ]] && : >"$T/audit" ;;
  esac
  return 0
}
dpkg-query() { cat "$T/broken" 2>/dev/null; }
apt-get() {
  call "apt-get $*"
  case "$*" in
    *--reinstall*) [[ -e "$T/fixable" ]] && : >"$T/broken" ;;
    *upgrade*)
      for u in $(cat "$T/stop_on_upgrade" 2>/dev/null); do
        grep -vx "$u" "$T/active" >"$T/active.new"; mv "$T/active.new" "$T/active"
      done
      return "$(cat "$T/upgrade_rc" 2>/dev/null || echo 0)" ;;
  esac
  return 0
}
systemctl() {
  case "$1" in
    is-active) grep -qx "$3" "$T/active" 2>/dev/null ;;
    start) call "systemctl start $2"; echo "$2" >>"$T/active" ;;
  esac
}
'''


def _run(tmp_path, *, audit="", broken="", fixable=False, upgrade_rc=0,
         active=(), stop_on_upgrade=()):
    (tmp_path / "audit").write_text(audit, encoding="utf-8")
    (tmp_path / "broken").write_text(broken, encoding="utf-8")
    if fixable:
        (tmp_path / "fixable").write_text("", encoding="utf-8")
    (tmp_path / "upgrade_rc").write_text(str(upgrade_rc), encoding="utf-8")
    (tmp_path / "active").write_text("".join(f"{u}\n" for u in active), encoding="utf-8")
    (tmp_path / "stop_on_upgrade").write_text(" ".join(stop_on_upgrade), encoding="utf-8")
    script = (
        "set -euo pipefail\n"
        f'T="{tmp_path.as_posix()}"\n'
        + STUBS
        + FUNCTIONS
        + 'OS_UPDATE_LOG="$T/os.log"\n'
        + "rc=0; update_operating_system || rc=$?; echo \"rc=$rc\"\n"
    )
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=True)
    calls = (tmp_path / "calls").read_text(encoding="utf-8") if (tmp_path / "calls").exists() else ""
    rc = int(done.stdout.strip().rsplit("rc=", 1)[1])
    return rc, calls, done.stdout


def test_a_sound_system_is_just_upgraded(tmp_path):
    rc, calls, out = _run(tmp_path)
    assert rc == 0
    assert "--configure" not in calls and "--reinstall" not in calls and " -f install" not in calls
    assert calls.index("apt-get update") < calls.index("upgrade")
    assert "apt-get -y --with-new-pkgs upgrade" in calls
    assert "dist-upgrade" not in calls and "autoremove" not in calls
    assert out.strip() == "rc=0", "the detail belongs in the log, not on screen"


def test_an_interrupted_dpkg_is_repaired_before_the_upgrade(tmp_path):
    rc, calls, _ = _run(
        tmp_path,
        audit="The following packages are in a mess ...\n nodejs\n",
        broken="iHR|nodejs\nii |git\n",
        fixable=True,
    )
    assert rc == 0
    assert "dpkg --force-confdef --force-confold --configure -a" in calls
    assert "apt-get -y install --reinstall nodejs\n" in calls
    assert calls.index("--configure -a") < calls.index("--reinstall") < calls.index("upgrade")


def test_a_dpkg_that_stays_broken_stops_before_the_upgrade(tmp_path):
    rc, calls, _ = _run(tmp_path, audit="half-configured: libc6\n")
    assert rc == 1
    assert "--configure -a" in calls
    assert "upgrade" not in calls


def test_a_failed_upgrade_on_a_sound_dpkg_lets_the_panel_update_go_on(tmp_path):
    rc, _, _ = _run(tmp_path, upgrade_rc=100)
    assert rc == 2


def test_a_daemon_the_upgrade_stopped_is_started_again(tmp_path):
    rc, calls, _ = _run(
        tmp_path,
        active=("lshttpd.service", "mariadb.service"),
        stop_on_upgrade=("lshttpd.service",),
    )
    assert rc == 0
    assert "systemctl start lshttpd.service" in calls
    assert "systemctl start mariadb.service" not in calls
    assert "redis-server.service" not in calls, "a daemon that was not running stays off"


def test_it_runs_after_the_handover_and_before_the_panel_changes():
    handover = UPDATE.index('exec /bin/bash "$stage2_copy"')
    os_step = UPDATE.index('log "Updating the operating system"')
    sync = UPDATE.index('log "Syncing source to $APP_DIR"')
    assert handover < os_step < sync


def _call_site(tmp_path, os_rc: int):
    script = (
        "set -euo pipefail\n"
        'log() { echo "==> $1"; }\n'
        'fail() { echo "ERROR: $1" >&2; exit 1; }\n'
        "update_progress() { :; }\n"
        f'OS_UPDATE_LOG="{(tmp_path / "os.log").as_posix()}"\n'
        'echo "dpkg: error processing package nodejs" >"$OS_UPDATE_LOG"\n'
        f"update_operating_system() {{ return {os_rc}; }}\n"
        + CALL_SITE
        + 'echo "panel update continues"\n'
    )
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_broken_packages_stop_the_update_before_the_panel(tmp_path):
    done = _call_site(tmp_path, 1)
    assert done.returncode == 1
    assert "panel update continues" not in done.stdout
    assert "the panel was not updated" in done.stderr
    assert "dpkg: error processing package nodejs" in done.stderr


@pytest.mark.parametrize("os_rc, error", [(0, False), (2, True)])
def test_the_panel_update_goes_on_otherwise(tmp_path, os_rc, error):
    done = _call_site(tmp_path, os_rc)
    assert done.returncode == 0
    assert done.stdout.splitlines() == ["==> Updating the operating system", "panel update continues"]
    assert ("ERROR:" in done.stderr) is error


def test_node_without_npm_gets_nodejs_reinstalled():
    frontend = UPDATE[UPDATE.index("# --- Frontend ----"):]
    check = frontend[:frontend.index('echo "Node version:')]
    assert "! command -v npm" in check
    assert "install -y --reinstall nodejs" in check
