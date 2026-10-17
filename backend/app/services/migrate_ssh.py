"""Accounts pulled off a cPanel or DirectAdmin server over SSH as root (the
cPanel Import and DirectAdmin Import addons, 2026-10-18).

The Restore wizard's "cPanel server" and "DirectAdmin server" sources. The
panel signs in as root -- the login is typed in for this restore and kept in
memory only, like any other server's -- lists the accounts, and for each one
picked has that server make the account's backup the way it would itself:
pkgacct on cPanel, an admin backup through DirectAdmin's task queue. The archive
comes down over the same login's SFTP, its folder on the other server is
removed, and it is imported like an uploaded one.

Nothing typed in reaches a shell unchecked: account names are matched against
a narrow pattern, the work folder is the one mktemp printed, and every value
put in a command is quoted.
"""
from __future__ import annotations

import json
import os
import posixpath
import re
import shlex
import time
from pathlib import Path
from typing import Callable, Optional

import paramiko

PANELS = {"cpanel": "cPanel", "directadmin": "DirectAdmin"}
ACCOUNT_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
# What mktemp makes on the other server, and the only folder ever removed there.
WORK_RE = re.compile(r"^/home/opanel-migrate\.[A-Za-z0-9]{6,16}$")
LIST_TIMEOUT = 120
# A big account takes a long time to pack; a stuck pack still has to end.
PACK_TIMEOUT = 6 * 3600
DA_USERS = "/usr/local/directadmin/data/users"

StatusFn = Optional[Callable[[str], None]]
ProgressFn = Optional[Callable[[int, int], None]]


class MigrateError(RuntimeError):
    """The other server said no, phrased for the panel."""


def check_account(name: str) -> str:
    if not ACCOUNT_RE.fullmatch(name or ""):
        raise ValueError(f"Not an account name: {name!r}")
    return name


def _exec(client: paramiko.SSHClient, command: str, timeout: float) -> tuple[int, str, str]:
    """Run one command, reading both streams as they come so neither fills up."""
    transport = client.get_transport()
    if transport is None:
        raise MigrateError("The SSH connection closed")
    channel = transport.open_session()
    channel.exec_command(command)
    out, err = bytearray(), bytearray()
    deadline = time.monotonic() + timeout
    while True:
        while channel.recv_ready():
            out.extend(channel.recv(65536))
        while channel.recv_stderr_ready():
            err.extend(channel.recv_stderr(65536))
        if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
            break
        if time.monotonic() > deadline:
            channel.close()
            raise MigrateError("The other server took too long to answer")
        time.sleep(0.2)
        # Keep only the end of a long log: pkgacct prints a line per file.
        if len(out) > 4 * 1024 * 1024:
            del out[: len(out) - 1024 * 1024]
    code = channel.recv_exit_status()
    channel.close()
    return code, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


def _must(client, command: str, timeout: float, what: str) -> str:
    code, out, err = _exec(client, command, timeout)
    if code != 0:
        detail = (err or out).strip().splitlines()[-1:] or [f"exit {code}"]
        raise MigrateError(f"{what} failed on the other server: {detail[0][:300]}")
    return out


# --- Listing --------------------------------------------------------------------------------------

_CPANEL_LIST = (
    "if [ -x /usr/local/cpanel/bin/whmapi1 ]; then "
    "/usr/local/cpanel/bin/whmapi1 --output=json listaccts want=user,domain,diskused,suspended 2>/dev/null; "
    "elif [ -r /etc/trueuserdomains ]; then echo TRUEUSERDOMAINS; cat /etc/trueuserdomains; "
    "else echo NOT_CPANEL; fi"
)

_DA_LIST = (
    f"D={DA_USERS}; [ -d \"$D\" ] || {{ echo NOT_DIRECTADMIN; exit 0; }}; "
    "for f in \"$D\"/*/user.conf; do [ -f \"$f\" ] || continue; u=$(basename \"$(dirname \"$f\")\"); "
    "v() { grep -m1 \"^$1=\" \"$2\" 2>/dev/null | cut -d= -f2-; }; "
    "printf '%s\\t%s\\t%s\\t%s\\t%s\\t%s\\n' \"$u\" \"$(v usertype \"$f\")\" \"$(v creator \"$f\")\" "
    "\"$(v domain \"$f\")\" \"$(v suspended \"$f\")\" \"$(v quota \"$D/$u/user.usage\")\"; done"
)


