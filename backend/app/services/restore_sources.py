"""Where a restore comes from, and how an archive gets from there to here.

Three sources, one list to pick from:

- ``local``: archives already on this server -- the panel's own account
  backups, the restore upload folder, and the DirectAdmin folder;
- ``target``: a saved Backup Destination (S3 or SFTP);
- ``remote``: another server reached over SFTP, FTP or FTPS with credentials
  typed in for this one restore -- DirectAdmin's "restore from FTP", and the
  usual way off an old DirectAdmin box.

Every row says which kind of archive it is, because the two are restored by
different code: an OPanel account backup by ``backup.restore_user_backup``, a
DirectAdmin one by ``da_import.import_da_backup``.

Credentials for another server never touch the disk. They live in memory for
the request that lists and the job that downloads, and nowhere else.
"""
from __future__ import annotations

import ftplib
import os
import posixpath
import re
import secrets
import shutil
import socket
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator, Optional

import paramiko

from app.core.config import settings
from app.services import backup, da_import

KIND_OPANEL = "opanel"
KIND_DA = "directadmin"

_WEEKDAYS = "|".join(backup.WEEKDAY_SLOTS)
# <account>-<weekday>.tar.gz and <account>-manual-<weekday>.tar.gz: the names
# the panel writes, so the account is known without opening the archive.
OPANEL_SLOT_RE = re.compile(rf"^(?P<user>.+?)-(?:manual-)?(?:{_WEEKDAYS})\.tar\.gz$")
# user.<creator>.<account>.tar.zst (or .tar.gz and the rest): what DirectAdmin
# writes for every account, whichever compression the box is set to.
DA_NAME_RE = re.compile(
    r"^(?:user|reseller|admin)\.[^.]+\.(?P<user>[^.]+)\."
    r"(?:tar\.zst|tar\.gz|tgz|tar\.bz2|tbz2|tar\.xz|txz|tar)$",
    re.IGNORECASE,
)

# A listing is for a person to pick from. Past this it is the wrong folder,
# and walking on would only make them wait for a list nobody can read.
MAX_LIST_ENTRIES = 5000
MAX_SUBDIRS = 200
# Room left over after a download: the restore itself unpacks next to it.
FREE_SPACE_MARGIN = 512 * 1024 * 1024
TIMEOUT = 30
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")

ProgressFn = Optional[Callable[[int, int], None]]


class RestoreSourceError(RuntimeError):
    """A source that could not be reached or read, phrased for the panel."""


@dataclass
class Remote:
    """An SFTP, FTP or FTPS server: a saved SFTP destination or one typed in."""

    protocol: str
    host: str
    port: int
    username: str = ""
    password: str = ""
    private_key: str = ""
    path: str = ""
    # The key this server must present: pinned on a saved destination, or the
    # one seen when the list was read. Empty means trust on first use.
    host_key_type: str = ""
    host_key_fingerprint: str = ""
    # What it presented this time, for the caller to show or persist.
    seen_host_key_type: str = ""
    seen_host_key_fingerprint: str = ""


@dataclass
class S3Bucket:
    endpoint: str
    region: str
    bucket: str
    access_key: str
    secret_key: str
    prefix: str = ""
    use_path_style: bool = False


@dataclass
class Source:
    kind: str  # local | target | remote
    remote: Optional[Remote] = None
    s3: Optional[S3Bucket] = None

    @property
    def location(self) -> str:
        if self.s3 is not None:
            return "s3"
        if self.remote is not None:
            return self.remote.protocol
        return "local"


# --- What an archive is ------------------------------------------------------

def archive_kind(name: str) -> Optional[str]:
    """``opanel``, ``directadmin``, or None for anything that is not a backup.

    The panel only ever writes ``.tar.gz``; any other tar format, and any
    ``.tar.gz`` named the DirectAdmin way, is a DirectAdmin archive.
    """
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    if DA_NAME_RE.match(base):
        return KIND_DA
    lower = base.lower()
    if lower.endswith(".tar.gz"):
        return KIND_OPANEL
    if any(lower.endswith(suffix) for suffix in da_import.ARCHIVE_SUFFIXES):
        return KIND_DA
    return None


