from datetime import datetime, timezone
from io import BytesIO, StringIO
import hashlib
import json
import logging
import os
from pathlib import Path
import posixpath
import time
import re
import secrets
import shutil
import tarfile
import tempfile
from typing import List, Optional

import paramiko
from paramiko.pkey import PKey
from paramiko.ssh_exception import SSHException

from app.core.config import settings
from app.core.secrets import decrypt, encrypt
from app.core.security import hash_password
from app.core.permissions import Role
from app.models.entities import DatabaseAccount, User, Website, WebsiteAlias
from app.services import mariadb, openlitespeed, site_users, waf, wordpress
from app.services import ssl as ssl_service
from app.services.shell import shell


logger = logging.getLogger("opanel.backup")

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
# A cert, key or CA bundle is a few KB; anything near this is not one.
MAX_SSL_MEMBER_BYTES = 256 * 1024
SITE_RESTORE_MAX_ITEMS = 200000
SITE_RESTORE_MAX_BYTES = 20 * 1024 * 1024 * 1024
BACKUP_MANIFEST = "manifest.json"
# Written last, because what a walk had to skip is only known when it ends.
BACKUP_SKIPPED = "skipped.json"
PANEL_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,64}$")

# Full-user archives opanel will restore. opanel writes "opanel_user"; "bpanel_user"
# is the same archive layout (sites/<domain>/site, databases/<domain>.sql, a
# version-1 website schema) written by bpanel, opanel's predecessor, so accept it
# too and move accounts between the panels. bpanel's extra "applications" section
# is ignored — only domains, files and databases are restored.
RESTORABLE_BACKUP_KINDS = ("opanel_user", "bpanel_user")
_PLACEHOLDER_EMAIL_SUFFIXES = (
    "@users.opanel.test", "@users.opanel.vn",
    "@users.bpanel.test", "@users.bpanel.vn",
)


def _hostname_conflicts(db, domain: str, exclude_website_id: int | None = None) -> bool:
    safe = (domain or "").strip().lower()
    if not safe:
        return True
    reserved: set[str] = set()
    for website in db.query(Website).all():
        if exclude_website_id is not None and website.id == exclude_website_id:
            continue
        hostname = (website.domain or "").strip().lower()
        if hostname:
            reserved.add(hostname)
            reserved.add(f"www.{hostname}")
    for alias in db.query(WebsiteAlias).all():
        if exclude_website_id is not None and alias.website_id == exclude_website_id:
            continue
        alias_host = (alias.domain or "").strip().lower()
        if alias_host:
            reserved.add(alias_host)
    return safe in reserved or f"www.{safe}" in reserved


def _tree_size(root) -> int:
    """Bytes of regular files under a path, without reading any of them.

    A stat walk over twenty thousand files is well under a second; gzipping
    the five gigabytes they hold is minutes. Paying the former to make the
    latter measurable is a good trade. Anything unreadable counts as nothing
    rather than stopping the count.
    """
    total = 0
    stack = [Path(root)]
    while stack:
        path = stack.pop()
        try:
            if path.is_symlink():
                continue
            if path.is_dir():
                stack.extend(path.iterdir())
            elif path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _add_tree(tar: tarfile.TarFile, source, arcname: str, skipped: list,
              on_bytes=None) -> None:
    """Add a directory tree, recording what could not be read instead of dying.

    ``tarfile.add()`` walks the tree itself and aborts the whole archive on the
    first entry it cannot open. Site PHP runs as the site's own user, so it can
    drop a file the panel cannot read at any moment -- Laravel writes
    bootstrap/cache/*.php with no permissions for "other" -- and one such file
    used to cost the operator the entire backup of every site they own.

    A silent gap would be worse than the failure it replaces, so every skipped
    path is collected: the caller puts the list in the manifest, so it travels
    with the archive and is visible at restore time, and reports the count.
    """
    root = Path(source)
    stack = [(root, arcname)]
    written = 0
    while stack:
        path, name = stack.pop()
        try:
            size = path.stat().st_size if path.is_file() and not path.is_symlink() else 0
        except OSError:
            size = 0
        try:
            tar.add(path, arcname=name, recursive=False)
        except (OSError, ValueError) as exc:
            skipped.append({"path": str(path), "reason": str(exc)})
            continue
        if size and on_bytes:
            written += size
            on_bytes(written)
        # is_dir() follows symlinks; a link was just stored as a link and must
        # not be walked, or a link pointing up the tree would loop.
        if path.is_symlink() or not path.is_dir():
            continue
        try:
            children = sorted(path.iterdir())
        except OSError as exc:
            skipped.append({"path": str(path), "reason": str(exc)})
            continue
        for child in reversed(children):
            stack.append((child, f"{name}/{child.name}"))


def _add_bytes(tar: tarfile.TarFile, arcname: str, payload: bytes, mode: int = 0o600) -> None:
    """Store bytes the caller already holds, without a file on disk.

    A private key must not be written to a temp directory on the way into the
    archive; it goes straight from memory into the stream.
    """
    info = tarfile.TarInfo(arcname)
    info.size = len(payload)
    info.mode = mode
    info.mtime = int(time.time())
    tar.addfile(info, BytesIO(payload))


def describe_skipped(skipped: list) -> str:
    """One line naming what was left out, for the panel's run message."""
    if not skipped:
        return ""
    names = ", ".join(item["path"] for item in skipped[:3])
    if len(skipped) > 3:
        names += f", and {len(skipped) - 3} more"
    return (f"{len(skipped)} unreadable file(s) were skipped ({names}). "
            "Run Fix permissions on the affected site to include them.")


def create_backup(website: Website, db_name: Optional[str] = None,
                  skipped: Optional[list] = None) -> str:
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    backup_dir = _site_backup_dir(website.domain)
    archive = backup_dir / f"{website.domain}-{stamp}.tar.gz"
    sql_file = backup_dir / f"{website.domain}-{stamp}.sql"
    shell.run(["mkdir", "-p", str(backup_dir)])
    backup_dir.mkdir(parents=True, exist_ok=True)
    if db_name:
        mariadb.export_database(db_name, str(sql_file))
        if settings.command_dry_run and not sql_file.exists():
            sql_file.write_text(f"-- DRY RUN database dump for {db_name}\n", encoding="utf-8")
    if skipped is None:
        skipped = []
    try:
        with tarfile.open(archive, "w:gz") as tar:
            _add_tree(tar, website.root_path, "site", skipped)
            if sql_file.exists():
                tar.add(sql_file, arcname=f"database/{sql_file.name}")
    finally:
        # The dump is inside the archive, and restore reads it from there.
        # Left beside it, every site backup kept a second, uncompressed copy
        # of the database that nothing ever removed.
        sql_file.unlink(missing_ok=True)
    if skipped:
        logger.warning("Backup of %s skipped %d unreadable path(s): %s",
                       website.domain, len(skipped),
                       ", ".join(item["path"] for item in skipped[:5]))
    return str(archive)


# One place, split by what a thing is rather than by what made it:
#
#   <backup_root>/users/<account>/   whole accounts
#   <backup_root>/sites/<domain>/    single websites
#   <backup_root>/restore/           waiting to be restored
#
# A website used to drop its folder at the root, beside users/ and
# db-snapshots/, so the layout depended on a domain never being called one of
# those. Under sites/ it cannot collide with anything.
def _user_backup_dir(username: str) -> Path:
    if not PANEL_USERNAME_RE.fullmatch(username or ""):
        raise ValueError("Invalid panel username")
    return Path(settings.backup_root) / "users" / username


def _site_backup_dir(domain: str) -> Path:
    safe = (domain or "").strip().lower()
    # A domain reaches here from a Website row, but it also reaches here from
    # an upload form, so it is checked rather than trusted.
    if not safe or not site_users.DOMAIN_RE.fullmatch(safe):
        raise ValueError("Invalid domain")
    return Path(settings.backup_root) / "sites" / safe


def _user_restore_dir() -> Path:
    return Path(settings.backup_root) / "restore"


def user_restore_dir() -> str:
    return str(_user_restore_dir())


WEEKDAY_SLOTS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def weekday_slot(when: datetime | None = None) -> str:
    """The rotation slot for a run: monday..sunday.

    A scheduled backup writes over the same weekday from last week, so a week
    of dailies occupies exactly seven files and stays that way even if the
    pruning step never runs.
    """
    return WEEKDAY_SLOTS[(when or datetime.now()).weekday()]


MANUAL_INFIX = "-manual-"


def manual_slot_filename(username: str, when: datetime | None = None) -> str:
    """The slot a backup taken by hand writes to.

    Seven of these per account, same as the schedule, so a run of manual
    backups is bounded instead of piling up forever. The infix keeps it clear
    of the scheduled slot for the same weekday.
    """
    return f"{username}{MANUAL_INFIX}{weekday_slot(when)}.tar.gz"


def rotation_filenames(username: str) -> set:
    """Every name a rotation may legitimately write for this account."""
    return ({f"{username}-{slot}.tar.gz" for slot in WEEKDAY_SLOTS}
            | {f"{username}{MANUAL_INFIX}{slot}.tar.gz" for slot in WEEKDAY_SLOTS})


