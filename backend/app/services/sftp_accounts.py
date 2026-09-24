"""Extra SFTP logins for one folder of a hosting account.

A hosting account already logs in over SFTP as its own Linux user, jailed in
/home/<user>, with its panel password. This module adds sub-accounts: a second
login, its own password, limited to one folder -- the whole account, one
website, or a folder inside a website. The Linux side (user, jail, bind mount,
sshd block) is opanel-helper's sftp-sub-* commands; this keeps the rows.
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Optional

from sqlalchemy.orm import Session

from app.models.entities import SftpAccount, User, Website
from app.services import network, panel_settings, site_users
from app.services.shell import shell

HOME_ROOT = PurePosixPath("/home")
MAX_ACCOUNTS_PER_OWNER = 10
SUFFIX_RE = re.compile(r"^[a-z0-9]{1,16}$")
SUBPATH_RE = re.compile(r"^[A-Za-z0-9._/-]*$")
SSHD_CONFIG = Path("/etc/ssh/sshd_config")


def validate_password(password: str) -> str:
    value = password or ""
    if not 12 <= len(value) <= 72:
        raise ValueError("Password must be 12-72 characters")
    if any(ch in value for ch in (":", "\r", "\n")):
        raise ValueError("Password cannot contain ':' or line breaks")
    return value


def owner_linux_user(owner: User) -> str:
    return site_users.linux_user_for_panel_username(owner.username)


def account_username(owner: User, suffix: str) -> str:
    value = (suffix or "").strip().lower()
    if not SUFFIX_RE.fullmatch(value):
        raise ValueError("Name must be 1-16 lowercase letters or digits")
    username = f"{owner_linux_user(owner)}_{value}"
    if len(username) > 32:
        raise ValueError("Name is too long for this account; use a shorter one")
    return username


def resolve_directory(db: Session, owner: User, website_id: Optional[int], subpath: str) -> tuple[str, Optional[Website]]:
    """The absolute folder a new login is limited to, inside /home/<owner>."""
    home = HOME_ROOT / owner_linux_user(owner)
    website = None
    if website_id:
        website = db.query(Website).filter(Website.id == website_id).first()
        if not website or website.owner_id != owner.id:
            raise ValueError("Website not found for this account")
        base = PurePosixPath(website.root_path or "")
        if base != home and home not in base.parents:
            raise ValueError("Website folder is outside this account")
    else:
        base = home
    raw = (subpath or "").strip().strip("/")
    if not SUBPATH_RE.fullmatch(raw):
        raise ValueError("Folder may only contain letters, digits and . _ / -")
    parts = [part for part in raw.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise ValueError("Folder cannot contain . or .. segments")
    folder = base.joinpath(*parts) if parts else base
    return str(folder), website


def display_directory(owner: User, directory: str) -> str:
    """The folder as the account sees it: relative to its own home."""
    home = HOME_ROOT / owner_linux_user(owner)
    path = PurePosixPath(directory)
    if path == home:
        return "/"
    try:
        return "/" + str(path.relative_to(home))
    except ValueError:
        return directory


def sftp_port(config: Path = SSHD_CONFIG) -> int:
    """The port sshd listens on: the first global Port line, else 22."""
    try:
        lines = config.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 22
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("match "):
            break
        match = re.match(r"(?i)^port\s+(\d{1,5})\b", stripped)
        if match and 0 < int(match.group(1)) < 65536:
            return int(match.group(1))
    return 22


def sftp_host() -> str:
    """Where a client connects: the panel's hostname if it is a name, else the server's IPv4."""
    host = (panel_settings.current_settings().get("panel_hostname") or "").strip()
    if host and not re.fullmatch(r"[0-9.]+", host) and host != "localhost":
        return host
    try:
        addresses = network.detect_addresses().get("ipv4") or []
    except Exception:  # detection is best effort
        addresses = []
    return addresses[0] if addresses else host


def create_account(db: Session, owner: User, suffix: str, website_id: Optional[int], subpath: str, password: str) -> SftpAccount:
    if owner.role == "admin":
        raise ValueError("SFTP accounts belong to a hosting account, not an administrator")
    password = validate_password(password)
    username = account_username(owner, suffix)
    if db.query(SftpAccount).filter(SftpAccount.owner_id == owner.id).count() >= MAX_ACCOUNTS_PER_OWNER:
        raise ValueError(f"An account can have at most {MAX_ACCOUNTS_PER_OWNER} extra SFTP logins")
    if db.query(SftpAccount).filter(SftpAccount.username == username).first():
        raise ValueError(f"{username} already exists")
    directory, website = resolve_directory(db, owner, website_id, subpath)
    shell.privileged(
        "sftp-sub-create",
        helper_args=[owner_linux_user(owner), username, directory],
        input=f"{password}\n",
        sensitive=True,
        fallback=["true"],
    )
    account = SftpAccount(owner_id=owner.id, username=username,
                          website_id=website.id if website else None, directory=directory)
    db.add(account)
    db.commit()
    db.refresh(account)
    return account


def set_password(account: SftpAccount, owner: User, password: str) -> None:
    password = validate_password(password)
    shell.privileged(
        "sftp-sub-password",
        helper_args=[owner_linux_user(owner), account.username],
        input=f"{password}\n",
        sensitive=True,
        fallback=["true"],
    )


def delete_account(db: Session, account: SftpAccount, owner: User, commit: bool = True) -> None:
    shell.privileged(
        "sftp-sub-delete",
        helper_args=[owner_linux_user(owner), account.username],
        fallback=["true"],
    )
    db.delete(account)
    if commit:
        db.commit()


def delete_for_website(db: Session, website: Website) -> list[str]:
    """Remove the logins limited to a website's folder, before the folder goes."""
    removed = []
    for account in db.query(SftpAccount).filter(SftpAccount.website_id == website.id).all():
        delete_account(db, account, account.owner, commit=False)
        removed.append(account.username)
    return removed


def delete_for_owner(db: Session, owner: User) -> list[str]:
    removed = []
    for account in db.query(SftpAccount).filter(SftpAccount.owner_id == owner.id).all():
        delete_account(db, account, owner, commit=False)
        removed.append(account.username)
    return removed


def serialize(account: SftpAccount, websites_by_id: dict[int, Website] | None = None) -> dict:
    website = (websites_by_id or {}).get(account.website_id) if account.website_id else None
    return {
        "id": account.id,
        "username": account.username,
        "owner_id": account.owner_id,
        "owner": account.owner.username if account.owner else None,
        "website_id": account.website_id,
        "domain": website.domain if website else None,
        "directory": display_directory(account.owner, account.directory) if account.owner else account.directory,
        "created_at": account.created_at.isoformat() if account.created_at else None,
    }