def account_of(kind: str, name: str, folder: str = "") -> str:
    """The account an archive belongs to, from its name or the folder it is in.

    Best effort: an OPanel archive with a name of its own and no telling
    folder says "" here and is described from its manifest later.
    """
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    if kind == KIND_DA:
        match = DA_NAME_RE.match(base)
        return match.group("user") if match else da_import._archive_username(base)
    match = OPANEL_SLOT_RE.match(base)
    if match:
        return match.group("user")
    return folder


def _row(ref: str, name: str, size, modified: str, location: str, folder: str = "") -> Optional[dict]:
    kind = archive_kind(name)
    if not kind:
        return None
    return {
        "ref": ref,
        "kind": kind,
        "filename": name,
        "account": account_of(kind, name, folder),
        "size": int(size or 0),
        "modified_at": modified or "",
        "location": location,
    }


def _check_remote_path(path: str) -> str:
    if _CONTROL_CHARS_RE.search(path or ""):
        raise ValueError("Control characters are not allowed in a path")
    return path


def _base_path(path: str) -> str:
    """The folder to list, as the server should see it. Empty is the login
    folder; a relative path stays relative to it."""
    path = _check_remote_path((path or "").strip())
    if not path:
        return "."
    normalised = posixpath.normpath(path)
    return normalised


def _under(base: str, ref: str) -> bool:
    ref = posixpath.normpath(ref)
    if base in {".", ""}:
        return not ref.startswith(("/", "../")) and ref != ".."
    if base == "/":
        return ref.startswith("/")
    return ref.startswith(base.rstrip("/") + "/")


def _iso(value) -> str:
    if not value:
        return ""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat() + "Z"
    if isinstance(value, (int, float)):
        return datetime.utcfromtimestamp(value).isoformat() + "Z"
    text = str(value).strip()
    # MLSD modify= and MDTM both answer YYYYMMDDHHMMSS[.sss], in UTC.
    match = re.match(r"^(\d{14})", text)
    if match:
        try:
            return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").isoformat() + "Z"
        except ValueError:
            return ""
    return ""


# --- Listing -----------------------------------------------------------------

def list_local() -> list[dict]:
    """Every archive already on this server, of either kind."""
    rows: list[dict] = []
    for item in backup.list_all_restorable_backups():
        rows.append({
            **item,
            "ref": item["backup_file"],
            "kind": KIND_OPANEL,
            "location": item.get("source") or "account",
            "account": item.get("account") or account_of(KIND_OPANEL, item["filename"]),
        })
    for item in da_import.list_da_backups():
        try:
            # list_da_backups stamps local time with a Z; say UTC like the rest.
            modified = _iso(Path(item["path"]).stat().st_mtime)
        except OSError:
            modified = ""
        rows.append({
            "ref": item["path"],
            "kind": KIND_DA,
            "filename": item["filename"],
            "account": account_of(KIND_DA, item["filename"]),
            "size": item.get("size") or 0,
            "modified_at": modified,
            "location": "da",
        })
    return rows


def _walk(entries: Callable[[str], list[tuple]], base: str, location: str) -> list[dict]:
    """The archives in ``base`` and one level of folders below it.

    One level is how backups are laid out in practice: flat, or one folder per
    account (the panel's S3 layout) or per date (DirectAdmin's "append date").
    """
    rows: list[dict] = []
    seen = 0
    folders: list[str] = []
    for name, is_dir, size, modified in entries(base):
        seen += 1
        if is_dir:
            if not name.startswith("."):
                folders.append(name)
            continue
        row = _row(posixpath.join(base, name), name, size, modified, location)
        if row:
            rows.append(row)
    for folder in folders[:MAX_SUBDIRS]:
        try:
            children = entries(posixpath.join(base, folder))
        except (OSError, ftplib.Error, EOFError):
            continue  # a folder we may not read is not a reason to show nothing
        for name, is_dir, size, modified in children:
            seen += 1
            if seen > MAX_LIST_ENTRIES:
                raise RestoreSourceError(
                    f"More than {MAX_LIST_ENTRIES} files here. Enter the folder that holds the backups."
                )
            if is_dir:
                continue
            row = _row(posixpath.join(base, folder, name), name, size, modified, location, folder)
            if row:
                rows.append(row)
    rows.sort(key=lambda row: row["modified_at"], reverse=True)
    return rows