def is_manual_slot(name: str) -> bool:
    """A manual slot bounds itself, so retention must leave it alone.

    Counting them towards a schedule's ``keep`` would let a week of scheduled
    backups evict the copy somebody took by hand before a risky change -- or
    the other way round.
    """
    return MANUAL_INFIX in (name or "").rsplit("/", 1)[-1]


STAGING_PREFIX = "opanel-user-backup-"
# Well past any real run, so a sweep can never meet one in progress.
STAGING_STALE_SECONDS = 6 * 3600


def _sweep_stale_staging(backup_dir: Path) -> None:
    """Remove staging directories a previous run never got to clean up.

    An archive is built in a temporary directory next to its destination, and
    TemporaryDirectory removes it on the way out -- but not when the process is
    killed, which is what every panel update does to a backup in flight. One
    such leftover on the production box held 6.2 GB and nothing was ever going
    to reclaim it.
    """
    cutoff = time.time() - STAGING_STALE_SECONDS
    for path in backup_dir.glob(f"{STAGING_PREFIX}*"):
        try:
            if not path.is_dir() or path.stat().st_mtime > cutoff:
                continue
            shutil.rmtree(path, ignore_errors=True)
            logger.warning("Removed stale backup staging directory %s", path)
        except OSError:
            continue


def create_user_backup(user: User, db, filename: str | None = None,
                       skipped: Optional[list] = None, on_progress=None) -> str:
    if skipped is None:
        skipped = []
    backup_dir = _user_backup_dir(user.username)
    backup_dir.mkdir(parents=True, exist_ok=True)
    _sweep_stale_staging(backup_dir)
    if filename:
        # <account>-<weekday>.tar.gz, so the name identifies the account even
        # when the file is looked at outside its folder.
        if filename not in rotation_filenames(user.username):
            raise ValueError("Invalid rotation filename")
        archive = backup_dir / filename
    else:
        # A backup taken by hand rotates on its own seven slots. A timestamp
        # would never repeat, so nothing would ever overwrite it and nothing
        # would ever remove it -- a run of manual backups filled the
        # destination and stayed there. The -manual- infix keeps it clear of
        # the scheduled slot for the same day.
        archive = backup_dir / manual_slot_filename(user.username)
    websites = db.query(Website).filter(Website.owner_id == user.id).order_by(Website.id.asc()).all()

    with tempfile.TemporaryDirectory(prefix=STAGING_PREFIX, dir=str(backup_dir)) as tmp:
        tmp_dir = Path(tmp)
        manifest = {
            "kind": "opanel_user",
            "version": 1,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            # No hashed_password and no role. The archive leaves the box --
            # upload_to_s3 does a plain upload_file with no client-side
            # encryption -- so exporting the account's bcrypt hash handed
            # anyone who can read the bucket material for an offline attack,
            # and manifest.json is deliberately the first member so it comes
            # out of a few kilobytes without touching the site data. The
            # restore no longer reads either field (it always creates an
            # end_user with a generated password), so there is nothing left to
            # export them for.
            "user": {
                "username": user.username,
                "email": user.email,
                "is_active": user.is_active,
                "website_limit": user.website_limit,
                "storage_limit_mb": user.storage_limit_mb,
            },
            "websites": [],
            "databases": [],
        }

        # Every database the account owns, not only the ones a Website row
        # happens to point at. A database created on the Databases page is
        # stored with an owner and no website_id, and a site declares the
        # database it actually uses in its own source -- Laravel in .env -- in
        # whatever shape it likes. Backing up only what the panel had linked
        # meant a full user backup could silently contain no data at all.
        owned_ids = {website.id for website in websites}
        db_items = [
            item for item in db.query(DatabaseAccount).order_by(DatabaseAccount.id.asc()).all()
            if item.owner_id == user.id or (item.website_id and item.website_id in owned_ids)
        ]
        domain_of = {website.id: website.domain for website in websites}

        sql_files: dict[str, Path] = {}
        ssl_files: dict[str, dict] = {}
        database_entries: dict[int, dict] = {}
        for item in db_items:
            safe_name = Path(item.db_name or "").name
            if not safe_name or safe_name != item.db_name:
                skipped.append({"path": f"database:{item.db_name}", "reason": "Invalid database name"})
                continue
            sql_path = tmp_dir / f"{safe_name}.sql"
            try:
                mariadb.export_database(item.db_name, str(sql_path))
            except Exception as exc:
                # A row left behind by a database that was dropped outside the
                # panel must not cost the account its whole backup.
                skipped.append({"path": f"database:{item.db_name}", "reason": str(exc)})
                continue
            if settings.command_dry_run and not sql_path.exists():
                sql_path.write_text(f"-- DRY RUN database dump for {item.db_name}\n", encoding="utf-8")
            if not sql_path.exists():
                skipped.append({"path": f"database:{item.db_name}", "reason": "Dump produced no file"})
                continue
            try:
                db_password = decrypt(item.db_password)
            except RuntimeError:
                db_password = ""
            entry = {
                "db_name": item.db_name,
                "db_user": item.db_user,
                "db_password": db_password,
                "sql_member": f"databases/{safe_name}.sql",
                "website_domain": domain_of.get(item.website_id, ""),
            }
            sql_files[item.db_name] = sql_path
            database_entries[item.id] = entry
            manifest["databases"].append(entry)

        for website in websites:
            site_entry = {
                "domain": website.domain,
                "php_version": website.php_version,
                "app_type": website.app_type or "wordpress",
                "status": website.status or "active",
                "document_root": getattr(website, "document_root", "public_html") or "public_html",
                "nginx_custom": website.nginx_custom or "",
                "nginx_config_mode": "managed",
                "nginx_rewrite_mode": getattr(website, "nginx_rewrite_mode", "none") or "none",
                "waf_enabled": bool(website.waf_enabled),
                "waf_default_rules": website.waf_default_rules or "",
                "waf_custom_rules": website.waf_custom_rules or "",
                "aliases": [alias.domain for alias in getattr(website, "aliases", []) or [] if getattr(alias, "mode", "alias") == "alias"],
                "database": None,
                "ssl": None,
            }
            # Carry the certificate, not just the fact that there was one. A
            # restored site has to answer HTTPS as soon as it comes up: the new
            # box has no certbot account or renewal config for the domain, and
            # DNS usually still points at the old server, so re-issuing is not
            # something the restore can fall back on.
            if getattr(website, "ssl_enabled", False):
                try:
                    material = ssl_service.read_site_certificate(
                        website.domain,
                        mode=getattr(website, "ssl_mode", "") or "",
                        cert_path=getattr(website, "ssl_cert_path", None),
                        key_path=getattr(website, "ssl_key_path", None),
                        ca_path=getattr(website, "ssl_ca_path", None),
                        reuse_name=getattr(website, "ssl_reuse_name", None),
                    )
                except Exception as exc:
                    material = None
                    skipped.append({"path": f"ssl:{website.domain}", "reason": str(exc)})
                if material:
                    base = f"ssl/{website.domain}"
                    ssl_files[website.domain] = material
                    site_entry["ssl"] = {
                        "issued_as": getattr(website, "ssl_mode", "") or "manual",
                        "wildcard": bool(getattr(website, "ssl_wildcard", False)),
                        "member_cert": f"{base}/fullchain.pem",
                        "member_key": f"{base}/privkey.pem",
                        "member_ca": f"{base}/ca.pem" if material["ca_bundle"] else "",
                    }
                else:
                    skipped.append({
                        "path": f"ssl:{website.domain}",
                        "reason": "No readable certificate on disk",
                    })
            # Still recorded per site, so a panel running an older release can
            # restore this archive; the top-level list is what a current one
            # reads, because it also carries the databases no site points at.
            for item in db_items:
                if item.website_id == website.id and item.id in database_entries:
                    site_entry["database"] = database_entries[item.id]
                    break
            manifest["websites"].append(site_entry)

        manifest_path = tmp_dir / BACKUP_MANIFEST
        # Build alongside the target and swap it in only once the archive closed
        # cleanly. Opening the destination directly would truncate it up front,
        # so a run that died halfway -- a full disk, a killed process -- would
        # leave a stub in the slot with last week's good copy already gone.
        staged = tmp_dir / "archive.tar.gz"
        with tarfile.open(staged, "w:gz") as tar:
            # First member, always. A .tar.gz is read sequentially, so anything
            # that wants the manifest -- the restore list describes every
            # archive it offers -- has to decompress up to wherever it sits.
            # It was briefly written last, to carry the skipped list, and that
            # cost 13 seconds per 2.4 GB archive just to name its owner.
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2), encoding="utf-8")
            tar.add(manifest_path, arcname=BACKUP_MANIFEST)
            for position, website in enumerate(websites, start=1):
                if on_progress:
                    on_progress(position - 1, len(websites), website.domain)
                root = Path(website.root_path)
                if root.exists():
                    # Within one site the bar moves on bytes, because most
                    # accounts own exactly one and site boundaries alone would
                    # leave it at zero for the whole run.
                    total_bytes = _tree_size(root) if on_progress else 0
                    step = max(1, total_bytes // 200)   # ~200 updates, no more
                    next_at = step

                    def report(written, _p=position, _t=total_bytes):
                        nonlocal next_at
                        if written < next_at:
                            return
                        next_at = written + step
                        on_progress(_p - 1 + min(1.0, written / _t), len(websites),
                                    website.domain)

                    _add_tree(tar, root, f"sites/{website.domain}/site", skipped,
                              on_bytes=report if (on_progress and total_bytes) else None)
            # Named for the database, not for a site: one account can own more
            # databases than it has websites, and more than one per site.
            for entry in manifest["databases"]:
                sql_path = sql_files.get(entry["db_name"])
                if sql_path and sql_path.exists():
                    tar.add(sql_path, arcname=entry["sql_member"])
            for site_entry in manifest["websites"]:
                material = ssl_files.get(site_entry["domain"])
                if not material or not site_entry.get("ssl"):
                    continue
                for member, part in (
                    (site_entry["ssl"]["member_cert"], material["certificate"]),
                    (site_entry["ssl"]["member_key"], material["private_key"]),
                    (site_entry["ssl"]["member_ca"], material["ca_bundle"]),
                ):
                    if member and part:
                        _add_bytes(tar, member, part, mode=0o600)
            # What the walk had to leave out is only known once it is done, so
            # it goes in its own member at the end rather than dragging the
            # manifest down there with it. The gap still travels with the
            # archive; only whoever asks for it pays to reach it.
            gaps = tmp_dir / BACKUP_SKIPPED
            gaps.write_text(json.dumps(skipped, ensure_ascii=True, indent=2), encoding="utf-8")
            tar.add(gaps, arcname=BACKUP_SKIPPED)
        os.replace(staged, archive)
    if on_progress:
        on_progress(len(websites), len(websites), "")
    if skipped:
        logger.warning("Backup of user %s skipped %d unreadable path(s): %s",
                       user.username, len(skipped),
                       ", ".join(item["path"] for item in skipped[:5]))
    return str(archive)


def list_user_backups(username: str) -> List[str]:
    backup_dir = _user_backup_dir(username)
    if settings.command_dry_run or not backup_dir.exists():
        return []
    # Newest first, by modification time. Sorting on the name only ever worked
    # because every name carried a timestamp; a rotation slot is named for its
    # weekday, so alphabetical order says nothing about age -- and mixed with
    # the old names it put every "user-<name>-<stamp>" ahead of every rotation
    # file, which made the prune below delete the backup just taken.
    paths = [path for path in backup_dir.glob("*.tar.gz") if path.is_file()]
    paths.sort(key=lambda path: (path.stat().st_mtime, path.name), reverse=True)
    return [str(path) for path in paths]


def list_uploaded_user_backups(username: Optional[str] = None) -> List[str]:
    backup_dirs = [_user_restore_dir(),
                   # Two folders older releases wrote uploads into.
                   Path(settings.backup_root) / "users" / "restore",
                   Path(settings.backup_root) / "users" / "uploads"]
    if settings.command_dry_run:
        return []
    items = []
    seen = set()
    for backup_dir in backup_dirs:
        if not backup_dir.exists():
            continue
        for path in sorted(backup_dir.glob("*.tar.gz"), reverse=True):
            if path in seen:
                continue
            if username:
                try:
                    manifest = read_backup_manifest(str(path))
                except Exception:
                    continue
                if (manifest.get("user") or {}).get("username") != username:
                    continue
            seen.add(path)
            items.append(str(path))
    return items


def describe_user_backup(backup_file: str) -> dict:
    path = user_backup_path(backup_file)
    item = {
        "backup_file": str(path),
        "filename": path.name,
        "size": path.stat().st_size,
        "username": "",
        "generated_at": "",
        "websites": 0,
        "valid": False,
        "error": "",
    }
    try:
        manifest = read_backup_manifest(str(path))
        item["valid"] = manifest.get("kind") in RESTORABLE_BACKUP_KINDS
        item["username"] = (manifest.get("user") or {}).get("username") or ""
        item["generated_at"] = manifest.get("generated_at") or ""
        item["websites"] = len(manifest.get("websites") or [])
        if not item["valid"]:
            item["error"] = "This is not a full user backup"
    except Exception as exc:
        item["error"] = str(exc)
    return item


def list_user_restore_backups() -> list[dict]:
    backup_dir = _user_restore_dir()
    if settings.command_dry_run or not backup_dir.exists():
        return []
    return [describe_user_backup(str(path)) for path in sorted(backup_dir.glob("*.tar.gz"), reverse=True)]


# Describing an archive means finding its manifest, and in one written before
# the manifest moved to the front that is a full decompress -- 159 seconds for
# seventeen archives on the production box. Keyed on identity, not just path,
# so a rotation slot that was overwritten is described again.
_describe_cache: dict = {}


def _archive_identity(path: Path):
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path), int(stat.st_mtime), stat.st_size)


