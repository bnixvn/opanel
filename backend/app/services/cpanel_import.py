"""Import cPanel account backups (the cPanel Import addon, 2026-10-18).

A cPanel backup -- ``cpmove-<user>.tar.gz`` from pkgacct, or the
``backup-<date>_<time>_<user>.tar.gz`` a cPanel backup run writes -- is laid
out the way a DirectAdmin backup is, then imported by the DirectAdmin importer:
one importer for users, websites, databases, app configs and certificates,
with two archive layouts in front of it.

What a cPanel archive holds, and where it goes:

- ``cp/<user>``: the account -- ``USER=``, ``CONTACTEMAIL=``, ``DNS=`` the
  main domain;
- ``userdata/main``: the main domain, the addon domains (each backed by a
  subdomain of the main one, which is not a website of its own), the parked
  domains and the subdomains;
- ``userdata/<domain>``: each one's ``documentroot`` and ``phpversion``;
- ``homedir/`` (or ``homedir.tar``): the home folder the document roots are in.
  cPanel nests addon domains and subdomains inside public_html; each is moved
  out before its parent is, so no files are imported twice;
- ``mysql/<db>.sql``: the databases (the grants in ``mysql.sql`` carry only
  password hashes; the importer takes the password from the site's config);
- ``apache_tls/<domain>``: the certificate, its key and chain in one PEM file.

Parked domains become aliases of the main website. Mail, cron jobs and DNS
zones are counted and reported, not imported.
"""
from __future__ import annotations

import datetime as _dt
import re
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.models.entities import Website, WebsiteAlias
from app.services import da_import

ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar")
# cpmove-<user>.tar.gz (pkgacct) and backup-<M.D.YYYY>_<H-M-S>_<user>.tar.gz
# (a cPanel backup run).
NAME_RE = re.compile(
    r"^(?:cpmove-(?P<a>[a-z0-9][a-z0-9_.-]{0,31})"
    r"|backup-\d{1,2}\.\d{1,2}\.\d{4}_\d{1,2}-\d{1,2}-\d{1,2}_(?P<b>[a-z0-9][a-z0-9_.-]{0,31}))"
    r"\.(?:tar\.gz|tgz|tar)$",
    re.IGNORECASE,
)
# Databases cPanel itself keeps beside the account's own; never the customer's.
SYSTEM_DATABASES = frozenset({"roundcube", "horde", "cphulkd", "eximstats", "leechprotect", "modsec", "whmxfer", "mysql"})
MAX_UPLOAD_BYTES = da_import.MAX_UPLOAD_BYTES
STAGE_PREFIX = "opanel-da-import-cpanel-"


class CPanelImportError(RuntimeError):
    """A cPanel archive that cannot be imported, phrased for the panel."""


def _log(msg: str) -> None:
    da_import._log(f"[cpanel] {msg}")


# --- Names and the folder ------------------------------------------------------------------------

def is_cpanel_name(name: str) -> bool:
    return bool(NAME_RE.match(name.replace("\\", "/").rsplit("/", 1)[-1]))


def account_of(name: str) -> str:
    match = NAME_RE.match(name.replace("\\", "/").rsplit("/", 1)[-1])
    return (match.group("a") or match.group("b") or "").lower() if match else ""


def backup_dir() -> str:
    return settings.cpanel_backup_dir


def resolve_path(backup_file: str) -> Path:
    """The archive a name or path in the cPanel folder names; FileNotFoundError if none."""
    folder = Path(settings.cpanel_backup_dir).resolve()
    raw = Path(backup_file or "")
    if not raw.name:
        raise FileNotFoundError("Backup not found")
    if raw.is_absolute():
        path = raw.resolve()
    elif raw.name == str(raw):
        path = (folder / raw.name).resolve()
    else:
        raise FileNotFoundError("Backup not found")
    if path.parent != folder or not path.is_file() or not is_cpanel_name(path.name):
        raise FileNotFoundError("Backup not found")
    return path