@contextmanager
def _sftp(remote: Remote) -> Iterator[paramiko.SFTPClient]:
    pkey = backup._load_private_key(remote.private_key, password=remote.password or None) \
        if remote.private_key else None
    if not pkey and not remote.password:
        raise RestoreSourceError("Enter a password or a private key")
    client = paramiko.SSHClient()
    # No known_hosts: the only key trusted is the pinned one, or -- with no
    # pin -- whichever one answers, which the caller then shows or pins.
    client.set_missing_host_key_policy(backup._PinnedHostKeyPolicy(
        remote.host_key_type or None, remote.host_key_fingerprint or None))
    try:
        client.connect(
            hostname=remote.host,
            port=remote.port,
            username=remote.username,
            password=None if pkey else remote.password,
            pkey=pkey,
            timeout=TIMEOUT,
            banner_timeout=TIMEOUT,
            auth_timeout=TIMEOUT,
            allow_agent=False,
            look_for_keys=False,
        )
        transport = client.get_transport()
        if transport is None:
            raise RestoreSourceError("SFTP connection closed")
        key = transport.get_remote_server_key()
        fingerprint = backup._fingerprint(key)
        if remote.host_key_fingerprint and fingerprint != remote.host_key_fingerprint:
            raise backup.SftpHostKeyMismatch(
                f"SFTP host key for {remote.host} changed: expected "
                f"{remote.host_key_fingerprint}, got {fingerprint}"
            )
        remote.seen_host_key_type = key.get_name()
        remote.seen_host_key_fingerprint = fingerprint
        sftp = client.open_sftp()
        sftp.get_channel().settimeout(TIMEOUT * 4)
        try:
            yield sftp
        finally:
            sftp.close()
    finally:
        client.close()


def _sftp_entries(sftp: paramiko.SFTPClient, path: str) -> list[tuple]:
    out = []
    for attr in sftp.listdir_attr(path):
        mode = attr.st_mode or 0
        if stat.S_ISDIR(mode):
            out.append((attr.filename, True, 0, ""))
        elif stat.S_ISREG(mode):
            out.append((attr.filename, False, attr.st_size or 0, _iso(attr.st_mtime)))
    return out


@contextmanager
def _ftp(remote: Remote) -> Iterator[ftplib.FTP]:
    ftp = ftplib.FTP_TLS(timeout=TIMEOUT) if remote.protocol == "ftps" else ftplib.FTP(timeout=TIMEOUT)
    try:
        ftp.connect(remote.host, remote.port)
        # FTP_TLS.login secures the control channel (AUTH TLS) before the
        # password goes over it; prot_p then covers the data channel too.
        ftp.login(remote.username or "anonymous", remote.password or "")
        if remote.protocol == "ftps":
            ftp.prot_p()
        ftp.voidcmd("TYPE I")
        yield ftp
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001 - the session may already be gone
            ftp.close()


def _ftp_entries(ftp: ftplib.FTP, path: str) -> list[tuple]:
    try:
        out = []
        for name, facts in ftp.mlsd(path, facts=["type", "size", "modify"]):
            kind = (facts.get("type") or "").lower()
            if name in {".", ".."} or kind in {"cdir", "pdir"}:
                continue
            is_dir = kind == "dir"
            out.append((name, is_dir, int(facts.get("size") or 0), _iso(facts.get("modify"))))
        return out
    except ftplib.error_perm as exc:
        # 50x is "no MLSD here"; anything else (550: no such folder) is the
        # server's real answer.
        if str(exc)[:3] not in {"500", "501", "502", "504"}:
            raise
    # No MLSD: a bare name list, then SIZE and MDTM per entry. SIZE fails on a
    # folder, which is how a folder is told from a file here.
    try:
        names = ftp.nlst(path)
    except ftplib.error_perm as exc:
        if str(exc)[:3] == "550":
            return []  # many servers answer an empty folder with 550
        raise
    out = []
    for raw in names:
        name = raw.rstrip("/").rsplit("/", 1)[-1]
        if not name or name in {".", ".."}:
            continue
        full = posixpath.join(path, name)
        try:
            size = ftp.size(full)
        except ftplib.all_errors:
            size = None
        if size is None:
            out.append((name, True, 0, ""))
            continue
        modified = ""
        try:
            modified = _iso(ftp.voidcmd(f"MDTM {full}").split()[-1])
        except ftplib.all_errors:
            pass
        out.append((name, False, size, modified))
    return out


