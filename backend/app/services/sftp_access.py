"""An account's main SFTP login: open or shut, panel password or its own.

The login is the account's Linux user (sshd matches it on opanel-sftp and
shuts it in /home/<user>). Shut is that user's password locked, its hash
kept, so opening it again brings the same password back; nothing else of the
account - its websites' PHP, its cron - notices.

It is open only while the account is active, its SFTP is on, and the panel
knows the password it should have. That last one is false for a moment after
an administrator puts it back on the panel password: the panel keeps only a
hash of that, and learns the password itself at the account's next sign-in.
"""
from __future__ import annotations

import logging
import secrets

from app.models.entities import User
from app.services import site_users
from app.services.shell import shell

logger = logging.getLogger("opanel.sftp_access")


def is_open(user: User) -> bool:
    return bool(user.is_active and getattr(user, "sftp_enabled", True)
                and not getattr(user, "sftp_sync_pending", False))


def state(user: User) -> dict:
    return {
        "login": site_users.linux_user_for_panel_username(user.username),
        "enabled": bool(getattr(user, "sftp_enabled", True)),
        "password_mode": "own" if getattr(user, "sftp_own_password", False) else "panel",
        # Back on the panel password, shut until the account next signs in.
        "waiting_for_sign_in": bool(getattr(user, "sftp_sync_pending", False)),
        "open": is_open(user),
    }


def apply(user: User) -> None:
    """Open or shut the Linux login to match the account."""
    linux_user = site_users.linux_user_for_panel_username(user.username)
    shell.privileged("panel-user-sftp", helper_args=[linux_user, "on" if is_open(user) else "off"],
                     fallback=["true"])


def apply_quietly(user: User) -> None:
    """apply(), for a caller whose own change must not fail on it."""
    try:
        apply(user)
    except Exception:  # pragma: no cover - logged, the login is fixed on the next change
        logger.warning("Could not set the SFTP login of %s", user.username, exc_info=True)


def sync_panel_password(user: User, password: str) -> None:
    """The panel password is set: the SFTP login follows it, unless it has its own."""
    if getattr(user, "sftp_own_password", False):
        return
    user.sftp_sync_pending = False
    site_users.set_panel_user_password(user.username, password, locked=not is_open(user))


def set_own_password(user: User, password: str | None = None) -> str:
    """Give the login a password of its own; returns it (made up when not given)."""
    password = password or secrets.token_urlsafe(18)
    user.sftp_own_password = True
    user.sftp_sync_pending = False
    site_users.set_panel_user_password(user.username, password, locked=not is_open(user))
    return password


def use_panel_password(user: User, panel_password: str | None = None) -> None:
    """Back to the panel password: now, when it is at hand, else at the next sign-in."""
    was_own = bool(getattr(user, "sftp_own_password", False))
    user.sftp_own_password = False
    if panel_password:
        sync_panel_password(user, panel_password)
    elif was_own:
        user.sftp_sync_pending = True
        apply(user)


def set_enabled(user: User, enabled: bool) -> None:
    user.sftp_enabled = bool(enabled)
    apply(user)