def list_backups() -> list[dict]:
    folder = Path(settings.cpanel_backup_dir)
    if not folder.is_dir():
        return []
    items = []
    for path in sorted(folder.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.is_file() and not path.is_symlink() and is_cpanel_name(path.name):
            info = path.stat()
            items.append({"filename": path.name, "path": str(path), "size": info.st_size,
                          "modified": _dt.datetime.utcfromtimestamp(info.st_mtime).isoformat() + "Z"})
    return items


def save_backup(filename: str, source_file) -> dict:
    """An uploaded cPanel archive, into the cPanel folder under a name not taken yet."""
    folder = Path(settings.cpanel_backup_dir)
    folder.mkdir(parents=True, exist_ok=True)
    safe = Path((filename or "").replace("\\", "/")).name
    if not is_cpanel_name(safe):
        raise ValueError("Not a cPanel backup: expected cpmove-<user>.tar.gz or backup-<date>_<user>.tar.gz")
    target = (folder / safe).resolve()
    if target.exists():
        stem = da_import._strip_archive_suffix(safe)
        # Keep the account at the end of the name, where account_of reads it.
        target = (folder / f"cpmove-{account_of(safe)}{safe[len(stem):]}").resolve()
        if target.exists():
            raise ValueError(f"{target.name} is already waiting to be restored; delete it first")
    if target.parent != folder.resolve():
        raise ValueError("Invalid backup filename")
    written = 0
    with target.open("wb") as out:
        while chunk := source_file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                out.close()
                target.unlink(missing_ok=True)
                raise ValueError("Backup file is too large for a browser upload (max 1 GB); use the SFTP folder")
            out.write(chunk)
    info = target.stat()
    return {"filename": target.name, "path": str(target), "size": info.st_size,
            "modified": _dt.datetime.utcfromtimestamp(info.st_mtime).isoformat() + "Z"}


def delete_backup(backup_file: str) -> str:
    path = resolve_path(backup_file)
    path.unlink()
    return path.name


# --- Reading the archive -------------------------------------------------------------------------

def parse_userdata(text: str) -> dict:
    """cPanel's userdata YAML, as far as it goes: top-level keys whose value is
    a scalar, a list ("  - item") or a map ("  key: value")."""
    data: dict = {}
    current: Optional[str] = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or raw.strip() == "---":
            continue
        if not raw.startswith((" ", "\t")):
            key, _, value = raw.partition(":")
            current = key.strip().strip("'\"")
            value = value.strip()
            data[current] = _scalar(value) if value and value not in {"{}", "[]"} else ([] if value == "[]" else {})
            continue
        if current is None:
            continue
        line = raw.strip()
        if line.startswith("- "):
            if not isinstance(data.get(current), list):
                data[current] = []
            data[current].append(_scalar(line[2:]))
        elif ":" in line:
            if not isinstance(data.get(current), dict):
                data[current] = {}
            key, _, value = line.partition(":")
            data[current][key.strip().strip("'\"")] = _scalar(value.strip())
    return data


def _scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value


def _find_root(extracted: Path) -> Path:
    """The folder holding cp/ and userdata/: the archive's top folder, usually."""
    def looks_right(path: Path) -> bool:
        return (path / "userdata").is_dir() or (path / "cp").is_dir()

    if looks_right(extracted):
        return extracted
    for child in sorted(extracted.iterdir()):
        if child.is_dir() and looks_right(child):
            return child
    for child in sorted(extracted.iterdir()):
        if child.is_dir():
            for grandchild in sorted(child.iterdir()):
                if grandchild.is_dir() and looks_right(grandchild):
                    return grandchild
    raise CPanelImportError("Not a cPanel backup: it has no cp/ or userdata/ folder")


def _account(root: Path, archive_name: str) -> dict:
    """The account file cp/<user>: its name, contact email and main domain."""
    values: dict[str, str] = {}
    user = ""
    cp = root / "cp"
    if cp.is_dir():
        files = [path for path in sorted(cp.iterdir()) if path.is_file()]
        if files:
            user = files[0].name
            for line in files[0].read_text(encoding="utf-8", errors="ignore").splitlines():
                key, sep, value = line.partition("=")
                if sep:
                    values[key.strip().upper()] = value.strip()
    user = (values.get("USER") or user or account_of(archive_name)).strip().lower()
    if not user:
        raise CPanelImportError("The cPanel account name is not in the archive")
    return {"user": user, "email": values.get("CONTACTEMAIL", "").split(",")[0].strip(),
            "main": (values.get("DNS") or "").strip().lower()}


def _websites(root: Path, account: dict, summary: dict) -> tuple[list[dict], list[str], str]:
    """The websites (domain, document root, PHP version), the parked domains, and the home folder."""
    main_data = {}
    main_file = root / "userdata" / "main"
    if main_file.is_file():
        main_data = parse_userdata(main_file.read_text(encoding="utf-8", errors="ignore"))
    main = (main_data.get("main_domain") or account["main"] or "").strip().lower()
    if not main:
        raise CPanelImportError("The archive does not name the account's main domain")
    addons = main_data.get("addon_domains") if isinstance(main_data.get("addon_domains"), dict) else {}
    subs = main_data.get("sub_domains") if isinstance(main_data.get("sub_domains"), list) else []
    parked = main_data.get("parked_domains") if isinstance(main_data.get("parked_domains"), list) else []
    backing = {str(sub).lower() for sub in addons.values()}

    def site(domain: str, userdata_name: str) -> dict:
        data = {}
        path = root / "userdata" / userdata_name
        if path.is_file():
            data = parse_userdata(path.read_text(encoding="utf-8", errors="ignore"))
        return {"domain": domain.lower(), "documentroot": str(data.get("documentroot") or ""),
                "homedir": str(data.get("homedir") or ""), "phpversion": str(data.get("phpversion") or "")}

    sites = [site(main, main)]
    if not sites[0]["documentroot"]:
        sites[0]["documentroot"] = f"/home/{account['user']}/public_html"
    for addon, sub in addons.items():
        sites.append(site(str(addon), str(sub)))
    for sub in subs:
        if str(sub).lower() not in backing:
            sites.append(site(str(sub), str(sub)))
    homedir = next((s["homedir"] for s in sites if s["homedir"]), "") or f"/home/{account['user']}"
    for entry in sites:
        if not da_import._normalize_domain(entry["domain"]):
            summary["warnings"].append(f"Skipped {entry['domain']!r}: not a domain name")
    sites = [entry for entry in sites if da_import._normalize_domain(entry["domain"])]
    return sites, [str(name).lower() for name in parked if da_import._normalize_domain(str(name))], homedir.rstrip("/")


def _home(root: Path) -> Optional[Path]:
    """The account's home folder from the archive, unpacked if it came as homedir.tar."""
    folder = root / "homedir"
    if folder.is_dir():
        return folder
    packed = root / "homedir.tar"
    if packed.is_file():
        folder.mkdir()
        da_import._safe_extract_tar(packed, folder)
        packed.unlink(missing_ok=True)
        return folder
    return None


def _split_pem(text: str) -> dict:
    """apache_tls/<domain>: the key, the certificate, then its chain, in one file."""
    keys, certificates = [], []
    for match in re.finditer(r"-----BEGIN ([A-Z ]+)-----.*?-----END \1-----", text, re.S):
        if "PRIVATE KEY" in match.group(1):
            keys.append(match.group(0))
        elif match.group(1) == "CERTIFICATE":
            certificates.append(match.group(0))
    return {"key": keys[0] if keys else "", "cert": certificates[0] if certificates else "",
            "chain": "\n".join(certificates[1:])}


# --- Laying it out the DirectAdmin way ------------------------------------------------------------

def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))