def _s3_client(bucket: S3Bucket):
    return backup._s3_client(endpoint=bucket.endpoint, region=bucket.region,
                             access_key=bucket.access_key, secret_key=bucket.secret_key,
                             use_path_style=bucket.use_path_style)


def list_source(source: Source) -> list[dict]:
    if source.kind == "local":
        return list_local()
    if source.s3 is not None:
        bucket = source.s3
        rows = backup.list_s3_backups(
            endpoint=bucket.endpoint, region=bucket.region, bucket=bucket.bucket,
            access_key=bucket.access_key, secret_key=bucket.secret_key,
            prefix=bucket.prefix, use_path_style=bucket.use_path_style,
        )
        base = backup.s3_prefix_for(bucket.prefix)
        out = []
        for entry in rows:
            key = entry["key"]
            tail = key[len(base):].lstrip("/") if base else key
            # <account>/<file> is how the panel writes to S3.
            folder = tail.split("/")[0] if "/" in tail else ""
            row = _row(key, key.rsplit("/", 1)[-1], entry.get("size"), _iso(entry.get("modified")), "s3", folder)
            if row:
                out.append(row)
        return out
    remote = source.remote
    if remote is None:
        raise RestoreSourceError("No source to list")
    base = _base_path(remote.path)
    if remote.protocol == "sftp":
        with _sftp(remote) as sftp:
            return _walk(lambda path: _sftp_entries(sftp, path), base, "sftp")
    with _ftp(remote) as ftp:
        return _walk(lambda path: _ftp_entries(ftp, path), base, remote.protocol)


# --- Checking a pick ---------------------------------------------------------

def check_ref(source: Source, kind: str, ref: str) -> str:
    """Refuse a pick that is not an archive of that kind from that source.

    The ref comes back from the browser, so it is checked again rather than
    trusted because the list once contained it.
    """
    _check_remote_path(ref)
    if archive_kind(ref) != kind:
        raise ValueError(f"Not a {'DirectAdmin' if kind == KIND_DA else 'OPanel'} backup: {ref}")
    if source.kind == "local":
        if kind == KIND_OPANEL:
            return str(backup.user_backup_path(ref))
        return str(da_import._resolve_da_backup_path(ref))
    if source.s3 is not None:
        base = backup.s3_prefix_for(source.s3.prefix)
        if base and not ref.startswith(base + "/"):
            raise ValueError(f"Not in this destination: {ref}")
        return ref
    if source.remote is None or not _under(_base_path(source.remote.path), ref):
        raise ValueError(f"Not in the folder that was listed: {ref}")
    return posixpath.normpath(ref)


# --- Fetching ----------------------------------------------------------------

def _destination(kind: str, name: str) -> Path:
    """A fresh file to download into, in the folder that kind is restored from.

    Never an existing name: a fetch must not overwrite an archive someone
    uploaded, and the copy is deleted again once it has been restored.
    """
    folder = (backup._user_restore_dir() if kind == KIND_OPANEL else Path(settings.da_backup_dir)).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    safe = Path(name.replace("\\", "/")).name
    if not safe or archive_kind(safe) != kind:
        raise ValueError(f"Not a backup archive: {name}")
    target = (folder / safe).resolve()
    if target.exists() or target.with_name(target.name + ".part").exists():
        stem = da_import._strip_archive_suffix(safe)
        suffix = safe[len(stem):]
        target = (folder / f"{stem}-{datetime.utcnow():%Y%m%d%H%M%S}-{secrets.token_hex(3)}{suffix}").resolve()
    if target.parent != folder:
        raise ValueError(f"Invalid backup name: {name}")
    return target