def _megabytes(value) -> int:
    """'123M', '1.5G', 'none', 123.4 -> bytes."""
    text = str(value or "").strip().upper()
    match = re.match(r"^([0-9.]+)\s*([KMGT]?)", text)
    if not match:
        return 0
    number = float(match.group(1))
    scale = {"": 1024 ** 2, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}[match.group(2)]
    return int(number * scale)


def _row(panel: str, account: str, domain: str, size: int, note: str = "") -> dict:
    return {
        "ref": account,
        "kind": panel,
        "filename": domain or account,
        "account": account,
        "size": size,
        "modified_at": "",
        "location": "ssh",
        "domain": domain,
        "note": note,
    }


def parse_cpanel_accounts(text: str) -> list[dict]:
    text = text.strip()
    if text.startswith("NOT_CPANEL"):
        raise MigrateError("That server has no cPanel (no whmapi1 and no /etc/trueuserdomains)")
    rows = []
    if text.startswith("TRUEUSERDOMAINS"):
        for line in text.splitlines()[1:]:
            domain, _, user = line.partition(":")
            user = user.strip()
            if ACCOUNT_RE.fullmatch(user):
                rows.append(_row("cpanel", user, domain.strip().lower(), 0))
        return rows
    try:
        data = json.loads(text[text.index("{"):]) if "{" in text else {}
    except ValueError as exc:
        raise MigrateError("cPanel's account list could not be read") from exc
    for account in ((data.get("data") or {}).get("acct") or []):
        user = str(account.get("user") or "")
        if not ACCOUNT_RE.fullmatch(user):
            continue
        suspended = str(account.get("suspended") or "0") not in {"0", "", "False", "false"}
        rows.append(_row("cpanel", user, str(account.get("domain") or "").lower(),
                         _megabytes(account.get("diskused")), "suspended" if suspended else ""))
    return rows


def parse_directadmin_accounts(text: str) -> list[dict]:
    if text.strip().startswith("NOT_DIRECTADMIN"):
        raise MigrateError(f"That server has no DirectAdmin ({DA_USERS} is missing)")
    rows = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 6 or not ACCOUNT_RE.fullmatch(parts[0]):
            continue
        user, usertype, creator, domain, suspended, quota = parts[:6]
        notes = [usertype] if usertype in {"admin", "reseller"} else []
        if suspended.strip().lower() == "yes":
            notes.append("suspended")
        rows.append(_row("directadmin", user, domain.strip().lower(), _megabytes(quota), ", ".join(notes)))
    return rows


def list_accounts(client: paramiko.SSHClient, panel: str) -> list[dict]:
    if panel == "cpanel":
        return parse_cpanel_accounts(_must(client, _CPANEL_LIST, LIST_TIMEOUT, "Listing the accounts"))
    if panel == "directadmin":
        return parse_directadmin_accounts(_must(client, _DA_LIST, LIST_TIMEOUT, "Listing the accounts"))
    raise ValueError(f"Unknown panel: {panel}")


# --- Packing one account --------------------------------------------------------------------------

def _work_folder(client) -> str:
    folder = _must(client, "umask 077; mktemp -d /home/opanel-migrate.XXXXXXXXXX", LIST_TIMEOUT,
                   "Making a work folder").strip().splitlines()[-1:]
    folder = folder[0].strip() if folder else ""
    if not WORK_RE.fullmatch(folder):
        raise MigrateError(f"Unexpected work folder on the other server: {folder!r}")
    return folder


def _remove_work_folder(client, folder: str) -> None:
    if WORK_RE.fullmatch(folder or ""):
        try:
            _exec(client, f"rm -rf -- {shlex.quote(folder)}", LIST_TIMEOUT)
        except Exception:  # noqa: BLE001 - best effort; the folder is root's and private
            pass


def _archives(client, folder: str) -> list[tuple[str, int]]:
    out = _must(client, f"find {shlex.quote(folder)} -maxdepth 1 -type f -name '*.tar*' -printf '%f\\t%s\\n'",
                LIST_TIMEOUT, "Looking for the backup")
    found = []
    for line in out.splitlines():
        name, _, size = line.partition("\t")
        if name and "/" not in name and size.isdigit():
            found.append((name, int(size)))
    return found