def _layout(root: Path, target: Path, account: dict, sites: list[dict], homedir: str, summary: dict) -> None:
    """Write ``target`` as the DirectAdmin importer reads a backup."""
    (target / "backup").mkdir(parents=True)
    (target / "domains").mkdir()
    (target / "mysql").mkdir()
    _write(target / "backup" / "user.conf", f"username={account['user']}\nemail={account['email']}\n")
    _write(target / "backup" / "domains.list", "".join(entry["domain"] + "\n" for entry in sites))

    home = _home(root)
    # Deepest document root first: cPanel keeps addon domains and subdomains
    # inside public_html, and each has to leave before its parent is moved.
    moved: dict[str, Path] = {}
    for entry in sorted(sites, key=lambda item: -len(Path(item["documentroot"] or "/").parts)):
        domain, docroot = entry["domain"], entry["documentroot"].rstrip("/")
        destination = target / "domains" / domain / "public_html"
        if not docroot or home is None:
            continue
        if docroot in moved:
            # Two websites on one folder: each gets its own copy.
            shutil.copytree(moved[docroot], destination, symlinks=True)
            continue
        if docroot != homedir and not docroot.startswith(homedir + "/"):
            summary["warnings"].append(f"{domain}: its files were outside the home folder ({docroot}) and are not in the archive")
            continue
        relative = docroot[len(homedir):].strip("/")
        source = home / relative if relative else None
        if source is None or not source.is_dir() or source.is_symlink():
            summary["warnings"].append(f"{domain}: no files found at {docroot or '(none)'}; the website starts empty")
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        moved[docroot] = destination

    sql_dir = root / "mysql"
    if sql_dir.is_dir():
        for path in sorted(sql_dir.iterdir()):
            lower = path.name.lower()
            if not path.is_file() or not any(lower.endswith(suffix) for suffix in da_import.SQL_SUFFIXES):
                continue
            if da_import._sql_base_name(path).lower() in SYSTEM_DATABASES:
                continue
            shutil.move(str(path), str(target / "mysql" / path.name))

    tls = root / "apache_tls"
    if tls.is_dir():
        for entry in sites:
            pem = tls / entry["domain"]
            if not pem.is_file() or pem.stat().st_size > 512 * 1024:
                continue
            parts = _split_pem(pem.read_text(encoding="utf-8", errors="ignore"))
            if not parts["key"] or not parts["cert"]:
                continue
            folder = target / "backup" / entry["domain"]
            folder.mkdir(parents=True, exist_ok=True)
            _write(folder / "domain.key", parts["key"] + "\n")
            _write(folder / "domain.cert", parts["cert"] + "\n")
            if parts["chain"]:
                _write(folder / "domain.cacert", parts["chain"] + "\n")


