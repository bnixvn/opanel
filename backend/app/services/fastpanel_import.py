"""Import accounts pulled off a FastPanel server (the FastPanel Import addon, 2026-10-18).

FastPanel's own backups keep a site's files and its databases in separate
archives, so there is nothing to upload: the panel signs in to the FastPanel
server as root (services/migrate_ssh.py) and packs each account picked into
one archive, ``fastpanel-<user>.tar.gz``, laid out the way a DirectAdmin
backup is:

- ``backup/user.conf`` and ``backup/domains.list``: the account and its sites;
- ``domains/<domain>/public_html``: each site's files, from
  ``/var/www/<user>/data/www/<domain>``;
- ``mysql/<db>.sql``: the databases its sites' configs name, and those named
  after the account;
- ``backup/<domain>/domain.{cert,key}``: the certificate nginx serves it with;
- ``opanel/aliases.tsv``: each site's other names (nginx ``server_name``).

The DirectAdmin importer then does the rest; the aliases are added after it.
"""
from __future__ import annotations

import datetime as _dt
import re
import shutil
import tempfile
from pathlib import Path

from app.core.config import settings
from app.services import cpanel_import, da_import

NAME_RE = re.compile(r"^fastpanel-(?P<user>[a-z][a-z0-9_-]{0,31})\.tar\.gz$")
# The root helper copies site files only out of /tmp/opanel-da-import-*.
STAGE_PREFIX = "opanel-da-import-fastpanel-"


def is_fastpanel_name(name: str) -> bool:
    return bool(NAME_RE.match(name.replace("\\", "/").rsplit("/", 1)[-1]))


def account_of(name: str) -> str:
    match = NAME_RE.match(name.replace("\\", "/").rsplit("/", 1)[-1])
    return match.group("user") if match else ""


def backup_dir() -> str:
    return settings.fastpanel_backup_dir


def resolve_path(backup_file: str) -> Path:
    folder = Path(settings.fastpanel_backup_dir).resolve()
    raw = Path(backup_file or "")
    path = raw.resolve() if raw.is_absolute() else (folder / raw.name).resolve()
    if path.parent != folder or not path.is_file() or not is_fastpanel_name(path.name):
        raise FileNotFoundError("Backup not found")
    return path


def _aliases(root: Path) -> dict[str, list[str]]:
    """Each site's other names, from the packed nginx server_name lines."""
    found: dict[str, list[str]] = {}
    table = root / "opanel" / "aliases.tsv"
    if not table.is_file():
        return found
    for line in table.read_text(encoding="utf-8", errors="ignore").splitlines():
        domain, _, names = line.partition("\t")
        domain = (da_import._normalize_domain(domain) or "")
        if not domain:
            continue
        others = []
        for name in names.split():
            name = da_import._normalize_domain(name.strip())
            # www.<domain> is served by every website here already.
            if name and name != domain and name != f"www.{domain}" and name not in others:
                others.append(name)
        found[domain] = others
    return found


def import_fastpanel_backup(backup_file: str, db, overwrite: bool = False) -> dict:
    """Import one packed FastPanel account."""
    path = resolve_path(backup_file)
    archive_name = path.name
    da_import._log(f"[fastpanel] Starting FastPanel import: {archive_name}")
    stage = Path(tempfile.mkdtemp(prefix=STAGE_PREFIX))
    credentials = [
        "# OPanel FastPanel import credentials",
        f"# Archive: {archive_name}",
        f"# Date: {_dt.datetime.utcnow().isoformat()}Z",
        "# Keep this file private.",
    ]
    try:
        da_import._safe_extract_tar(path, stage)
        aliases = _aliases(stage)
        summary = da_import._process_archive(stage, archive_name, db, credentials, overwrite=overwrite)
        summary["source"] = "fastpanel"
        for domain, names in aliases.items():
            if names and domain in summary.get("imported_domains", []):
                cpanel_import._add_parked(db, domain, names, summary)
        credentials_file = path.parent / f"{da_import._strip_archive_suffix(archive_name)}-credentials.txt"
        credentials_file.write_text("\n".join(credentials) + "\n", encoding="utf-8")
        credentials_file.chmod(0o600)
        return summary
    finally:
        shutil.rmtree(stage, ignore_errors=True)