def _ensure_room(folder: Path, size: int, name: str) -> None:
    if not size:
        return
    free = shutil.disk_usage(folder).free
    if free < size + FREE_SPACE_MARGIN:
        raise RestoreSourceError(
            f"Not enough disk space to download {name}: it is {_human(size)} "
            f"and {_human(free)} is free"
        )


def _human(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


class _Throttle:
    """Report progress at most once a second. An update for every 32 KB block
    is far more than the job store, or anyone watching the bar, can use."""

    def __init__(self, on_progress: ProgressFn, total: int):
        self.on_progress = on_progress
        self.total = total
        self.done = 0
        self.last = 0.0

    def add(self, count: int) -> None:
        self.set(self.done + count)

    def set(self, done: int) -> None:
        self.done = done
        now = time.monotonic()
        if self.on_progress and (now - self.last >= 1.0 or (self.total and done >= self.total)):
            self.last = now
            self.on_progress(done, self.total)


def fetch(source: Source, kind: str, ref: str, size: int = 0, on_progress: ProgressFn = None) -> str:
    """Download one archive into the folder its kind is restored from.

    Returns the local path. A partial download is never left under a name the
    restore list would offer: it is written as ``.part`` and renamed whole.
    """
    name = ref.replace("\\", "/").rsplit("/", 1)[-1]
    destination = _destination(kind, name)
    partial = destination.with_name(destination.name + ".part")
    try:
        if source.s3 is not None:
            _fetch_s3(source.s3, ref, partial, name, size, on_progress)
        elif source.remote is not None and source.remote.protocol == "sftp":
            with _sftp(source.remote) as sftp:
                total = sftp.stat(ref).st_size or size
                _ensure_room(destination.parent, total, name)
                throttle = _Throttle(on_progress, total)
                sftp.get(ref, str(partial), callback=lambda done, _total: throttle.set(done))
        elif source.remote is not None:
            with _ftp(source.remote) as ftp:
                try:
                    total = ftp.size(ref) or size
                except ftplib.all_errors:
                    total = size
                _ensure_room(destination.parent, total, name)
                throttle = _Throttle(on_progress, total)
                with partial.open("wb") as handle:
                    def write(block: bytes) -> None:
                        handle.write(block)
                        throttle.add(len(block))
                    ftp.retrbinary(f"RETR {ref}", write, blocksize=1024 * 1024)
        else:
            raise RestoreSourceError("Nothing to download from")
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    os.replace(partial, destination)
    return str(destination)


def _fetch_s3(bucket: S3Bucket, key: str, partial: Path, name: str, size: int, on_progress: ProgressFn) -> None:
    client = _s3_client(bucket)
    try:
        total = int(client.head_object(Bucket=bucket.bucket, Key=key).get("ContentLength") or size)
    except Exception:  # noqa: BLE001 - the download below reports the real failure
        total = size
    _ensure_room(partial.parent, total, name)
    throttle = _Throttle(on_progress, total)
    try:
        client.download_file(bucket.bucket, key, str(partial), Callback=throttle.add)
    except Exception as exc:  # noqa: BLE001
        raise backup._s3_failure(exc, bucket.bucket) from exc


# --- Errors ------------------------------------------------------------------

def describe_error(exc: BaseException, source: Optional[Source] = None) -> str:
    """One line an operator can act on, instead of a library's exception."""
    where = ""
    if source is not None and source.remote is not None:
        where = f"{source.remote.host}:{source.remote.port}"
    if isinstance(exc, paramiko.AuthenticationException):
        return f"SFTP login to {where} failed: check the username, password or key"
    if isinstance(exc, ftplib.error_perm) and str(exc).startswith("530"):
        return f"FTP login to {where} failed: check the username and password"
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return f"Timed out connecting to {where}" if where else "Timed out"
    if isinstance(exc, ConnectionRefusedError):
        return f"Connection to {where} refused: check the port and protocol"
    if isinstance(exc, socket.gaierror):
        return f"Cannot resolve {source.remote.host if source and source.remote else 'the host'}"
    if isinstance(exc, FileNotFoundError):
        return "Folder or file not found on the server"
    if isinstance(exc, PermissionError):
        return "Permission denied on the server"
    message = str(exc).strip() or exc.__class__.__name__
    return message[:500]