def list_all_restorable_backups() -> list[dict]:
    """Every full-user archive on the box, uploaded or made here.

    Reads no archive. The account comes from the folder it sits in, which is
    what put it there, and the rest -- how many sites, whether the manifest is
    even valid -- is left to describe_backups() so the screen can paint before
    anything is decompressed. An archive written before the manifest moved to
    the first member costs a full decompress to describe, and there is no
    reason to pay that just to list a filename.
    """
    if settings.command_dry_run:
        return []
    seen: set = set()
    items: list[dict] = []

    def _add(path: Path, source: str, account: str = "") -> None:
        resolved = str(path)
        if resolved in seen:
            return
        seen.add(resolved)
        try:
            stat = path.stat()
            size, modified = stat.st_size, datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z"
        except OSError:
            size, modified = 0, ""
        cached = _describe_cache.get(_archive_identity(path))
        items.append({
            "backup_file": resolved,
            "filename": path.name,
            "size": size,
            "modified_at": modified,
            "source": source,
            "account": account,
            # Unknown until described; None is not the same claim as 0 or False.
            "username": (cached or {}).get("username", account),
            "websites": (cached or {}).get("websites"),
            "valid": (cached or {}).get("valid"),
            "generated_at": (cached or {}).get("generated_at", ""),
            "error": (cached or {}).get("error", ""),
        })

    restore_dir = _user_restore_dir()
    if restore_dir.exists():
        for path in sorted(restore_dir.glob("*.tar.gz")):
            _add(path, "uploaded")

    users_root = Path(settings.backup_root) / "users"
    if users_root.exists():
        for account_dir in sorted(users_root.iterdir()):
            if not account_dir.is_dir() or account_dir.name in {"restore", "uploads"}:
                continue
            for path in sorted(account_dir.glob("*.tar.gz")):
                _add(path, "account", account_dir.name)

    items.sort(key=lambda row: (row.get("account") or "", row.get("modified_at") or ""), reverse=True)
    return items


def describe_backups(backup_files: list[str]) -> list[dict]:
    """Open these archives and say what is in them.

    The slow half of the restore list, asked for separately and cached, so the
    cost is paid once per archive rather than on every visit to the page.
    """
    results = []
    for backup_file in backup_files:
        try:
            path = user_backup_path(backup_file)
        except FileNotFoundError:
            results.append({"backup_file": backup_file, "valid": False, "error": "Backup not found"})
            continue
        identity = _archive_identity(path)
        cached = _describe_cache.get(identity)
        if cached is None:
            try:
                cached = describe_user_backup(str(path))
            except Exception as exc:  # a half-written or foreign archive
                cached = {"backup_file": str(path), "filename": path.name, "username": "",
                          "generated_at": "", "websites": 0, "valid": False, "error": str(exc)}
            if identity:
                _describe_cache[identity] = cached
                if len(_describe_cache) > 500:
                    _describe_cache.pop(next(iter(_describe_cache)))
        results.append({**cached, "backup_file": str(path)})
    return results


def user_backup_path(backup_file: str) -> Path:
    backup_root = Path(settings.backup_root).resolve()
    path = Path(backup_file).resolve()
    if backup_root != path and backup_root not in path.parents:
        raise FileNotFoundError("Backup not found")
    if not path.exists() or not path.is_file() or path.suffixes[-2:] != [".tar", ".gz"]:
        raise FileNotFoundError("Backup not found")
    return path


def delete_user_backup(backup_file: str) -> str:
    path = user_backup_path(backup_file)
    path.unlink()
    return str(path)


def delete_user_restore_backup(backup_file: str) -> str:
    path = user_backup_path(backup_file)
    restore_dir = _user_restore_dir().resolve()
    if restore_dir not in path.parents:
        raise FileNotFoundError("Backup not found")
    path.unlink()
    return str(path)


def discard_local_copy(archive: str) -> None:
    """Remove an archive that has just been uploaded off-server.

    A backup sent to S3 or SFTP used to stay on this disk as well, so a
    schedule with a destination kept a week of full-account archives in both
    places -- tens of gigabytes on the very disk the destination was meant to
    spare. Call this only once the upload has succeeded: when it fails, the
    local archive is the one copy there is, and it stays.
    """
    try:
        user_backup_path(archive).unlink()
    except FileNotFoundError:
        pass


def prune_user_backups(username: str, keep: int) -> None:
    """Hold the scheduled set at ``keep``. Manual slots are not counted.

    They bound themselves at seven, and letting them share a schedule's budget
    means one evicts the other -- most likely deleting the copy somebody took
    by hand right before a risky change.
    """
    keep = max(int(keep or 1), 1)
    scheduled = [path for path in list_user_backups(username) if not is_manual_slot(path)]
    for old_backup in scheduled[keep:]:
        Path(old_backup).unlink(missing_ok=True)


