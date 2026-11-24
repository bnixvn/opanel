"""A change to sshd_config reaches every running sshd, and a failed reload shows.

A fresh OPanel 1.49.6 install (lab box, 2026-11-23) answered half its SFTP
logins with "This account is currently not available": cloud-init's
first-boot upgrade had left an sshd systemd no longer tracked, the
installer's `systemctl enable --now ssh` started a second one on the same
socket-activated port, and `systemctl reload ssh || true` only ever reached
the second. The first kept the configuration from before OPanel's Match
block, so every login it took ran the account's nologin shell.
opanel-sshd-reload reloads the unit and sends every other sshd listener the
SIGHUP sshd reloads on; its callers check what it returns.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
INSTALLER = PROJECT_ROOT / "installer"
RELOAD = INSTALLER / "files" / "opanel-sshd-reload.sh"
HELPER = (INSTALLER / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
INSTALL = (INSTALLER / "install.sh").read_text(encoding="utf-8")
UPDATE = (INSTALLER / "update.sh").read_text(encoding="utf-8")
CTL = (INSTALLER / "files" / "opanelctl").read_text(encoding="utf-8")

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _function(text: str, name: str) -> str:
    start = text.index(f"\n{name}() {{")
    return text[start:text.index("\n}\n", start)]


def _code(text: str, name: str) -> str:
    return "\n".join(line for line in _function(text, name).splitlines() if not line.strip().startswith("#"))


def _run(tmp_path, *, active="ssh.service", main="100", listeners="100", sshd_ok=True, reload_ok=True):
    """Runs opanel-sshd-reload with systemctl, sshd, pgrep and kill stubbed."""
    calls = tmp_path / "calls"
    stubs = (
        f'CALLS="{calls.as_posix()}"\n'
        f'sshd() {{ echo "sshd $*" >>"$CALLS"; {"return 0" if sshd_ok else "return 255"}; }}\n'
        "systemctl() {\n"
        '  echo "systemctl $*" >>"$CALLS"\n'
        '  case "$1" in\n'
        f'    is-active) [[ "${{@: -1}}" == "{active}" ]] ;;\n'
        f'    reload) {"return 0" if reload_ok else "return 1"} ;;\n'
        f'    show) echo "{main}" ;;\n'
        "  esac\n"
        "}\n"
        f'pgrep() {{ echo "pgrep $*" >>"$CALLS"; for p in {listeners}; do echo "$p"; done; }}\n'
        'kill() { echo "kill $*" >>"$CALLS"; return 0; }\n'
        "export CALLS\n"
        "export -f sshd systemctl pgrep kill\n"
    )
    done = subprocess.run(["bash", "-c", stubs + f'bash "{RELOAD.as_posix()}"'], capture_output=True, text=True)
    log = calls.read_text(encoding="utf-8") if calls.exists() else ""
    return done.returncode, done.stderr, log


@needs_bash
def test_one_sshd_is_reloaded_through_its_unit(tmp_path):
    rc, err, log = _run(tmp_path)
    assert rc == 0, err
    assert "systemctl reload ssh.service" in log
    assert "kill" not in log


@needs_bash
def test_a_second_listener_systemd_lost_is_reloaded_too(tmp_path):
    # The lab box: 37709 is ssh.service, 26198 the sshd cloud-init left behind.
    rc, err, log = _run(tmp_path, main="37709", listeners="26198 37709")
    assert rc == 0, err
    assert "systemctl reload ssh.service" in log
    assert "kill -HUP 26198" in log
    assert "kill -HUP 37709" not in log
    assert re.search(r"pgrep -f \^sshd: \.\*\\\[listener\\\]", log), log


@needs_bash
def test_with_the_unit_stopped_every_listener_still_gets_it(tmp_path):
    # Between cloud-init's upgrade and the installer: ssh.service "inactive",
    # its old sshd still answering on the socket.
    rc, err, log = _run(tmp_path, active="none", main="0", listeners="26198")
    assert rc == 0, err
    assert "systemctl reload" not in log
    assert "kill -HUP 26198" in log


@needs_bash
def test_nothing_running_is_not_an_error(tmp_path):
    # Socket activation with no login yet: the first one starts sshd, which
    # reads the file as it is.
    rc, err, log = _run(tmp_path, active="none", main="0", listeners="")
    assert rc == 0, err
    assert "systemctl reload" not in log and "kill" not in log


@needs_bash
def test_a_configuration_sshd_refuses_is_not_loaded(tmp_path):
    rc, err, log = _run(tmp_path, sshd_ok=False, listeners="100 200")
    assert rc == 1
    assert "refuses" in err
    assert "systemctl reload" not in log and "kill" not in log


@needs_bash
def test_a_failed_reload_is_reported(tmp_path):
    rc, err, log = _run(tmp_path, reload_ok=False, listeners="100")
    assert rc == 1
    assert "systemctl reload ssh.service failed" in err
    # Its listener still gets the signal sshd reloads on.
    assert "kill -HUP 100" in log


def test_the_helper_reloads_every_sshd_outside_the_sandbox_and_checks_it():
    code = _code(HELPER, "reload_sshd_service")
    assert 'in_host_mount_ns "$SSHD_RELOAD"' in code
    assert "|| true" not in code
    assert 'SSHD_RELOAD="/usr/local/sbin/opanel-sshd-reload"' in HELPER
    commit = _code(HELPER, "sftp_sub_sshd_commit")
    assert commit.index("sshd -t") < commit.index("reload_sshd_service")
    # Refused or not reloaded: the old sshd_config goes back and the request fails.
    assert 'cp -p "$backup" "$cfg"' in commit and "deny" in commit
    runtime = _code(HELPER, "ensure_sftp_sub_runtime")
    assert runtime.count('sftp_sub_sshd_commit "$cfg" "$backup"') == 2
    assert "reload_sshd_service" not in runtime


def test_installer_and_updater_reload_every_sshd_and_say_when_it_failed():
    setup = _code(INSTALL, "setup_sftp_access")
    assert 'bash "${SCRIPT_DIR}/files/opanel-sshd-reload.sh"' in setup
    assert "WARNING: sshd did not reload" in setup
    assert ('install -m 0755 -o root -g root "${SCRIPT_DIR}/files/opanel-sshd-reload.sh" '
            "/usr/local/sbin/opanel-sshd-reload") in _code(INSTALL, "install_privileged_helper")
    runtime = _code(UPDATE, "install_panel_runtime")
    assert 'bash "$SOURCE_DIR/installer/files/opanel-sshd-reload.sh"' in runtime
    assert "WARNING: sshd did not reload" in runtime
    assert ('install -m 0755 -o root -g root "$SOURCE_DIR/installer/files/opanel-sshd-reload.sh" '
            "/usr/local/sbin/opanel-sshd-reload") in UPDATE
    assert "/usr/local/sbin/opanel-sshd-reload" in _code(CTL, "fix_permissions")


@pytest.mark.parametrize("name", ["install.sh", "update.sh", "opanel-helper.sh"])
def test_no_reload_of_sshd_is_ignored(name):
    text = {"install.sh": INSTALL, "update.sh": UPDATE, "opanel-helper.sh": HELPER}[name]
    assert "systemctl reload ssh 2>/dev/null || systemctl reload sshd 2>/dev/null || true" not in text
