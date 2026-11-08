"""The PHP functions an account's PHP may not call (operator, 2026-11-08).

Every account's PHP disables DEFAULT_DISABLED unless the account has a list of
its own: a customer who needs one of them takes it off on a website's PHP tab
and saves. Per account, not per site: LiteSpeed runs one lsphp per Linux user
for all of that user's sites, and lsphp reads disable_functions only from ini
files when it starts - a vhost's phpIniOverride is ignored for it (tested on
.41). So the helper writes it into the account's root-owned PHP ini directory
(opanel-helper.sh, ensure_php_disable_ini), where the account's code cannot
change it, and keeps the account's own list in a root-owned file.

User.php_disable_functions mirrors it for the panel: None is the default list,
"" is nothing disabled. The helper holds the same default list.
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy.orm import Session

from app.models.entities import User, Website
from app.services import site_users
from app.services.shell import shell

DEFAULT_DISABLED = (
    "exec", "system", "passthru", "shell_exec", "proc_close", "proc_open", "dl", "show_source",
    "posix_kill", "posix_mkfifo", "posix_getpwuid", "posix_setpgid", "posix_setsid", "posix_setuid",
    "posix_setgid", "posix_seteuid", "posix_setegid", "posix_uname",
)
NAME_RE = re.compile(r"[a-z_][a-z0-9_]{0,63}")
MAX_NAMES = 200


def normalize(text: Optional[str]) -> Optional[str]:
    """A comma list of function names, in the order given, each once; None
    stays None (the default list)."""
    if text is None:
        return None
    names: list[str] = []
    for raw in re.split(r"[\s,]+", text.strip().lower()):
        if not raw:
            continue
        if not NAME_RE.fullmatch(raw):
            raise ValueError(f"Not a PHP function name: {raw}")
        if raw not in names:
            names.append(raw)
    if len(names) > MAX_NAMES:
        raise ValueError(f"At most {MAX_NAMES} functions")
    return ",".join(names)


def linux_users(db: Session, user: User) -> list[str]:
    """The Linux users this account's PHP runs as: its sites' own, and the one
    its username maps to (a site made later runs as that one)."""
    names = {site_users.linux_user_for_panel_username(user.username)}
    for website in db.query(Website).filter(Website.owner_id == user.id).all():
        if website.linux_user and website.linux_user != "www-data":
            names.add(website.linux_user)
    return sorted(names)


def apply(db: Session, user: User, functions: Optional[str]) -> None:
    """Write the account's list into its PHP and onto the row. None goes back
    to the default list. The row changes only once PHP has."""
    value = normalize(functions)
    helper_value = "default" if value is None else (value or "none")
    for linux_user in linux_users(db, user):
        shell.privileged(
            "user-php-disable",
            helper_args=[site_users.validate_linux_user(linux_user), helper_value],
            fallback=["true"],
        )
    user.php_disable_functions = value
