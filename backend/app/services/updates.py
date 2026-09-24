import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from app.core.version import APP_VERSION
from app.services.shell import shell


REPO_URL = os.environ.get("opanel_REPO_URL", "https://github.com/bnixvn/opanel.git")
UPDATE_BRANCH = os.environ.get("opanel_UPDATE_BRANCH", "main")
UPDATE_STATE_FILE = Path(os.environ.get("opanel_UPDATE_STATE_FILE", "/var/lib/opanel/update-status.json"))
STATUS_CACHE_SECONDS = 300


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_update_state() -> dict:
    try:
        if UPDATE_STATE_FILE.exists():
            return json.loads(UPDATE_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {}


def _write_update_state(state: dict) -> None:
    try:
        UPDATE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = UPDATE_STATE_FILE.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp_path.replace(UPDATE_STATE_FILE)
    except Exception:
        # Update status should never break the Updates page itself.
        return


def _latest_branch_ref_from_git() -> tuple[str, str]:
    completed = subprocess.run(
        ["git", "ls-remote", "--heads", REPO_URL, f"refs/heads/{UPDATE_BRANCH}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=12,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or f"Could not read {UPDATE_BRANCH} branch").strip())
    first_line = completed.stdout.splitlines()[0] if completed.stdout.splitlines() else ""
    commit = first_line.split(None, 1)[0] if first_line else ""
    if not commit:
        raise RuntimeError(f"No {UPDATE_BRANCH} branch found")
    return f"origin/{UPDATE_BRANCH}", commit


_VERSION_RE = re.compile(r"[0-9][0-9A-Za-z.+-]{0,31}")


def _remote_version(commit: str) -> str:
    """Read VERSION at the branch tip, or "" when it cannot be read.

    `git ls-remote` returns a commit and no file content, so there was nothing
    to compare a version against and the installed one was echoed back as the
    latest. A blobless depth-1 fetch into a throwaway bare repo costs about two
    seconds and 160K, and the answer is cached for STATUS_CACHE_SECONDS like
    the rest of the check.

    Anything unexpected yields "" rather than a guess: the caller reports "I do
    not know" instead of "you are up to date", which is the failure that hid a
    release in the first place.
    """
    if not commit:
        return ""
    workdir = tempfile.mkdtemp(prefix="opanel-version-")
    try:
        staged = [
            ["git", "init", "--quiet", "--bare", workdir],
            [
                "git", "-C", workdir, "fetch", "--quiet", "--depth=1",
                "--filter=blob:none", REPO_URL, f"refs/heads/{UPDATE_BRANCH}",
            ],
        ]
        for argv in staged:
            done = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=45)
            if done.returncode != 0:
                return ""
        shown = subprocess.run(
            ["git", "-C", workdir, "cat-file", "blob", "FETCH_HEAD:VERSION"],
            capture_output=True, text=True, check=False, timeout=45,
        )
        if shown.returncode != 0:
            return ""
        text = (shown.stdout or "").strip()
        # A VERSION file holding something other than a version -- or a stubbed
        # subprocess in a test -- must not be presented as a release number.
        return text if _VERSION_RE.fullmatch(text) else ""
    except Exception:
        return ""
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _version_tuple(text: str) -> tuple:
    """Leading integers of a dotted version, stopping at the first non-number.

    "1.10.0" sorts above "1.9.8" here, which string comparison gets backwards.
    """
    parts: list[int] = []
    for chunk in (text or "").split("."):
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def _update_available(installed_commit: str, latest_commit: str,
                      current_version: str, remote_version: str) -> "bool | None":
    """True, False, or None for "could not tell" -- never a guess.

    The commit is the exact answer, but only boxes updated by a copy of
    update.sh new enough to record it have one; update.sh applies its own edits
    one run late, so every existing installation falls back to comparing
    versions until it has updated once more.
    """
    if installed_commit and latest_commit:
        return installed_commit != latest_commit
    installed = _version_tuple(current_version)
    offered = _version_tuple(remote_version)
    if installed and offered:
        return offered > installed
    return None


def panel_release_status(force_refresh: bool = False) -> dict:
    state = _read_update_state()
    now = _utc_now()
    current_version = APP_VERSION
    latest_version = state.get("latest_version") or current_version
    latest_tag = state.get("latest_tag") or f"origin/{UPDATE_BRANCH}"
    latest_commit = state.get("latest_commit") or ""
    check_error = ""

    checked_at = float(state.get("last_checked_epoch") or 0)
    should_refresh = force_refresh or not latest_commit or (time.time() - checked_at > STATUS_CACHE_SECONDS)
    if should_refresh:
        try:
            latest_tag, latest_commit = _latest_branch_ref_from_git()
            remote_version = _remote_version(latest_commit)
            latest_version = remote_version or current_version
            state.update(
                {
                    "current_version": current_version,
                    "latest_tag": latest_tag,
                    "latest_version": latest_version,
                    "remote_version": remote_version,
                    "latest_commit": latest_commit,
                    "update_channel": "branch",
                    "update_branch": UPDATE_BRANCH,
                    "last_checked_at": now,
                    "last_checked_epoch": time.time(),
                    "check_error": "",
                }
            )
        except Exception as exc:
            check_error = str(exc)
            state.update(
                {
                    "current_version": current_version,
                    "latest_tag": latest_tag,
                    "latest_version": latest_version,
                    "update_channel": "branch",
                    "update_branch": UPDATE_BRANCH,
                    "last_checked_at": now,
                    "last_checked_epoch": time.time(),
                    "check_error": check_error,
                }
            )
        _write_update_state(state)
    else:
        state["current_version"] = current_version

    if not check_error:
        check_error = state.get("check_error") or ""

    # `remote_version` is what the check actually learned; `latest_version`
    # falls back to the installed one so the page still has something to show.
    # Only the former decides whether an update exists, or an unreadable
    # VERSION would read as "up to date".
    installed_commit = state.get("installed_commit") or ""
    remote_version = state.get("remote_version") or ""
    update_available = _update_available(
        installed_commit, latest_commit, current_version, remote_version
    )

    return {
        "current_version": current_version,
        "latest_version": latest_version,
        "latest_tag": latest_tag,
        "latest_commit": latest_commit,
        "installed_commit": installed_commit,
        "update_channel": "branch",
        "update_branch": UPDATE_BRANCH,
        "update_available": update_available,
        "last_checked_at": state.get("last_checked_at") or "",
        "last_update_started_at": state.get("last_update_started_at") or "",
        "last_update_finished_at": state.get("last_update_finished_at") or "",
        "last_update_status": state.get("last_update_status") or "",
        "last_update_ref": state.get("last_update_ref") or "",
        "check_error": check_error or state.get("check_error") or "",
        "progress_percent": state.get("progress_percent", 0),
        "progress_phase": state.get("progress_phase") or "",
        "progress_message": state.get("progress_message") or "",
        "state_file": str(UPDATE_STATE_FILE),
    }


def _read_panel_update_log(max_lines: int = 100) -> list[str]:
    """Return recent panel update log lines.

    Prefers the systemd journal for `opanel-panel-update.service`; falls back to
    the flat log file at /var/log/opanel-panel-update.log when journald is
    unavailable (e.g. containers without systemd).
    """
    lines: list[str] = []
    try:
        have_journal = subprocess.run(
            ["systemctl", "cat", "opanel-panel-update.service"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        ).returncode == 0
        journal_ok = False
        if have_journal:
            completed = subprocess.run(
                ["journalctl", "-u", "opanel-panel-update.service", "-n", str(max_lines), "--no-pager", "--output=cat"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if completed.returncode == 0 and completed.stdout.strip():
                lines = completed.stdout.splitlines()[-max_lines:]
                journal_ok = True
        if not journal_ok:
            log_path = Path("/var/log/opanel-panel-update.log")
            if log_path.exists():
                with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                    lines = handle.read().splitlines()[-max_lines:]
    except Exception:
        return []
    return lines


def cached_release_summary() -> dict:
    """Whether a panel update is waiting, from the last recorded check only.

    For the dashboard: panel_release_status() may run git ls-remote when its
    cache is stale, and opening the dashboard must not wait on the network.
    """
    state = _read_update_state()
    latest_version = state.get("latest_version") or APP_VERSION
    checked = state.get("last_checked_at") or ""
    finished = state.get("last_update_finished_at") or ""
    # A check older than the last update compares the new install against a
    # branch tip recorded before it, so its commit answer is stale; fall back
    # to the version numbers, which the update itself moved forward.
    stale = bool(checked and finished and finished > checked)
    return {
        "current_version": APP_VERSION,
        "latest_version": latest_version,
        "update_available": _update_available(
            "" if stale else state.get("installed_commit") or "",
            "" if stale else state.get("latest_commit") or "",
            APP_VERSION,
            state.get("remote_version") or "",
        ),
        "last_checked_at": checked,
    }


def status(force_refresh: bool = False):
    result = shell.privileged(
        "updates-status",
        check=False,
        fallback=["bash", "-lc", "apt list --upgradable 2>/dev/null | head -40"],
    )
    payload = result.__dict__
    payload["panel"] = panel_release_status(force_refresh=force_refresh)
    payload["panel_update_log"] = _read_panel_update_log()
    return payload


def run_os_update():
    return shell.privileged(
        "updates-os-run",
        check=False,
        fallback=[
            "bash",
            "-lc",
            "nohup bash -lc 'apt-get update && apt-get upgrade -y' >/tmp/opanel-os-update.log 2>&1 & echo OS update started in background. Log: /tmp/opanel-os-update.log",
        ],
    )


def configure_os_auto_update(enabled: bool, mode: str, auto_reboot: bool):
    if mode not in {"security", "all"}:
        raise ValueError("Unsupported OS auto-update mode")
    return shell.privileged(
        "updates-os-auto",
        helper_args=["on" if enabled else "off", mode, "on" if auto_reboot else "off"],
        check=False,
        fallback=["bash", "-lc", "echo unattended-upgrades helper is not installed"],
    )


def run_panel_update():
    state = _read_update_state()
    state.update(
        {
            "last_update_status": "checking",
            "last_update_started_at": _utc_now(),
            "last_update_message": "Starting panel update",
            "progress_percent": 0,
            "progress_phase": "starting",
            "progress_message": "Starting panel update",
        }
    )
    _write_update_state(state)
    result = shell.privileged(
        "updates-panel-run",
        check=False,
        fallback=["bash", "installer/update.sh", "--branch", "main"],
    )
    if result.returncode != 0:
        state = _read_update_state()
        state.update(
            {
                "last_update_status": "failed",
                "last_update_finished_at": _utc_now(),
                "last_update_message": (result.stderr or result.stdout or "Panel update could not be started").strip(),
                "progress_phase": "failed",
                "progress_message": "Panel update could not be started",
            }
        )
        _write_update_state(state)
    return result


def configure_panel_auto_update(enabled: bool, time_value: str):
    return shell.privileged(
        "updates-panel-auto",
        helper_args=["on" if enabled else "off", time_value],
        check=False,
        fallback=["bash", "-lc", "echo panel auto-update helper is not installed"],
    )
