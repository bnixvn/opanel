"""The updater must apply its own changes in the run that brings them in.

bash reads a script as it runs, so update.sh re-execs a copy of itself from
/tmp - a copy of the *previous* release. Without a handover, anything an update
changed about updating itself landed one release late, and a repair shipped in
update.sh needed a second update before it held. BPanel's updater has handed
over since 2026-08; OPanel's does the same from 1.30.0.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
UPDATE_SCRIPT = PROJECT_ROOT / "installer" / "update.sh"


def _script() -> str:
    return UPDATE_SCRIPT.read_text(encoding="utf-8")


def _handover_block() -> str:
    script = _script()
    start = script.index("# bash reads a script as it runs")
    return script[start : script.index("# --- Sync code into APP_DIR")]


def test_the_updater_hands_over_to_the_one_from_the_new_release():
    block = _handover_block()
    assert 'exec /bin/bash "$stage2_copy"' in block
    assert 'cp "$SOURCE_DIR/installer/update.sh" "$stage2_copy"' in block
    # Only when it actually differs, so an unchanged updater keeps going.
    assert 'cmp -s "$SOURCE_DIR/installer/update.sh"' in block


def test_the_handover_comes_after_the_downgrade_refusal():
    """The updater the server already trusts decides whether the incoming
    release may be installed at all, before any of its code runs as root."""
    script = _script()
    assert script.index("Refusing to downgrade opanel") < script.index('exec /bin/bash "$stage2_copy"')


def test_the_handover_cannot_loop():
    block = _handover_block()
    assert '[[ -z "${opanel_UPDATE_STAGE2:-}"' in block
    assert "opanel_UPDATE_STAGE2=1" in block


def test_the_second_stage_does_not_fetch_the_release_again():
    block = _handover_block()
    assert "SKIP_PULL=true" in block
    assert 'SOURCE_DIR="$SOURCE_DIR"' in block
    # The release name and the commit have to survive, or the Updates page
    # would report the update as coming from a directory and lose track of
    # which commit is installed.
    assert 'opanel_UPDATE_REF_OVERRIDE="${UPDATE_REF:-}"' in block
    assert 'INSTALLED_COMMIT="${INSTALLED_COMMIT:-}"' in block
    assert 'UPDATE_REF="${opanel_UPDATE_REF_OVERRIDE:-local:${SOURCE_DIR}}"' in _script()
    # --refresh-sites is a command-line option, and the command line stays behind.
    assert 'FORCE_SITE_REFRESH="$FORCE_SITE_REFRESH"' in block


def test_the_handover_leaves_nothing_behind():
    script = _script()
    block = _handover_block()
    # exec skips the EXIT trap, so the second stage inherits the cleanup.
    # Read before the assignments: they expand left to right, each seeing the
    # one before, so naming opanel_UPDATE_STABLE_COPY in the list itself
    # gave the second stage's path and the first stage's copy stayed in /tmp.
    assert 'first_stage_copy="${opanel_UPDATE_STABLE_COPY:-}"' in block
    assert 'opanel_UPDATE_PREVIOUS_COPY="$first_stage_copy"' in block
    assert block.index('first_stage_copy=') < block.index('opanel_UPDATE_STABLE_COPY="$stage2_copy"')
    assert 'RELEASE_WORK_DIR="${RELEASE_WORK_DIR:-}"' in block
    assert 'rm -f "${opanel_UPDATE_STABLE_COPY:-}" "${opanel_UPDATE_PREVIOUS_COPY:-}"' in script
    # ...and the work dir it was handed must not be wiped out on the way in.
    assert 'RELEASE_WORK_DIR=""' not in script
    # Copies orphaned by a run that never reached its EXIT trap.
    assert "-mmin +1440 -delete" in block


def test_the_second_stage_does_not_snapshot_the_database_twice():
    block = _script().split('if [[ -n "${opanel_UPDATE_STAGE2:-}" ]]; then\n', 1)[1].split("\nfi\n", 1)[0]
    stage2, first = block.split("\nelse\n", 1)
    assert "backup_db" not in stage2
    assert 'log "Backing up the panel database"\n  backup_db' in first


def test_the_update_shows_only_the_main_steps():
    """Operator, 2026-11-20: no notes, no "already / skipping" lines; the main
    steps, the result and errors. The rest goes to the detail log."""
    script = _script()
    shown = [line.strip() for line in script.splitlines()
             if line.strip().startswith(("log \"", "echo \"")) or " log \"" in line]
    for line in shown:
        lowered = line.lower()
        assert "already" not in lowered and "skipping" not in lowered, line
    steps = [line for line in shown if line.startswith('log "')]
    assert len(steps) <= 12, steps
    # chatty tools run quietly: their output is shown only when they fail
    for command in ('quiet git fetch --prune', 'quiet sudo -u opanel env HOME="$APP_DIR" .venv/bin/pip install -r',
                    'quiet sudo -u opanel env HOME="$APP_DIR" VITE_API_URL=/api npm run build',
                    'quiet sudo -u opanel env HOME="$APP_DIR" npm ci'):
        assert command in script, command
    assert 'usermod --home "$home_dir" --shell /usr/sbin/nologin --gid "$user" "$user" >/dev/null 2>&1' in script
    assert "hard refresh" not in script and 'echo "API is healthy."' not in script
    quiet = script[script.index("quiet() {"):]
    quiet = quiet[:quiet.index("\n}\n")]
    assert 'tail -n 40 "$tmp" >&2' in quiet and 'return "$rc"' in quiet


def test_an_update_installs_the_new_resource_limits_agent_itself():
    """The panel used to compare the agent copies on its minute tick, because
    a step added here ran one update late. With the handover it runs now."""
    script = _script()
    start = script.index("# --- Resource limits agent")
    block = script[start : script.index("# --- Panel serves HTTPS by default", start)]
    # Only where the addon is installed: an addon that is off has nothing on disk.
    assert "-f /etc/systemd/system/opanel-limits.service" in block
    # Through the helper, which checks the hash it carries.
    assert "opanel-helper limits-agent-install" in block
    assert '<"$LIMITS_AGENT_SOURCE"' in block
    # After the helper that carries the new hash is in place.
    assert script.index("Refreshing /usr/local/sbin/opanel-helper") < start
    # Never fatal: an update must not stop over an addon.
    assert "|| echo" in block


def test_the_panel_no_longer_reinstalls_the_agent_on_a_timer():
    source = (PROJECT_ROOT / "backend" / "app" / "services" / "resource_limits.py").read_text(encoding="utf-8")
    sync = source.split("def sync(db: Session)", 1)[1].split("\ndef ", 1)[0]
    assert "install_agent" not in sync