def read_backup_skipped(backup_file: str) -> list:
    """What the backup could not read, if the archive records it.

    Its own member at the tail of the archive, so reaching it costs a full
    decompress -- only ask when the answer is wanted.
    """
    archive = user_backup_path(backup_file)
    with tarfile.open(archive, "r:gz") as tar:
        try:
            member = tar.getmember(BACKUP_SKIPPED)
        except KeyError:
            return []
        if not member.isfile() or member.size > 2 * 1024 * 1024:
            return []
        source = tar.extractfile(member)
        if source is None:
            return []
        try:
            return json.loads(source.read().decode("utf-8"))
        except ValueError:
            return []


def read_backup_manifest(backup_file: str) -> dict:
    """The manifest, read without walking the rest of the archive.

    getmember() builds the full member index first, which means decompressing
    the whole file however early the manifest sits -- 13 seconds on a 2.4 GB
    archive, paid once per archive by the restore list. Iterating the TarFile
    yields members as it reaches them, so writing the manifest first (see
    create_user_backup) and stopping there costs one member.
    """
    archive = user_backup_path(backup_file)
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar:
            if member.name != BACKUP_MANIFEST:
                continue
            if member.size > 2 * 1024 * 1024:
                raise ValueError("Backup manifest is too large")
            source = tar.extractfile(member)
            if source is None:
                raise ValueError("Backup manifest cannot be read")
            return json.loads(source.read().decode("utf-8"))
    raise ValueError("Backup manifest not found")