def _pack_cpanel(client, account: str, folder: str) -> str:
    log = shlex.quote(posixpath.join(folder, "pkgacct.log"))
    command = (f"/usr/local/cpanel/scripts/pkgacct {shlex.quote(account)} {shlex.quote(folder)} "
               f"</dev/null >{log} 2>&1 || {{ tail -n 5 {log} >&2; exit 1; }}")
    _must(client, command, PACK_TIMEOUT, f"pkgacct {account}")
    for name, _size in _archives(client, folder):
        if name.startswith(f"cpmove-{account}.tar"):
            return name
    raise MigrateError(f"pkgacct finished but left no cpmove-{account} archive")


def _da_admin(client) -> str:
    out = _must(client, f"grep -l '^usertype=admin' {DA_USERS}/*/user.conf 2>/dev/null | head -n 1",
                LIST_TIMEOUT, "Finding the DirectAdmin admin")
    admin = Path(out.strip()).parent.name if out.strip() else ""
    if not ACCOUNT_RE.fullmatch(admin):
        raise MigrateError("No DirectAdmin admin account was found on that server")
    return admin


def _pack_directadmin(client, account: str, folder: str, on_status: StatusFn) -> str:
    admin = _da_admin(client)
    _must(client, f"chown {shlex.quote(admin)}: {shlex.quote(folder)} && chmod 0700 {shlex.quote(folder)}",
          LIST_TIMEOUT, "Preparing the work folder")
    # DirectAdmin's own "Admin Backup" of one account, to a local folder.
    task = ("action=backup&append%5Fto%5Fpath=nothing&database%5Fdata%5Faware=yes"
            "&email%5Fdata%5Faware=yes"
            f"&local%5Fpath={folder.replace('/', '%2F')}&owner={admin}&select%30={account}"
            "&type=admin&value=multiple&what=select&when=now&where=local")
    quoted = shlex.quote(task)
    command = (
        "if /usr/local/directadmin/directadmin taskq --help >/dev/null 2>&1; then "
        f"/usr/local/directadmin/directadmin taskq --run {quoted}; "
        f"else echo {quoted} >> /usr/local/directadmin/data/task.queue && /usr/local/directadmin/dataskq; fi"
    )
    _must(client, command, PACK_TIMEOUT, f"DirectAdmin backup of {account}")
    # The run usually ends with the file in place; when DirectAdmin's own cron
    # took the task first, wait for it to settle.
    deadline = time.monotonic() + PACK_TIMEOUT
    last: dict[str, int] = {}
    steady = 0
    while time.monotonic() < deadline:
        current = {name: size for name, size in _archives(client, folder) if f".{account}.tar" in name}
        if current and current == last:
            steady += 1
            if steady >= 2:
                return sorted(current)[0]
        else:
            steady = 0
        last = current
        if on_status and not current:
            on_status(f"Waiting for DirectAdmin to back up {account}")
        time.sleep(10)
    raise MigrateError(f"DirectAdmin did not write a backup of {account}")


def pack_and_fetch(client: paramiko.SSHClient, panel: str, account: str,
                   destination_for: Callable[[str], Path], ensure_room: Callable[[Path, int, str], None],
                   on_status: StatusFn = None, on_progress: ProgressFn = None) -> str:
    """Have the other server back up ``account``, download it, clean up there.

    ``destination_for(name)`` gives the local file for the archive's name.
    Returns the local path of the downloaded archive.
    """
    check_account(account)
    folder = _work_folder(client)
    try:
        if on_status:
            on_status(f"Packing {account} on the other server")
        if panel == "cpanel":
            name = _pack_cpanel(client, account, folder)
        elif panel == "directadmin":
            name = _pack_directadmin(client, account, folder, on_status)
        else:
            raise ValueError(f"Unknown panel: {panel}")
        destination = destination_for(name)
        partial = destination.with_name(destination.name + ".part")
        remote = posixpath.join(folder, name)
        sftp = client.open_sftp()
        try:
            total = sftp.stat(remote).st_size or 0
            ensure_room(destination.parent, total, name)
            if on_status:
                on_status(f"Downloading {name}")
            sftp.get(remote, str(partial), callback=(lambda done, of: on_progress(done, of)) if on_progress else None)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        finally:
            sftp.close()
        os.replace(partial, destination)
        return str(destination)
    finally:
        _remove_work_folder(client, folder)
