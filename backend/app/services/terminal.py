"""The browser terminal: a real shell, one sandbox per session.

Operator, 2026-10-17. Each session is a transient systemd unit started by the
root helper (`terminal-session`), and everything about the sandbox is decided
there, never by the panel:

- it runs as the account's own Linux user, in the account's resource-limits
  slice, so its CPU, memory and process caps hold;
- it sees its own home only (the other homes are not there), a /tmp of its own,
  its own processes only, on a read-only system, with no sudo and no setuid;
- it still reaches the network and MariaDB.

Two sessions of one account are two units with two private /tmp: nothing is
shared between them but the home.

The panel holds the terminal (systemd-run --pty): this module opens a PTY,
starts the helper on it and hands the master to the WebSocket. Closing the
master hangs the shell up, which ends the unit and every process in it.

Rules: at most MAX_SESSIONS_PER_ACCOUNT at once per account, closed after
IDLE_SECONDS without a keystroke and after MAX_SECONDS in any case, and closed
the moment the browser goes away. Only a hosting customer opens one: an
administrator or a reseller gets a shell by logging in as the customer, never
one of its own, and never root. Opening and closing are audited; what is typed
is not.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import signal
import struct
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from app.core.permissions import is_admin_role, is_reseller_role
from app.services import demo_mode, site_users
from app.services.shell import HELPER_PATH, _use_helper, shell

logger = logging.getLogger("opanel.terminal")

MAX_SESSIONS_PER_ACCOUNT = 2
IDLE_SECONDS = 30 * 60
MAX_SECONDS = 8 * 3600
UNIT_PREFIX = "opanel-term-"
DEFAULT_COLS, DEFAULT_ROWS = 80, 24

_PHP_VERSION_RE = re.compile(r"^\d+\.\d+$")


# --- who may open one ---------------------------------------------------------------

def access_problem(user) -> Optional[str]:
    """Why this account may not open a terminal, or None if it may.

    "impersonate": an administrator or a reseller, who opens a customer's shell
    by logging in as that customer. "disabled": not part of the account's
    package. "demo": the read-only demo account.
    """
    if user is None or not user.is_active:
        return "inactive"
    if demo_mode.is_demo_account(user):
        return "demo"
    if is_admin_role(user.role) or is_reseller_role(user.role):
        return "impersonate"
    if not user.terminal_enabled:
        return "disabled"
    return None


PROBLEM_TEXT = {
    "inactive": "This account is disabled.",
    "demo": "The terminal is not available in the demo.",
    "impersonate": "Administrators and resellers open a customer's terminal by logging in as the customer.",
    "disabled": "The terminal is not part of this account's package.",
}


def default_cwd(site_root: str) -> str:
    """Where a session opened from a website starts: its document root when it
    has one, else the website directory."""
    root = Path(site_root).resolve(strict=False)
    document_root = site_users.document_root(root).resolve(strict=False)
    if document_root.exists() and document_root.is_dir():
        return str(document_root)
    return str(root)


# --- the sessions this process holds ------------------------------------------------

@dataclass
class Session:
    id: str
    user_id: int
    linux_user: str
    unit: str
    target: str
    started: float = field(default_factory=time.monotonic)
    last_input: float = field(default_factory=time.monotonic)
    # Set (from any thread) to have the session close itself, with the reason.
    close_request: Optional[Callable[[str], None]] = None


_sessions: dict[str, Session] = {}
_lock = threading.Lock()


def sessions_of(user_id: int) -> list[Session]:
    with _lock:
        return [s for s in _sessions.values() if s.user_id == user_id]


def reserve(user_id: int, linux_user: str, target: str) -> Optional[Session]:
    """A slot for a new session, or None when the account already has the most
    it may. The API runs as one process, so this registry is the whole count."""
    with _lock:
        if sum(1 for s in _sessions.values() if s.user_id == user_id) >= MAX_SESSIONS_PER_ACCOUNT:
            return None
        session_id = secrets.token_hex(6)
        session = Session(id=session_id, user_id=user_id, linux_user=linux_user,
                          unit=unit_name(linux_user, session_id), target=target)
        _sessions[session_id] = session
        return session


def release(session: Session) -> None:
    with _lock:
        _sessions.pop(session.id, None)


def end_sessions(user_id: int, reason: str) -> int:
    """Close every session of an account now: its access changed, or it is
    being deleted (userdel refuses a user with processes). The units are
    stopped here, synchronously; the WebSockets notice and close."""
    ended = 0
    for session in sessions_of(user_id):
        # The socket hangs the shell up at once; the stop makes sure, and is
        # done before this returns.
        if session.close_request:
            session.close_request(reason)
        stop_unit(session.unit)
        ended += 1
    return ended


def unit_name(linux_user: str, session_id: str) -> str:
    return f"{UNIT_PREFIX}{linux_user}-{session_id}"


# --- the process --------------------------------------------------------------------

def clamp_size(cols, rows) -> tuple[int, int]:
    try:
        cols, rows = int(cols), int(rows)
    except (TypeError, ValueError):
        return DEFAULT_COLS, DEFAULT_ROWS
    return max(20, min(cols, 500)), max(5, min(rows, 200))


def set_size(fd: int, cols: int, rows: int) -> None:
    import fcntl  # Linux only, like everything that touches the PTY
    import termios

    cols, rows = clamp_size(cols, rows)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))


def available() -> bool:
    """Only on the server: the sandbox is the root helper's to build."""
    return _use_helper()


def spawn(session: Session, cwd: str, php_version: Optional[str], slice_name: str,
          cols: int, rows: int) -> tuple[subprocess.Popen, int]:
    """Start the session's shell on a new PTY; returns the process and the master.

    `setsid -c` makes the PTY the controlling terminal of the chain, so a
    window size change reaches systemd-run as SIGWINCH and the shell inside
    follows it.
    """
    php = php_version if php_version and _PHP_VERSION_RE.match(php_version) else "-"
    argv = ["setsid", "-c", "sudo", "-n", HELPER_PATH, "terminal-session",
            session.linux_user, session.unit, cwd, php, slice_name]
    master, slave = os.openpty()
    try:
        set_size(master, cols, rows)
        process = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, close_fds=True,
                                   env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                                        "LANG": "C.UTF-8", "TERM": "xterm-256color"})
    except Exception:
        os.close(master)
        raise
    finally:
        os.close(slave)
    os.set_blocking(master, False)
    return process, master


def hang_up(process: subprocess.Popen, master: int) -> None:
    """Close the PTY (the shell gets SIGHUP and the unit stops) and reap the chain."""
    try:
        os.close(master)
    except OSError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.warning("terminal process %s did not exit", process.pid)


def stop_unit(unit: str) -> None:
    """Make sure the unit is gone; hanging up normally does it already."""
    if not unit.startswith(UNIT_PREFIX):
        return
    try:
        shell.privileged("terminal-stop", helper_args=[unit], check=False)
    except Exception:  # never let cleanup raise
        logger.exception("could not stop %s", unit)


def exit_status(process: subprocess.Popen) -> Optional[int]:
    code = process.poll()
    if code is None:
        return None
    return code if code >= 0 else 128 - code