def _safe_extract_prefix(archive: Path, prefix: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    prefix = prefix.strip("/")
    with tarfile.open(archive, "r:gz") as tar:
        for original in tar.getmembers():
            if original.name == prefix:
                continue
            if not original.name.startswith(prefix + "/"):
                continue
            if original.islnk():
                continue
            original.name = original.name[len(prefix) + 1:]
            if not original.name:
                continue

            def safe_filter(member: tarfile.TarInfo, dest_path: str):
                if member.islnk():
                    return None
                return tarfile.data_filter(member, dest_path)

            try:
                tar.extract(original, path=str(destination), filter=safe_filter)
            except TypeError:
                member_path = (destination / original.name).resolve()
                if destination != member_path and destination not in member_path.parents:
                    raise ValueError("Backup archive contains unsafe paths")
                if original.issym():
                    link_path = (member_path.parent / original.linkname).resolve()
                    if destination != link_path and destination not in link_path.parents:
                        raise ValueError("Backup archive contains unsafe links")
                tar.extract(original, str(destination))


def _extract_member_to_file(archive: Path, member_name: str, output_dir: Path) -> Optional[Path]:
    with tarfile.open(archive, "r:gz") as tar:
        try:
            member = tar.getmember(member_name)
        except KeyError:
            return None
        if not member.isfile() or member.size > MAX_UPLOAD_BYTES:
            raise ValueError("Invalid SQL backup member")
        source = tar.extractfile(member)
        if source is None:
            return None
        target = output_dir / Path(member_name).name
        with target.open("wb") as output:
            while chunk := source.read(1024 * 1024):
                output.write(chunk)
        return target


def _read_member_bytes(archive: Path, member_name: str) -> bytes:
    """Read a small member straight into memory.

    Certificate material never touches a temp file on the way out of the
    archive, so a private key is not left lying in a directory somewhere if the
    restore fails partway.
    """
    if not member_name:
        return b""
    with tarfile.open(archive, "r:gz") as tar:
        try:
            member = tar.getmember(member_name)
        except KeyError:
            return b""
        if not member.isfile() or member.size > MAX_SSL_MEMBER_BYTES:
            raise ValueError(f"Invalid certificate member: {member_name}")
        source = tar.extractfile(member)
        return source.read() if source else b""


def _preflight_restore(db, manifest: dict, user, owned_databases: list) -> None:
    """Reject an archive before any privileged side effect happens.

    restore_user_backup performs every irreversible step -- Linux account, site
    tree, MariaDB database with GRANT ALL, SQL import, certificate install,
    root-owned WAF include, root-owned vhost plus an OpenLiteSpeed restart --
    before its single db.commit(). Several of its own validations then fire
    partway through, and the only recovery anywhere is a SQLAlchemy rollback,
    which touches none of that. An archive whose first site was valid and whose
    second was not therefore left a live vhost, a root-owned WAF include, a
    database with GRANT ALL, installed key material and a Linux user, with zero
    rows in the panel database -- so nothing listed them, nothing could remove
    them through the panel, and remove_vhost was never called for that domain.
    It also let a crafted archive fail late on purpose to avoid leaving an
    auditable record of what it had installed.

    Every check here is one the main loop already performs; doing them up front
    is what makes the attacker-controllable failures happen before the first
    side effect rather than in the middle of them.
    """
    owner_id = user.id if user is not None else None
    seen_domains: set[str] = set()
    # Hostnames the archive itself claims. Two sites in one archive that both
    # claim a name -- as a primary domain, a www. variant or an alias -- used to
    # fail on the SECOND one, inside the loop, after the first site's Linux
    # account, database, certificate, WAF include and vhost were already live.
    seen_hostnames: set[str] = set()
    for site_info in manifest.get("websites") or []:
        domain = (site_info.get("domain") or "").strip().lower()
        if not site_users.DOMAIN_RE.fullmatch(domain):
            raise ValueError(f"Invalid domain in backup: {domain}")
        if domain in seen_domains:
            raise ValueError(f"Domain listed twice in backup: {domain}")
        seen_domains.add(domain)
        site_users.validate_document_root(site_info.get("document_root") or "public_html")

        existing = db.query(Website).filter(Website.domain == domain).first()
        if existing is not None and existing.owner_id != owner_id:
            raise ValueError(f"Domain already belongs to another account: {domain}")

        exclude_id = existing.id if existing is not None else None
        # An owner check on Website.domain alone is weaker than the rule
        # create_website applies. _hostname_conflicts also reserves each site's
        # www. variant and every alias, so without it an archive could claim
        # another account's alias -- no Website row carries that name -- and get
        # a second vhost serving the hijacked hostname under its own uid.
        if _hostname_conflicts(db, domain, exclude_website_id=exclude_id):
            raise ValueError(f"Hostname already belongs to another website: {domain}")
        for hostname in (domain, f"www.{domain}"):
            if hostname in seen_hostnames:
                raise ValueError(f"Hostname claimed twice in backup: {hostname}")
            seen_hostnames.add(hostname)

        for alias in site_info.get("aliases") or []:
            alias_domain = (alias or "").strip().lower()
            if not alias_domain or alias_domain == domain:
                continue
            if _hostname_conflicts(db, alias_domain, exclude_website_id=exclude_id):
                raise ValueError(
                    f"Alias domain already belongs to another website: {alias_domain}"
                )
            if alias_domain in seen_hostnames:
                raise ValueError(f"Hostname claimed twice in backup: {alias_domain}")
            seen_hostnames.add(alias_domain)

    # Check the database entries the restore will ACTUALLY use. The loop picks
    # `owned_databases` when the manifest carries a top-level list and the
    # per-site `database` entry otherwise, so validating only the former left
    # the other path entirely unchecked -- and an archive chooses which one it
    # ships.
    entries = list(owned_databases)
    if not entries:
        for site_info in manifest.get("websites") or []:
            site_db = site_info.get("database")
            if site_db:
                entries.append(site_db)

    for entry in entries:
        db_name = (entry.get("db_name") or "").strip()
        db_user = (entry.get("db_user") or "").strip()
        if not db_name or not db_user:
            continue
        if Path(db_name).name != db_name:
            raise ValueError(f"Invalid database name in backup: {db_name}")
        # By owner, never by website_id: website_id is nullable, and SQL
        # `website_id != <n>` does not match NULL, so a per-website comparison
        # is blind to exactly the standalone databases POST /api/databases
        # creates.
        clash = (
            db.query(DatabaseAccount)
            .filter(
                DatabaseAccount.db_name == db_name,
                DatabaseAccount.owner_id != owner_id,
            )
            .first()
        )
        if clash is not None:
            raise ValueError(f"Database name already belongs to another account: {db_name}")
        mariadb.assert_db_user_available(db, db_user, owner_id)


def restore_user_backup(backup_file: str, db, on_progress=None) -> dict:
    archive = user_backup_path(backup_file)
    manifest = read_backup_manifest(str(archive))
    if manifest.get("kind") not in RESTORABLE_BACKUP_KINDS:
        raise ValueError("This is not a full user backup")
    user_info = manifest.get("user") or {}
    username = user_info.get("username") or ""
    if not PANEL_USERNAME_RE.fullmatch(username):
        raise ValueError("Invalid user in backup")

    user = db.query(User).filter(User.username == username).first()

    # Reject the archive before anything privileged happens. ensure_panel_user
    # below creates a Linux account, so the preflight has to run ahead of it,
    # not just ahead of the site loop. `user` is None for an account that does
    # not exist yet, which means it cannot own any domain or database already
    # on the box -- so every collision is a conflict.
    owned_databases = manifest.get("databases") or []
    _preflight_restore(db, manifest, user, owned_databases)

    created_user = False
    if user is None:
        email = user_info.get("email") or f"{username}@users.opanel.invalid"
        if email.endswith(_PLACEHOLDER_EMAIL_SUFFIXES):
            email = f"{username}@users.opanel.invalid"
        # Neither the role nor the credential may come from the archive.
        # normalize_role maps "admin" (and the legacy "super_admin") to
        # Role.admin, so an archive naming an unused username and role "admin"
        # used to create a panel administrator whose bcrypt hash -- and so
        # whose password -- its author chose, with no TOTP and immediately
        # usable at /api/auth/login. A panel admin reaches root through the
        # sudoers grant on opanel-helper, which carries no argument
        # restriction. RESTORABLE_BACKUP_KINDS deliberately accepts
        # "bpanel_user", a foreign panel's export, so archive bytes are
        # third-party data; the operator authorises "restore this account", not
        # "make this archive's author an administrator", and describe_backups
        # never showed them the requested role.
        #
        # da_import._process_archive already does exactly this: role is
        # hardcoded to end_user and the password is generated server-side.
        restored_password = secrets.token_urlsafe(18)
        user = User(
            username=username,
            email=email,
            hashed_password=hash_password(restored_password),
            role=Role.end_user.value,
            is_active=bool(user_info.get("is_active", True)),
            website_limit=int(user_info.get("website_limit") or 5),
            storage_limit_mb=int(user_info.get("storage_limit_mb") or 1024),
        )
        db.add(user)
        db.flush()
        created_user = True
    site_users.ensure_panel_user(user.username)

    restored_websites = []
    restored_databases = []
    restored_certificates: list = []
    ssl_warnings: list = []
    website_by_domain: dict = {}
    # Archives written before databases were backed up by ownership carry them
    # only per site; newer ones carry every database the account owned, so the
    # per-site path below must stand down or each one would import twice.
    # (owned_databases is resolved above, before the preflight.)
    with tempfile.TemporaryDirectory(prefix="opanel-user-restore-") as tmp:
        tmp_dir = Path(tmp)
        site_entries = manifest.get("websites") or []
        for site_position, site_info in enumerate(site_entries, start=1):
            if on_progress:
                on_progress(site_position - 1, len(site_entries),
                            (site_info.get("domain") or "").strip().lower())
            domain = (site_info.get("domain") or "").strip().lower()
            if not site_users.DOMAIN_RE.fullmatch(domain):
                raise ValueError(f"Invalid domain in backup: {domain}")
            php_version = site_info.get("php_version") or settings.default_php_version
            app_type = site_info.get("app_type") or "wordpress"
            if app_type not in {"wordpress", "php", "static"}:
                app_type = "wordpress"
            nginx_rewrite_mode = site_info.get("nginx_rewrite_mode")
            if nginx_rewrite_mode not in {"none", "front_controller", "laravel", "codeigniter", "seohburl"}:
                nginx_rewrite_mode = "front_controller" if app_type in {"wordpress", "php"} else "none"
            document_root = site_users.validate_document_root(site_info.get("document_root") or "public_html")
            backup_aliases = [
                (alias or "").strip().lower()
                for alias in (site_info.get("aliases") or [])
                if (alias or "").strip()
            ]
            backup_aliases = sorted({alias for alias in backup_aliases if alias != domain})
            linux_user = site_users.linux_user_for_panel_username(user.username)
            root_path = site_users.site_root_for_panel_user(user.username, domain)
            runtime_php_version = php_version if app_type in {"wordpress", "php"} else None
            site_users.ensure_site_runtime(domain, root_path, runtime_php_version, linux_user)
            _safe_extract_prefix(archive, f"sites/{domain}/site", Path(root_path).resolve())
            # Backups from older opanel releases may contain public/. Normalize
            # the document root after extraction before rewriting the vhost.
            site_users.ensure_site_runtime(domain, root_path, runtime_php_version, linux_user)
            site_users.ensure_document_root(root_path, document_root, linux_user)

            website = db.query(Website).filter(Website.domain == domain).first()
            # A domain already on the box must belong to the account being
            # restored. The lookup used to be by domain alone, and the update
            # branch below then rewrote owner_id, root_path, linux_user and the
            # WAF columns -- so an archive naming someone else's domain took
            # the site over, deleted the aliases it did not list, repointed its
            # database row, and had rewrite_vhost serve the archive's content
            # under the archive account's uid. The alias loop below and the
            # owned-database loop further down both already refuse a conflict;
            # so does websites.create_website. This is the same rule.
            if website is not None and website.owner_id != user.id:
                raise ValueError(
                    f"Domain already belongs to another account: {domain}"
                )
            if _hostname_conflicts(
                db, domain, exclude_website_id=website.id if website else None
            ):
                raise ValueError(f"Hostname already belongs to another website: {domain}")
            created_site = False
            if website is None:
                website = Website(
                    domain=domain,
                    owner_id=user.id,
                    root_path=root_path,
                    document_root=document_root,
                    linux_user=linux_user,
                    php_version=php_version,
                    app_type=app_type,
                    ssl_enabled=False,
                    status=site_info.get("status") or "active",
                    nginx_custom=site_info.get("nginx_custom") or "",
                    nginx_config_mode="managed",
                    nginx_rewrite_mode=nginx_rewrite_mode,
                    waf_enabled=bool(site_info.get("waf_enabled", True)),
                    waf_default_rules=site_info.get("waf_default_rules") or "",
                    # Never from the archive. api/waf.py refuses any change to
                    # custom_rules from a non-admin and says why in source:
                    # these are raw ModSecurity directives, actions can run
                    # programs, and a rule that fails to parse can stop the
                    # server coming back up. The restore applied them with no
                    # author check at all, and render_site_rules appends them
                    # last, after the base include -- so a trailing
                    # "SecRuleEngine Off" silently disabled the WAF for that
                    # site. waf_default_rules is a catalogue selection, not
                    # directives, so it is safe to carry.
                    waf_custom_rules="",
                )
                db.add(website)
                db.flush()
                created_site = True
            else:
                website.owner_id = user.id
                website.root_path = root_path
                website.document_root = document_root
                website.linux_user = linux_user
                website.php_version = php_version
                website.app_type = app_type
                website.status = site_info.get("status") or "active"
                website.nginx_custom = site_info.get("nginx_custom") or ""
                website.nginx_config_mode = "managed"
                website.nginx_rewrite_mode = nginx_rewrite_mode
                website.waf_enabled = bool(site_info.get("waf_enabled", True))
                website.waf_default_rules = site_info.get("waf_default_rules") or ""
                # waf_custom_rules is deliberately left as it is: whatever this
                # row already holds was authored by an admin through
                # PATCH /websites/{id}/waf, and the archive is not an admin.
                db.flush()

            existing_aliases = {
                alias.domain: alias
                for alias in db.query(WebsiteAlias).filter(WebsiteAlias.website_id == website.id).all()
            }
            for alias_domain in backup_aliases:
                if _hostname_conflicts(db, alias_domain, exclude_website_id=website.id):
                    raise ValueError(f"Alias domain already belongs to another website: {alias_domain}")
                if alias_domain not in existing_aliases:
                    db.add(WebsiteAlias(website_id=website.id, domain=alias_domain, mode="alias"))
            for alias_domain, alias_obj in existing_aliases.items():
                if alias_domain not in backup_aliases:
                    db.delete(alias_obj)

            db_info = None if owned_databases else (site_info.get("database") or None)
            if db_info:
                db_name = db_info.get("db_name")
                db_user = db_info.get("db_user")
                db_password = db_info.get("db_password") or mariadb.random_password()
                # By owner. `website_id != website.id` cannot match a NULL
                # website_id, which is what every standalone database has, so
                # this check used to be blind to the rows most worth protecting.
                conflict = db.query(DatabaseAccount).filter(
                    DatabaseAccount.db_name == db_name,
                    DatabaseAccount.owner_id != user.id,
                ).first()
                if conflict:
                    raise ValueError(f"Database name already belongs to another account: {db_name}")
                mariadb.assert_db_user_available(db, db_user, user.id)
                mariadb.create_database_credentials(db_name, db_user, db_password, allow_existing=True)
                sql_member = db_info.get("sql_member") or f"databases/{domain}.sql"
                sql_path = _extract_member_to_file(archive, sql_member, tmp_dir)
                if sql_path:
                    mariadb.import_database(db_name, str(sql_path))
                db_account = db.query(DatabaseAccount).filter(DatabaseAccount.website_id == website.id).first()
                if db_account is None:
                    db.add(DatabaseAccount(
                        owner_id=website.owner_id,
                        website_id=website.id,
                        db_name=db_name,
                        db_user=db_user,
                        db_password=encrypt(db_password),
                    ))
                else:
                    db_account.owner_id = website.owner_id
                    db_account.db_name = db_name
                    db_account.db_user = db_user
                    db_account.db_password = encrypt(db_password)

            # Put the certificate back before the vhost is written, so the site
            # comes up on HTTPS in the same pass rather than waiting for someone
            # to notice and re-issue.
            ssl_kwargs: dict = {}
            ssl_info = site_info.get("ssl") or None
            if ssl_info:
                try:
                    certificate = _read_member_bytes(archive, ssl_info.get("member_cert") or "")
                    private_key = _read_member_bytes(archive, ssl_info.get("member_key") or "")
                    ca_bundle = _read_member_bytes(archive, ssl_info.get("member_ca") or "") or b""
                    if not certificate or not private_key:
                        raise ValueError("certificate material missing from the archive")
                    paths = ssl_service.install_site_certificate(
                        domain, certificate, private_key, ca_bundle
                    )
                    website.ssl_enabled = True
                    # Manual whatever it was issued as: this box has no renewal
                    # config for it, so a vhost pointing into /etc/letsencrypt
                    # would point at nothing. Let's Encrypt can take over once
                    # DNS moves; until then the site is served, not broken.
                    website.ssl_mode = "manual"
                    website.ssl_cert_path = paths.get("cert")
                    website.ssl_key_path = paths.get("key")
                    website.ssl_ca_path = paths.get("ca")
                    website.ssl_updated_at = datetime.utcnow()
                    ssl_kwargs = {
                        "ssl_enabled": True,
                        "ssl_cert_path": paths.get("cert"),
                        "ssl_key_path": paths.get("key"),
                    }
                    expiry = ssl_service.certificate_expiry(certificate)
                    restored_certificates.append({
                        "domain": domain,
                        "issued_as": ssl_info.get("issued_as") or "",
                        "expires": expiry.isoformat() if expiry else "",
                        "expired": bool(expiry and expiry < datetime.now(timezone.utc)),
                    })
                except Exception as exc:
                    # A site on plain HTTP is recoverable; a failed restore is
                    # not. Say which domain and why, and carry on.
                    website.ssl_enabled = False
                    website.ssl_mode = "none"
                    ssl_warnings.append(f"{domain}: {exc}")

            result = waf.sync_website_rules(website)
            if result.returncode != 0:
                raise RuntimeError((result.stderr or result.stdout or "Could not write WAF rules").strip())
            openlitespeed.rewrite_vhost(
                domain,
                root_path,
                app_type=app_type,
                php_version=php_version,
                custom_directives="",
                linux_user=linux_user,
                lsphp_socket_override=site_users.site_lsphp_socket(linux_user, root_path, runtime_php_version),
                waf_enabled=website.waf_enabled,
                document_root=document_root,
                rewrite_mode=nginx_rewrite_mode,
                aliases=backup_aliases,
                **ssl_kwargs,
            )
            wordpress.fix_permissions(root_path, linux_user)
            website_by_domain[domain] = website
            restored_websites.append({"domain": domain, "created": created_site})

        # Databases last: one can only be attached to a site that exists. A
        # database with no site of its own is restored all the same -- it is
        # the account's, and whatever uses it says so in its own source.
        for entry in owned_databases:
            db_name = (entry.get("db_name") or "").strip()
            db_user = (entry.get("db_user") or "").strip()
            if not db_name or not db_user:
                continue
            if Path(db_name).name != db_name:
                raise ValueError(f"Invalid database name in backup: {db_name}")
            db_password = entry.get("db_password") or mariadb.random_password()
            website = website_by_domain.get((entry.get("website_domain") or "").strip().lower())
            conflict = db.query(DatabaseAccount).filter(
                DatabaseAccount.db_name == db_name,
                DatabaseAccount.owner_id != user.id,
            ).first()
            if conflict:
                raise ValueError(f"Database name already belongs to another account: {db_name}")
            mariadb.assert_db_user_available(db, db_user, user.id)
            mariadb.create_database_credentials(db_name, db_user, db_password, allow_existing=True)
            sql_path = _extract_member_to_file(
                archive, entry.get("sql_member") or f"databases/{db_name}.sql", tmp_dir
            )
            if sql_path:
                mariadb.import_database(db_name, str(sql_path))
            account = db.query(DatabaseAccount).filter(DatabaseAccount.db_name == db_name).first()
            if account is None:
                db.add(DatabaseAccount(
                    owner_id=user.id,
                    website_id=website.id if website else None,
                    db_name=db_name,
                    db_user=db_user,
                    db_password=encrypt(db_password),
                ))
            else:
                account.owner_id = user.id
                if website is not None:
                    account.website_id = website.id
                account.db_user = db_user
                account.db_password = encrypt(db_password)
            restored_databases.append(db_name)

    db.commit()
    return {
        "created_user": created_user,
        "username": username,
        "websites": restored_websites,
        "databases": restored_databases,
        "certificates": restored_certificates,
        "ssl_warnings": ssl_warnings,
    }


def save_uploaded_backup(domain: str, filename: str, source_file) -> str:
    backup_dir = _site_backup_dir(domain).resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    if not safe_name.endswith(".tar.gz"):
        raise ValueError("Only .tar.gz backup files are supported")
    target = (backup_dir / safe_name).resolve()
    if backup_dir not in target.parents:
        raise ValueError("Invalid backup filename")
    written = 0
    with target.open("wb") as buffer:
        while chunk := source_file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                target.unlink(missing_ok=True)
                raise ValueError("Backup file is too large")
            buffer.write(chunk)
    return str(target)


def save_uploaded_user_backup(filename: str, source_file) -> str:
    backup_dir = _user_restore_dir().resolve()
    backup_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name
    if not safe_name.endswith(".tar.gz"):
        raise ValueError("Only .tar.gz backup files are supported")
    target = (backup_dir / safe_name).resolve()
    if backup_dir not in target.parents:
        raise ValueError("Invalid backup filename")
    if target.exists():
        stem = safe_name[:-7]
        suffix = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        target = (backup_dir / f"{stem}-{suffix}-{secrets.token_hex(3)}.tar.gz").resolve()
    written = 0
    with target.open("wb") as buffer:
        while chunk := source_file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                target.unlink(missing_ok=True)
                raise ValueError("Backup file is too large")
            buffer.write(chunk)
    return str(target)


def archive_has_database(website: Website, backup_file: str) -> bool:
    """Whether the archive carries a SQL dump at all."""
    archive = backup_path(website.domain, backup_file)
    with tarfile.open(archive, "r:gz") as tar:
        return any(m.name.startswith("database/") and m.name.endswith(".sql") for m in tar.getmembers())


def restore_backup_database(
    website: Website,
    backup_file: str,
    db_name: str,
    *,
    db_user: str | None = None,
    db_password: str | None = None,
) -> bool:
    """Import the SQL dump held in a website backup.

    restore_backup() deliberately skips the database/ member, so for a long time
    the dump the backup had faithfully captured could not be put back from the
    panel at all -- the only copy sat unreachable inside the .tar.gz.
    """
    archive = backup_path(website.domain, backup_file)
    with tempfile.TemporaryDirectory(prefix="opanel-db-restore-") as tmp_dir:
        sql_path = None
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar.getmembers():
                if not (member.isfile() and member.name.startswith("database/")
                        and member.name.endswith(".sql")):
                    continue
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                sql_path = Path(tmp_dir) / "restore.sql"
                with open(sql_path, "wb") as handle:
                    shutil.copyfileobj(extracted, handle)
                break
        if sql_path is None:
            return False
        # Scoped to the schema's own account: this route is reachable by any
        # end_user who owns the site, and the archive -- including its SQL
        # member -- can be one they uploaded themselves.
        mariadb.import_database(
            db_name, str(sql_path), as_user=db_user, as_password=db_password
        )
        return True


def restore_backup(website: Website, backup_file: str) -> str:
    archive = backup_path(website.domain, backup_file)
    destination = Path(website.root_path).resolve()

    if website.linux_user:
        if settings.command_dry_run:
            return str(destination)
        shell.privileged(
            "site-backup-restore",
            helper_args=[
                website.linux_user,
                website.root_path,
                str(archive),
                str(SITE_RESTORE_MAX_ITEMS),
                str(SITE_RESTORE_MAX_BYTES),
            ],
        )
        return str(destination)

    # Single-pass extraction with PEP 706 data filter (Python 3.12+).
    # The data filter rejects path traversal, absolute paths, and unsafe
    # symlinks at the tarfile layer itself.
    with tarfile.open(archive, "r:gz") as tar:
        members = list(tar.getmembers())
        has_site_prefix = any(m.name == "site" or m.name.startswith("site/") for m in members)

        def safe_filter(member: tarfile.TarInfo, dest_path: str):
            # Hard-links inside backups are uncommon and risky; refuse outright.
            if member.islnk():
                return None
            if member.name.startswith("database/"):
                return None
            if has_site_prefix:
                if member.name == "site":
                    return None
                if not member.name.startswith("site/"):
                    return None
                member.name = member.name[len("site/"):]
            return tarfile.data_filter(member, dest_path)

        try:
            tar.extractall(path=str(destination), filter=safe_filter)
        except TypeError:
            # Older Python (<3.12) without the filter parameter Ã¢â‚¬â€ fall back to
            # manual extraction with the existing safety check.
            _ensure_safe_tar(archive, destination)
            for member in members:
                if member.name.startswith("database/"):
                    continue
                if has_site_prefix:
                    if member.name == "site":
                        continue
                    if not member.name.startswith("site/"):
                        continue
                    member.name = member.name[len("site/"):]
                tar.extract(member, str(destination))
    return str(destination)


def backup_path(domain: str, backup_file: str) -> Path:
    backup_root = _site_backup_dir(domain).resolve()
    path = Path(backup_file).resolve()
    if backup_root not in path.parents or not path.exists() or path.suffixes[-2:] != [".tar", ".gz"] or not path.is_file():
        raise FileNotFoundError("Backup not found")
    return path


def _ensure_safe_tar(archive: Path, destination: Path) -> None:
    with tarfile.open(archive, "r:gz") as tar:
        for member in tar.getmembers():
            member_path = (destination / member.name).resolve()
            if destination != member_path and destination not in member_path.parents:
                raise ValueError("Backup archive contains unsafe paths")
            if member.issym() or member.islnk():
                link_path = (member_path.parent / member.linkname).resolve()
                if destination != link_path and destination not in link_path.parents:
                    raise ValueError("Backup archive contains unsafe links")


def delete_backup(domain: str, backup_file: str) -> str:
    path = backup_path(domain, backup_file)
    path.unlink()
    return str(path)


def list_backups(domain: str) -> List[str]:
    backup_dir = _site_backup_dir(domain)
    if settings.command_dry_run:
        return []
    if not backup_dir.exists():
        return []
    return [str(path) for path in sorted(backup_dir.glob("*.tar.gz"), reverse=True)]


def _load_private_key(private_key: str, password: Optional[str] = None):
    key_stream = StringIO(private_key)
    # paramiko 5 dropped DSSKey: DSA is obsolete and OpenSSH has refused it by
    # default since 7.0. Look it up dynamically so a box still on paramiko 3.x
    # (mid-rollout) keeps accepting an old DSA key instead of raising
    # AttributeError at import time.
    key_classes = tuple(
        cls
        for cls in (
            paramiko.RSAKey,
            paramiko.ECDSAKey,
            paramiko.Ed25519Key,
            getattr(paramiko, "DSSKey", None),
        )
        if cls is not None
    )
    last_error = None
    for key_class in key_classes:
        key_stream.seek(0)
        try:
            return key_class.from_private_key(key_stream, password=password or None)
        except Exception as exc:  # pragma: no cover - depends on key type
            last_error = exc
    raise ValueError(f"Cannot load SFTP private key: {last_error}")


def _ensure_remote_dir(sftp, remote_dir: str) -> None:
    remote_dir = posixpath.normpath(remote_dir or ".")
    if remote_dir in {".", "/"}:
        return
    parts = [part for part in remote_dir.split("/") if part]
    current = "/" if remote_dir.startswith("/") else "."
    for part in parts:
        current = posixpath.join(current, part) if current != "/" else f"/{part}"
        try:
            sftp.stat(current)
        except FileNotFoundError:
            sftp.mkdir(current)


class SftpHostKeyMismatch(RuntimeError):
    """Raised when a pinned host key no longer matches the server."""


def _fingerprint(key: PKey) -> str:
    """SHA256:base64 fingerprint, identical to OpenSSH ``ssh-keygen -lf``."""
    digest = hashlib.sha256(key.asbytes()).digest()
    import base64 as _b64

    return "SHA256:" + _b64.b64encode(digest).rstrip(b"=").decode("ascii")


class _PinnedHostKeyPolicy(paramiko.MissingHostKeyPolicy):
    """Paramiko policy that captures the server host key on first connect.

    When ``expected`` is ``None`` (TOFU bootstrap) the policy accepts the key
    and stores it on ``self.captured`` for the caller to persist. When
    ``expected`` is set the policy refuses to fall back here at all because
    the verification has already happened in the caller; this is purely a
    safety net to surface a clear error if logic upstream changes.
    """

    def __init__(self, expected_type: Optional[str], expected_fingerprint: Optional[str]):
        self.expected_type = expected_type
        self.expected_fingerprint = expected_fingerprint
        self.captured_type: Optional[str] = None
        self.captured_fingerprint: Optional[str] = None

    def missing_host_key(self, client, hostname, key):  # type: ignore[override]
        captured = _fingerprint(key)
        if self.expected_fingerprint:
            # Verification path: a key was pinned but Paramiko did not find a
            # matching entry. Refuse the connection.
            raise SftpHostKeyMismatch(
                f"SFTP host key mismatch for {hostname}: "
                f"expected {self.expected_type or '?'} {self.expected_fingerprint}, "
                f"got {key.get_name()} {captured}"
            )
        # TOFU bootstrap.
        self.captured_type = key.get_name()
        self.captured_fingerprint = captured
        logger.warning(
            "SFTP TOFU bootstrap: pinning %s host key %s %s",
            hostname,
            self.captured_type,
            self.captured_fingerprint,
        )


def upload_to_sftp(
    local_file: str,
    *,
    host: str,
    port: int,
    username: str,
    remote_path: str,
    password: Optional[str] = None,
    private_key: Optional[str] = None,
    expected_host_key_type: Optional[str] = None,
    expected_host_key_fingerprint: Optional[str] = None,
) -> dict:
    """Upload ``local_file`` to ``host:port`` via SFTP.

    Host key handling:
      * If a fingerprint is pinned, the server's key is compared against it
        before any auth bytes are sent. A mismatch raises
        :class:`SftpHostKeyMismatch`.
      * If no fingerprint is pinned yet (TOFU bootstrap), the server's key is
        captured and returned. The caller must persist it.

    Returns a dict ``{"remote_file": ..., "host_key_type": ...,
    "host_key_fingerprint": ...}``.
    """
    local_path = Path(local_file).resolve()
    if not local_path.exists() or not local_path.is_file():
        raise FileNotFoundError("Local backup file not found")
    if not password and not private_key:
        raise ValueError("SFTP password or private key is required")

    pkey = _load_private_key(private_key, password=password) if private_key else None
    remote_dir = posixpath.normpath(remote_path.strip() or ".")
    remote_file = posixpath.join(remote_dir, local_path.name)

    captured_type: Optional[str] = expected_host_key_type
    captured_fp: Optional[str] = expected_host_key_fingerprint

    client = paramiko.SSHClient()
    # Note: we deliberately do NOT call load_system_host_keys() because the
        # daemon runs as the opanel service user with no interactive shell
        # history; trusting its known_hosts blindly would defeat the pinning model.
    if expected_host_key_fingerprint:
        # Strict: the only acceptable key is the one pinned in the DB.
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        try:
            host_keys = client.get_host_keys()
            host_key_obj = _decode_pinned_key(
                expected_host_key_type or "", expected_host_key_fingerprint
            )
            if host_key_obj is not None:
                host_keys.add(host, expected_host_key_type or host_key_obj.get_name(), host_key_obj)
        except Exception:  # pragma: no cover - decoding fallback
            pass
        # Even if we could not pre-load the key (e.g. only fingerprint stored),
        # the missing_host_key policy below performs the comparison itself.
        client.set_missing_host_key_policy(
            _PinnedHostKeyPolicy(expected_host_key_type, expected_host_key_fingerprint)
        )
    else:
        # TOFU bootstrap path: capture the server key for the caller.
        client.set_missing_host_key_policy(
            _PinnedHostKeyPolicy(None, None)
        )

    try:
        client.connect(
            hostname=host,
            port=port,
            username=username,
            password=password if not pkey else None,
            pkey=pkey,
            timeout=20,
            banner_timeout=20,
            auth_timeout=20,
            allow_agent=False,
            look_for_keys=False,
        )
        # Verify the server key we actually negotiated against the pin. The
        # policy catches the "missing" case; this catches the case where the
        # key was already in the local known_hosts and the policy was not
        # called at all.
        transport = client.get_transport()
        if transport is None:
            raise SSHException("SFTP transport unavailable")
        server_key = transport.get_remote_server_key()
        server_fp = _fingerprint(server_key)
        server_type = server_key.get_name()
        if expected_host_key_fingerprint:
            if server_fp != expected_host_key_fingerprint:
                raise SftpHostKeyMismatch(
                    f"SFTP host key mismatch for {host}: "
                    f"expected {expected_host_key_type or '?'} {expected_host_key_fingerprint}, "
                    f"got {server_type} {server_fp}"
                )
            captured_type = expected_host_key_type or server_type
            captured_fp = expected_host_key_fingerprint
        else:
            captured_type = server_type
            captured_fp = server_fp
        with client.open_sftp() as sftp:
            _ensure_remote_dir(sftp, remote_dir)
            sftp.put(str(local_path), remote_file)
    finally:
        client.close()
    return {
        "remote_file": remote_file,
        "host_key_type": captured_type,
        "host_key_fingerprint": captured_fp,
    }


def _decode_pinned_key(key_type: str, fingerprint: str) -> Optional[PKey]:
    """We store only the fingerprint, so reconstructing a PKey is not always
    possible. Returns ``None`` to indicate the caller should rely on the
    in-policy fingerprint comparison instead."""
    return None


# ---------------------------------------------------------------------------
# S3-compatible object storage
# ---------------------------------------------------------------------------
# boto3 is imported lazily. It is a large dependency and nothing on the request
# path needs it, so a box that has not finished installing requirements yet
# still serves the panel instead of failing to import the backup module.

S3_KEY_RE = re.compile(r"^[A-Za-z0-9!_.*'()/-]*$")


class S3Error(RuntimeError):
    """Anything the object store refused, phrased for the panel."""


def _s3_client(
    *,
    endpoint: str,
    region: str,
    access_key: str,
    secret_key: str,
    use_path_style: bool,
):
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover - only on a half-installed box
        raise S3Error(
            "boto3 is not installed on this server. Run opanel-update to install it."
        ) from exc

    if not access_key or not secret_key:
        raise ValueError("S3 access key and secret key are required")

    endpoint = (endpoint or "").strip()
    if endpoint and not endpoint.startswith(("http://", "https://")):
        endpoint = f"https://{endpoint}"

    return boto3.client(
        "s3",
        endpoint_url=endpoint or None,
        region_name=(region or "us-east-1").strip(),
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(
            # Every S3-compatible store speaks v4 now, and several refuse v2.
            signature_version="s3v4",
            # MinIO and Ceph serve the bucket as a path rather than a subdomain.
            s3={"addressing_style": "path" if use_path_style else "auto"},
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=20,
            read_timeout=120,
        ),
    )


def s3_prefix_for(prefix: str) -> str:
    """Normalise a user-typed prefix into an S3 key prefix.

    S3 has no directories. A leading slash makes an object whose name starts
    with a slash, which then displays as an empty folder in every console, so
    strip it here rather than leaving each caller to remember.
    """
    cleaned = (prefix or "").strip().strip("/")
    if not cleaned:
        return ""
    if not S3_KEY_RE.match(cleaned):
        raise ValueError("S3 prefix may only contain letters, digits and ! _ . * ' ( ) / -")
    if ".." in cleaned.split("/"):
        raise ValueError("S3 prefix cannot contain ..")
    return cleaned


def _s3_key(prefix: str, filename: str) -> str:
    safe_prefix = s3_prefix_for(prefix)
    return f"{safe_prefix}/{filename}" if safe_prefix else filename


def _s3_failure(exc: Exception, bucket: str) -> S3Error:
    """Turn botocore's error shapes into something an admin can act on."""
    from botocore.exceptions import ClientError, EndpointConnectionError, NoCredentialsError

    if isinstance(exc, EndpointConnectionError):
        return S3Error("Could not reach the S3 endpoint. Check the endpoint URL.")
    if isinstance(exc, NoCredentialsError):
        return S3Error("S3 credentials were rejected as empty.")
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        code = str(error.get("Code", "")).strip()
        message = str(error.get("Message", "")).strip()
        known = {
            "NoSuchBucket": f"Bucket '{bucket}' does not exist.",
            "AccessDenied": "Access denied. Check the key's permissions on this bucket.",
            "InvalidAccessKeyId": "Access key not recognised by this endpoint.",
            "SignatureDoesNotMatch": "Secret key does not match the access key.",
            "PermanentRedirect": "Wrong region for this bucket.",
            "AuthorizationHeaderMalformed": "Wrong region for this bucket.",
            "401": "Credentials were rejected.",
            "403": "Access denied. Check the key's permissions on this bucket.",
            "404": f"Bucket '{bucket}' does not exist.",
        }
        if code in known:
            return S3Error(known[code])
        return S3Error(f"S3 error {code}: {message}" if code else f"S3 error: {message or exc}")
    return S3Error(str(exc))


def test_s3_target(
    *,
    endpoint: str,
    region: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    use_path_style: bool = False,
    prefix: str = "",
) -> dict:
    """Prove the credentials can actually write, not just connect.

    HeadBucket alone passes for a read-only key, which then fails at 2am on the
    first scheduled run. This writes a small object and deletes it again.
    """
    client = _s3_client(
        endpoint=endpoint, region=region, access_key=access_key,
        secret_key=secret_key, use_path_style=use_path_style,
    )
    probe_key = _s3_key(prefix, f".opanel-write-test-{int(time.time())}")
    try:
        client.head_bucket(Bucket=bucket)
        client.put_object(Bucket=bucket, Key=probe_key, Body=b"opanel write test")
        client.delete_object(Bucket=bucket, Key=probe_key)
    except Exception as exc:
        raise _s3_failure(exc, bucket) from exc
    return {"ok": True, "bucket": bucket, "prefix": s3_prefix_for(prefix)}


def upload_to_s3(
    local_file: str,
    *,
    endpoint: str,
    region: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    prefix: str = "",
    use_path_style: bool = False,
) -> dict:
    """Upload one archive. Multipart is handled by boto3's transfer manager,
    which matters: these files run to gigabytes and a single PUT caps at 5 GB."""
    local_path = Path(local_file).resolve()
    if not local_path.exists() or not local_path.is_file():
        raise FileNotFoundError("Local backup file not found")

    client = _s3_client(
        endpoint=endpoint, region=region, access_key=access_key,
        secret_key=secret_key, use_path_style=use_path_style,
    )
    key = _s3_key(prefix, local_path.name)
    try:
        client.upload_file(str(local_path), bucket, key)
    except Exception as exc:
        raise _s3_failure(exc, bucket) from exc

    return {
        "remote_file": f"s3://{bucket}/{key}",
        "bucket": bucket,
        "key": key,
        "size": local_path.stat().st_size,
    }


def list_s3_backups(
    *,
    endpoint: str,
    region: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    prefix: str = "",
    use_path_style: bool = False,
) -> list[dict]:
    client = _s3_client(
        endpoint=endpoint, region=region, access_key=access_key,
        secret_key=secret_key, use_path_style=use_path_style,
    )
    safe_prefix = s3_prefix_for(prefix)
    lookup = f"{safe_prefix}/" if safe_prefix else ""
    entries: list[dict] = []
    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=lookup):
            for item in page.get("Contents", []) or []:
                entries.append({
                    "key": item["Key"],
                    "size": int(item.get("Size", 0)),
                    "modified": item.get("LastModified"),
                })
    except Exception as exc:
        raise _s3_failure(exc, bucket) from exc
    entries.sort(key=lambda row: row["modified"] or 0, reverse=True)
    return entries


