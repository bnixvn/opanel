"""Demo mode: accounts anyone may sign in with, that can look at everything
and change nothing.

A demo account's password is public -- it is shown on the login page -- so
everything here is about what that password can never do:

- Read only. While Demo mode is on, a demo account's session may read (GET)
  but not write: every POST/PUT/PATCH/DELETE is refused before the route runs,
  except signing out and the few POSTs that only read. The GETs that hand out
  data wholesale (database dumps, backup archives, file downloads) are refused
  too, and so is the web terminal.
- Only while Demo mode is on. Off or removed, a demo account cannot sign in
  and any session it still holds stops working, so turning the addon off never
  leaves a full admin account behind with a published password.
- Not over SFTP. Every panel account is also a Linux/SFTP login that normally
  shares the panel password; a demo account's Linux password is set to a
  random one nobody knows.
- Taking an account off the list gives it a new random password, for the same
  reason.
"""
from __future__ import annotations

import re
import secrets

from fastapi import HTTPException, Request, status

from app.services import addons

ADDON_ID = "demo"
MAX_ACCOUNTS = 5
READ_METHODS = {"GET", "HEAD", "OPTIONS"}
# POSTs that only read, or end the visitor's own session.
READ_ONLY_POSTS = {
    "/api/auth/logout",
    "/api/maintenance/user-restore-backups/describe",
    # Listing is allowed for this server only; the route refuses another
    # server or a destination for a demo session (see is_demo_request).
    "/api/maintenance/restore/list",
}
# GETs that hand out data wholesale rather than show it.
BLOCKED_GETS = [re.compile(pattern) for pattern in (
    r"^/api/databases/\d+/download$",
    r"^/api/maintenance/backups/\d+/download$",
    r"^/api/maintenance/user-backups-download$",
    r"^/api/maintenance/files/\d+/download$",
)]
READ_ONLY_MESSAGE = "This is a read-only demo: changes are not saved."
OFF_MESSAGE = "This is a demo account, and demo mode is off."


def settings() -> dict:
    stored = addons._entry(ADDON_ID).get("settings") or {}
    accounts = [a for a in stored.get("accounts") or [] if isinstance(a, dict) and a.get("user_id")]
    return {"accounts": accounts, "show_on_login": bool(stored.get("show_on_login", True))}


def demo_user_ids() -> set[int]:
    return {int(a["user_id"]) for a in settings()["accounts"]}


def is_active() -> bool:
    return addons.is_enabled(ADDON_ID)


def is_demo_account(user) -> bool:
    return user is not None and user.id in demo_user_ids()


def is_demo_request(request: Request) -> bool:
    return bool(getattr(request.state, "demo", False))


def enforce(request: Request, user) -> None:
    """Called for every authenticated request, before its route runs."""
    if not is_demo_account(user):
        return
    if not is_active():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=OFF_MESSAGE)
    request.state.demo = True
    method = request.method.upper()
    path = request.url.path
    if method in READ_METHODS:
        if any(pattern.match(path) for pattern in BLOCKED_GETS):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=READ_ONLY_MESSAGE)
        return
    if method == "POST" and path in READ_ONLY_POSTS:
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=READ_ONLY_MESSAGE)


def public_accounts(db) -> list[dict]:
    """What the login page shows: nothing unless Demo mode is on and set to."""
    if not is_active():
        return []
    current = settings()
    if not current["show_on_login"]:
        return []
    from app.models.entities import User

    out = []
    for account in current["accounts"]:
        user = db.query(User).filter(User.id == int(account["user_id"])).first()
        if user and user.is_active:
            out.append({"username": user.username, "password": account.get("password") or "",
                        "role": user.role})
    return out


def describe(db) -> dict:
    from app.models.entities import User

    current = settings()
    rows = []
    for account in current["accounts"]:
        user = db.query(User).filter(User.id == int(account["user_id"])).first()
        if user:
            rows.append({"user_id": user.id, "username": user.username, "role": user.role,
                         "password": account.get("password") or ""})
    return {"accounts": rows, "show_on_login": current["show_on_login"], "active": is_active()}


def save(db, accounts: list[dict], show_on_login: bool, actor) -> dict:
    """Make these accounts the demo accounts, each with its public password."""
    from app.core.security import hash_password
    from app.models.entities import User, WebauthnCredential
    from app.services import site_users

    if len(accounts) > MAX_ACCOUNTS:
        raise ValueError(f"At most {MAX_ACCOUNTS} demo accounts")
    chosen: dict[int, tuple] = {}
    for account in accounts:
        user_id = int(account["user_id"])
        password = account["password"]
        if user_id in chosen:
            raise ValueError("An account is listed twice")
        if user_id == actor.id:
            raise ValueError("You cannot make your own account a demo account")
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise ValueError(f"User {user_id} not found")
        if not 12 <= len(password) <= 72:
            raise ValueError("A demo password must be 12 to 72 characters")
        chosen[user_id] = (user, password)

    previous = demo_user_ids()
    for user, password in chosen.values():
        # The Linux/SFTP login gets a password nobody knows: the panel one is
        # about to be published.
        site_users.set_panel_user_password(user.username, secrets.token_urlsafe(24))
        user.hashed_password = hash_password(password)
        user.totp_enabled, user.totp_secret = False, None
        user.is_active = True
        user.token_version = (user.token_version or 0) + 1
        db.query(WebauthnCredential).filter(WebauthnCredential.user_id == user.id).delete()
    for user_id in previous - set(chosen):
        user = db.query(User).filter(User.id == user_id).first()
        if user:
            # Its password was public; it must not keep working.
            user.hashed_password = hash_password(secrets.token_urlsafe(32))
            user.token_version = (user.token_version or 0) + 1
    db.commit()
    addons._update_state(ADDON_ID, settings={
        "accounts": [{"user_id": user.id, "password": password} for user, password in chosen.values()],
        "show_on_login": bool(show_on_login),
    })
    return describe(db)