def _notes(root: Path, account: dict, sites: list[dict], summary: dict) -> None:
    """What the archive carries that the import leaves out, counted for the summary."""
    home = root / "homedir"
    mailboxes = 0
    mail = home / "mail"
    if mail.is_dir():
        for domain_dir in mail.iterdir():
            if domain_dir.is_dir() and not domain_dir.name.startswith(".") and da_import._normalize_domain(domain_dir.name):
                mailboxes += sum(1 for box in domain_dir.iterdir() if box.is_dir())
    if mailboxes:
        summary["warnings"].append(f"{mailboxes} mailbox(es) are not imported; create them again under Email")
    cron = root / "cron" / account["user"]
    if cron.is_file():
        jobs = [line for line in cron.read_text(encoding="utf-8", errors="ignore").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
                and re.match(r"^\s*(@\w+|\S+\s+\S+\s+\S+\s+\S+\s+\S+)\s+\S", line)]
        if jobs:
            summary["warnings"].append(f"{len(jobs)} cron job(s) are not imported; add them again under Cron")
    for entry in sites:
        version = re.match(r"^(?:ea-php|alt-php)(\d)(\d)$", entry.get("phpversion") or "")
        if version:
            wanted = f"{version.group(1)}.{version.group(2)}"
            if wanted != settings.default_php_version:
                summary["warnings"].append(
                    f"{entry['domain']} ran PHP {wanted} on cPanel and runs {settings.default_php_version} here; "
                    "change it on the website's page if it needs the old one")


def _add_parked(db, main_domain: str, parked: list[str], summary: dict) -> None:
    """Parked domains become aliases of the main website."""
    if not parked:
        return
    website = db.query(Website).filter(Website.domain == main_domain).first()
    if website is None:
        return
    added = []
    for name in parked:
        if db.query(Website).filter(Website.domain == name).first():
            summary["warnings"].append(f"Parked domain {name} is a website here already; not added as an alias")
            continue
        existing = db.query(WebsiteAlias).filter(WebsiteAlias.domain == name).first()
        if existing is not None:
            if existing.website_id != website.id:
                summary["warnings"].append(f"Parked domain {name} is an alias of another website here; not added")
            continue
        db.add(WebsiteAlias(website_id=website.id, domain=name, mode="alias"))
        added.append(name)
    if not added:
        return
    db.commit()
    db.refresh(website)
    summary.setdefault("aliases", []).extend(added)
    try:
        from app.api.websites import _rewrite_website_vhost

        _rewrite_website_vhost(website)
    except Exception as exc:  # noqa: BLE001 - the aliases are saved; a later save rewrites the vhost
        summary["warnings"].append(f"Aliases saved but the vhost was not rewritten: {exc}")


# --- The import ----------------------------------------------------------------------------------

def import_cpanel_backup(backup_file: str, db, overwrite: bool = False) -> dict:
    """Import one cPanel archive: the account, its websites, databases and certificates."""
    path = resolve_path(backup_file)
    archive_name = path.name
    _log(f"Starting cPanel import: {archive_name}")
    # The root helper copies site files only out of /tmp/opanel-da-import-*
    # (site-import-copy): the cPanel import stages under that name too, rather
    # than the helper trusting one more folder. Found live on .41.
    stage = Path(tempfile.mkdtemp(prefix=STAGE_PREFIX))
    credentials = [
        "# OPanel cPanel import credentials",
        f"# Archive: {archive_name}",
        f"# Date: {_dt.datetime.utcnow().isoformat()}Z",
        "# Keep this file private.",
    ]
    try:
        extracted = stage / "archive"
        extracted.mkdir()
        da_import._safe_extract_tar(path, extracted)
        root = _find_root(extracted)
        account = _account(root, archive_name)
        summary_notes: dict = {"warnings": []}
        sites, parked, homedir = _websites(root, account, summary_notes)
        laid_out = stage / "directadmin"
        _layout(root, laid_out, account, sites, homedir, summary_notes)
        _notes(root, account, sites, summary_notes)
        shutil.rmtree(extracted, ignore_errors=True)

        summary = da_import._process_archive(laid_out, archive_name, db, credentials, overwrite=overwrite)
        summary["source"] = "cpanel"
        summary["warnings"] = summary_notes["warnings"] + list(summary.get("warnings") or [])
        if sites:
            _add_parked(db, sites[0]["domain"], parked, summary)

        credentials_file = path.parent / f"{da_import._strip_archive_suffix(archive_name)}-credentials.txt"
        credentials_file.write_text("\n".join(credentials) + "\n", encoding="utf-8")
        credentials_file.chmod(0o600)
        _log(f"cPanel import completed: {archive_name}")
        return summary
    except Exception as exc:
        _log(f"cPanel import failed: {archive_name}: {exc}")
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