def download_from_s3(key: str, *, endpoint: str, region: str, bucket: str,
                     access_key: str, secret_key: str, use_path_style: bool = False,
                     destination_dir: str) -> str:
    """Pull one object into the restore folder.

    The name is taken from the key, not from anything the caller passes, and
    is reduced to a bare filename -- a key is remote input and must not be
    able to place a file outside the folder it was asked for.
    """
    safe_name = Path(str(key).replace("\\", "/")).name
    if not safe_name.endswith(".tar.gz"):
        raise S3Error("Only .tar.gz backups can be fetched")
    folder = Path(destination_dir).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    destination = (folder / safe_name).resolve()
    if folder not in destination.parents:
        raise S3Error("Invalid backup name")

    client = _s3_client(endpoint=endpoint, region=region, access_key=access_key,
                        secret_key=secret_key, use_path_style=use_path_style)
    partial = destination.with_suffix(destination.suffix + ".part")
    try:
        client.download_file(bucket, key, str(partial))
    except Exception as exc:  # noqa: BLE001
        partial.unlink(missing_ok=True)
        raise _s3_failure(exc) from exc
    # Swap in only once it is whole, so a half-downloaded archive is never
    # offered for restore.
    os.replace(partial, destination)
    return str(destination)


def prune_s3_backups(
    *,
    endpoint: str,
    region: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    prefix: str = "",
    use_path_style: bool = False,
    keep: int = 7,
    name_prefix: str = "",
) -> int:
    """Keep the newest ``keep`` archives under the prefix, delete the rest.

    Object storage bills for every byte kept, so unlike the SFTP target this
    one prunes the far end. ``name_prefix`` scopes a prune to one account's
    archives. It is anchored, not a substring: matching "user-acme-" loosely
    also caught user-acme2-*.tar.gz, so one account's retention deleted
    another account's backups.
    """
    if keep < 1:
        return 0
    entries = list_s3_backups(
        endpoint=endpoint, region=region, bucket=bucket, access_key=access_key,
        secret_key=secret_key, prefix=prefix, use_path_style=use_path_style,
    )
    if name_prefix:
        entries = [row for row in entries if row["key"].rsplit("/", 1)[-1].startswith(name_prefix)]
    # A manual slot bounds itself at seven and is not part of any schedule's
    # budget; counting it would let the two evict each other.
    entries = [row for row in entries if not is_manual_slot(row["key"])]
    stale = entries[keep:]
    if not stale:
        return 0

    client = _s3_client(
        endpoint=endpoint, region=region, access_key=access_key,
        secret_key=secret_key, use_path_style=use_path_style,
    )
    removed = 0
    try:
        # delete_objects takes 1000 keys at a time.
        for start in range(0, len(stale), 1000):
            chunk = stale[start:start + 1000]
            client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": [{"Key": row["key"]} for row in chunk], "Quiet": True},
            )
            removed += len(chunk)
    except Exception as exc:
        raise _s3_failure(exc, bucket) from exc
    return removed


def delete_s3_object(
    *,
    endpoint: str,
    region: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    key: str,
    prefix: str = "",
    use_path_style: bool = False,
) -> str:
    """Delete one object, and only one that belongs to this target.

    The key arrives from the browser. Without the prefix check an admin who
    mistyped -- or anyone who reached the endpoint -- could remove anything
    else the bucket holds, which may be nothing to do with opanel.
    """
    safe_prefix = s3_prefix_for(prefix)
    wanted = (key or "").strip().lstrip("/")
    if not wanted:
        raise ValueError("Object key is required")
    if safe_prefix and not wanted.startswith(f"{safe_prefix}/"):
        raise ValueError("That object is outside this destination's prefix")
    if ".." in wanted.split("/"):
        raise ValueError("Invalid object key")

    client = _s3_client(
        endpoint=endpoint, region=region, access_key=access_key,
        secret_key=secret_key, use_path_style=use_path_style,
    )
    try:
        client.delete_object(Bucket=bucket, Key=wanted)
    except Exception as exc:
        raise _s3_failure(exc, bucket) from exc
    return wanted
