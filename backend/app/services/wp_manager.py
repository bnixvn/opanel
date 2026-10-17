"""WordPress Manager (an addon, 2026-10-17; SNPanel's WordPress Manager).

Every WordPress in the websites a caller manages: found by a scan of each
website's folder, checked with WP-CLI - core, plugins, themes, administrators,
maintenance mode, debugging - and managed from the panel: updates, plugins and
themes switched on and off, installed and deleted, a one-time sign-in to
wp-admin, a new administrator password.

Every WP-CLI command runs as the website's own Linux user through
opanel-helper's wp-site, never as root, www-data or opanel: WP-CLI loads the
site's wp-config.php. Whatever changes a site is a task (WpJob) run in the
background, one at a time per WordPress, and the page follows it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import string
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.models.entities import User, Website, WpInstall, WpJob
from app.services import site_users
from app.services.shell import shell

logger = logging.getLogger("opanel.wordpress")

ADDON_ID = "wordpress"
# What a task may name: a wordpress.org slug for a plugin or a theme, a
# folder one level under public_html.
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,99}$")
FOLDER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
# Folders under public_html that are never a WordPress of their own.
NOT_A_SITE = {"wp-admin", "wp-content", "wp-includes", "cgi-bin", ".well-known", "logs", "tmp"}
MAX_SUBFOLDERS = 60
KEEP_JOBS = 40
LOG_LIMIT = 32_000
PLUGIN_FIELDS = "name,title,status,version,update,update_version,auto_update"
THEME_FIELDS = "name,title,status,version,update,update_version,auto_update"
SSO_PLUGIN = "opanel-sso.php"
ITEM_ACTIONS = {
    "plugin": {"install", "activate", "deactivate", "update", "update_all", "delete", "auto_on", "auto_off"},
    "theme": {"install", "activate", "update", "update_all", "delete", "auto_on", "auto_off"},
}

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="opanel-wpm")
_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()
# The tasks this process has started and not finished: a row still queued or
# running that is not here was cut short by a restart.
_live: set[int] = set()
# Tests point the background work at their own database.
_session_factory = None


def enabled() -> bool:
    """The WordPress Manager is an addon: off until an administrator installs it."""
    from app.services import addons

    return addons.is_enabled(ADDON_ID)


# --- Finding WordPress -----------------------------------------------------------------

def public_root(website: Website) -> Path:
    return Path(site_users.document_root(website.root_path, website.document_root or "public_html"))


def _looks_like_wordpress(folder: Path) -> Optional[str]:
    """'installed', 'setup' (WordPress's files, no wp-config.php yet), or None."""
    try:
        if not (folder / "wp-includes" / "version.php").is_file():
            return None
        # WordPress also reads a wp-config.php one folder up - unless that
        # folder is a WordPress of its own (a blog under a WordPress site).
        above = folder.parent
        if (folder / "wp-config.php").is_file() or (
                (above / "wp-config.php").is_file() and not (above / "wp-settings.php").is_file()
                and not (above / "wp-includes").is_dir()):
            return "installed"
        return "setup"
    except OSError:
        return None


def discover(website: Website) -> list[tuple[str, str, str]]:
    """The WordPress installs in a website: (rel_path, path, state).

    Its public_html, and each folder one level under it - a blog at
    example.com/blog - as SNPanel's scan does.
    """
    root = public_root(website)
    found: list[tuple[str, str, str]] = []
    state = _looks_like_wordpress(root)
    if state:
        found.append(("", str(root), state))
    try:
        children = sorted(child for child in root.iterdir() if child.is_dir() and not child.is_symlink())
    except OSError:
        return found
    for child in children[:MAX_SUBFOLDERS]:
        if child.name in NOT_A_SITE or child.name.startswith("."):
            continue
        state = _looks_like_wordpress(child)
        if state:
            found.append((child.name, str(child), state))
    return found


# --- WP-CLI as the site's user -----------------------------------------------------------

class WpError(RuntimeError):
    pass


def _linux_user(website: Website) -> str:
    return site_users.require_site_linux_user(website)


def wp(website: Website, path: str, args: list[str], *, skip=True, input: Optional[str] = None,
       sensitive: bool = False, check: bool = True):
    """Run one WP-CLI command in a WordPress, as the website's own user.

    `skip` leaves the site's plugins and theme out: a broken plugin then
    cannot stop the panel from reading or repairing the site.
    """
    argv = [*args, f"--path={path}"]
    if skip:
        argv += ["--skip-plugins", "--skip-themes"]
    result = shell.privileged("wp-site", helper_args=[_linux_user(website), *argv], check=False,
                              input=input, sensitive=sensitive, fallback=["wp", *argv])
    if check and result.returncode != 0:
        message = _clean_error(result.stderr or result.stdout or f"wp {' '.join(args[:2])} failed")
        raise WpError(message)
    return result


def _clean_error(text: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    errors = [line for line in lines if line.startswith("Error:")]
    return (errors[-1] if errors else (lines[-1] if lines else "WP-CLI failed"))[:500]


def _json(result) -> list | dict:
    text = (result.stdout or "").strip()
    # A plugin's notice may come before the JSON: take from its first bracket.
    start = min([i for i in (text.find("["), text.find("{")) if i >= 0], default=-1)
    if start < 0:
        return []
    try:
        return json.loads(text[start:])
    except ValueError:
        return []


def _version_key(version: str) -> tuple:
    return tuple(int(part) if part.isdigit() else 0 for part in re.split(r"[.-]", version or "0"))


def read_inventory(website: Website, path: str) -> dict:
    """What a WordPress runs, as WP-CLI reads it."""
    core_version = wp(website, path, ["core", "version"]).stdout.strip()
    updates = _json(wp(website, path, ["core", "check-update", "--format=json"], check=False)) or []
    updates = [item for item in updates if isinstance(item, dict) and item.get("version")]
    latest = max((item["version"] for item in updates), key=_version_key, default="")
    minor = next((item["version"] for item in updates if item.get("update_type") == "minor"), "")
    plugins = _json(wp(website, path, ["plugin", "list", "--format=json", f"--fields={PLUGIN_FIELDS}"], check=False)) or []
    themes = _json(wp(website, path, ["theme", "list", "--format=json", f"--fields={THEME_FIELDS}"], check=False)) or []
    admins = _json(wp(website, path, ["user", "list", "--role=administrator", "--format=json",
                                      "--fields=ID,user_login,user_email,display_name"], check=False)) or []
    title = wp(website, path, ["option", "get", "blogname"], check=False).stdout.strip()
    home = wp(website, path, ["option", "get", "home"], check=False).stdout.strip()
    maintenance = wp(website, path, ["maintenance-mode", "is-active"], check=False).returncode == 0
    debug = wp(website, path, ["config", "get", "WP_DEBUG", "--type=constant"], check=False).stdout.strip().lower()
    auto = wp(website, path, ["config", "get", "WP_AUTO_UPDATE_CORE", "--type=constant"], check=False)
    auto_value = auto.stdout.strip().lower() if auto.returncode == 0 else ""
    file_edit = wp(website, path, ["config", "get", "DISALLOW_FILE_EDIT", "--type=constant"], check=False)
    blog_public = wp(website, path, ["option", "get", "blog_public"], check=False).stdout.strip()
    config_path = wp(website, path, ["config", "path"], check=False).stdout.strip()
    try:
        config_mode = oct(Path(config_path).stat().st_mode & 0o777)[2:] if config_path else ""
    except OSError:
        config_mode = ""
    return {
        "core_version": core_version,
        "core_update": latest,
        "core_minor_update": minor,
        "title": title,
        "url": home,
        "plugins": [_item(entry) for entry in plugins if isinstance(entry, dict)],
        "themes": [_item(entry) for entry in themes if isinstance(entry, dict)],
        "admins": [{"id": int(entry.get("ID") or 0), "login": entry.get("user_login", ""), "email": entry.get("user_email", ""),
                    "name": entry.get("display_name", "")} for entry in admins if isinstance(entry, dict)],
        "maintenance": maintenance,
        "debug": debug in {"1", "true"},
        # WordPress's own default is minor (security) releases.
        "auto_update_core": "all" if auto_value in {"1", "true"} else "off" if auto_value in {"false", "0"} else "minor",
        "file_edit_disallowed": file_edit.returncode == 0 and file_edit.stdout.strip().lower() in {"1", "true"},
        "blog_public": blog_public != "0",
        "config_path": config_path,
        "config_mode": config_mode,
    }


def _item(entry: dict) -> dict:
    return {
        "name": str(entry.get("name", "")),
        "title": str(entry.get("title", "")),
        "status": str(entry.get("status", "")),
        "version": str(entry.get("version", "")),
        "update": entry.get("update") == "available",
        "update_version": str(entry.get("update_version") or ""),
        "auto_update": entry.get("auto_update") in ("on", True, "enabled"),
    }


def apply_inventory(install: WpInstall, inventory: dict) -> None:
    plugins = [item for item in inventory["plugins"] if item["status"] not in {"must-use", "dropin"}]
    install.core_version = inventory["core_version"][:32]
    install.core_update = inventory["core_update"][:32] if _version_key(inventory["core_update"]) > _version_key(inventory["core_version"]) else ""
    install.title = (inventory.get("title") or "")[:255]
    install.url = (inventory.get("url") or "")[:512]
    install.plugins_total = len(plugins)
    install.plugins_updates = sum(1 for item in plugins if item["update"])
    install.themes_total = len(inventory["themes"])
    install.themes_updates = sum(1 for item in inventory["themes"] if item["update"])
    previous = json.loads(install.inventory or "{}")
    if previous.get("security_full") and "security_full" not in inventory:
        inventory["security_full"] = previous["security_full"]
    install.inventory = json.dumps(inventory)
    install.last_checked_at = datetime.utcnow()
    install.last_error = ""


# --- Access -----------------------------------------------------------------------------

def installs_for(db: Session, actor: User) -> list[WpInstall]:
    from app.core.access import scope_owner

    return scope_owner(db.query(WpInstall), WpInstall.owner_id, db, actor).order_by(WpInstall.id).all()


def forget_website(db: Session, website_id: int) -> None:
    """A website is going: the panel forgets the WordPress it found in it."""
    db.query(WpInstall).filter(WpInstall.website_id == website_id).delete(synchronize_session=False)


# --- Tasks --------------------------------------------------------------------------------

def _factory():
    if _session_factory is not None:
        return _session_factory
    from app.core.database import SessionLocal

    return SessionLocal


def _lock_for(key: str) -> threading.Lock:
    with _locks_lock:
        return _locks.setdefault(key, threading.Lock())


def submit(db: Session, *, kind: str, actor: Optional[User], label: str, params: Optional[dict] = None,
           install: Optional[WpInstall] = None, website: Optional[Website] = None) -> WpJob:
    job = WpJob(
        kind=kind, label=label[:255], params=json.dumps(params or {}),
        install_id=install.id if install else None,
        website_id=(install.website_id if install else website.id if website else None),
        owner_id=(install.owner_id if install else website.owner_id if website else (actor.id if actor else None)),
        actor_id=actor.id if actor else None, status="queued",
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    _prune_jobs(db)
    _live.add(job.id)
    _executor.submit(_run, job.id)
    return job


def _prune_jobs(db: Session) -> None:
    ids = [row.id for row in db.query(WpJob.id).order_by(WpJob.id.desc()).offset(KEEP_JOBS * 10).all()]
    if ids:
        db.query(WpJob).filter(WpJob.id.in_(ids), WpJob.status.in_(("completed", "failed", "interrupted"))).delete(
            synchronize_session=False)
        db.commit()


def settle_stale(db: Session) -> None:
    """Tasks a restart cut short say so, instead of running forever."""
    stale = [job for job in db.query(WpJob).filter(WpJob.status.in_(("queued", "running"))).all() if job.id not in _live]
    for job in stale:
        job.status = "interrupted"
        job.finished_at = datetime.utcnow()
        job.error = "The panel restarted before this task ended."
    if stale:
        db.commit()


class _Task:
    """What a running task writes as it goes: its stage and its log."""

    def __init__(self, db: Session, job: WpJob):
        self.db = db
        self.job = job

    def stage(self, text: str) -> None:
        self.job.stage = text[:255]
        self.log(text)

    def log(self, text: str) -> None:
        line = f"[{datetime.utcnow().strftime('%H:%M:%S')}] {text}\n"
        self.job.log = ((self.job.log or "") + line)[-LOG_LIMIT:]
        self.db.commit()


def _run(job_id: int) -> None:
    db = _factory()()
    try:
        job = db.get(WpJob, job_id)
        if not job:
            return
        key = f"install:{job.install_id}" if job.install_id else f"site:{job.website_id}" if job.website_id else "scan"
        with _lock_for(key):
            db.refresh(job)
            job.status = "running"
            job.started_at = datetime.utcnow()
            db.commit()
            task = _Task(db, job)
            try:
                HANDLERS[job.kind](db, task, json.loads(job.params or "{}"))
                job.status = "completed"
                job.stage = ""
            except Exception as exc:  # the task's own failure, shown on the page
                logger.warning("WordPress task %s (%s) failed: %s", job.id, job.kind, exc)
                job.status = "failed"
                job.error = str(exc)[:2000]
                task.log(f"Failed: {exc}")
            job.finished_at = datetime.utcnow()
            db.commit()
    finally:
        _live.discard(job_id)
        db.close()


def _install_and_site(db: Session, job: WpJob) -> tuple[WpInstall, Website]:
    install = db.get(WpInstall, job.install_id)
    if not install:
        raise WpError("This WordPress is no longer known: scan its website again.")
    website = db.get(Website, install.website_id)
    if not website:
        raise WpError("Its website is gone.")
    return install, website


def refresh_install(db: Session, task: Optional[_Task], install: WpInstall, website: Website) -> None:
    if task:
        task.stage(f"Checking {install.url or website.domain}")
    try:
        apply_inventory(install, read_inventory(website, install.path))
    except WpError as exc:
        install.last_error = str(exc)
        install.last_checked_at = datetime.utcnow()
        db.commit()
        raise
    db.commit()


def _scan_website(db: Session, task: _Task, website: Website) -> list[WpInstall]:
    found = discover(website)
    known = {row.path: row for row in db.query(WpInstall).filter(WpInstall.website_id == website.id).all()}
    rows = []
    for rel, path, state in found:
        row = known.pop(path, None)
        if row is None:
            row = WpInstall(website_id=website.id, owner_id=website.owner_id, rel_path=rel, path=path)
            db.add(row)
        row.owner_id = website.owner_id
        row.status = "ok"
        row.state = state
        rows.append(row)
    for row in known.values():
        row.status = "missing"
    db.commit()
    task.log(f"{website.domain}: {len(found)} WordPress found")
    return rows


def _handle_scan(db: Session, task: _Task, params: dict) -> None:
    ids = params.get("website_ids") or []
    websites = db.query(Website).filter(Website.id.in_(ids)).order_by(Website.domain).all() if ids else []
    rows: list[tuple[WpInstall, Website]] = []
    for index, website in enumerate(websites, 1):
        task.stage(f"Scanning {website.domain} ({index}/{len(websites)})")
        try:
            rows += [(row, website) for row in _scan_website(db, task, website)]
        except Exception as exc:
            task.log(f"{website.domain}: {exc}")
    checked = [pair for pair in rows if pair[0].state == "installed"]
    for index, (row, website) in enumerate(checked, 1):
        task.stage(f"Checking {row.url or website.domain} ({index}/{len(checked)})")
        try:
            refresh_install(db, None, row, website)
        except Exception as exc:
            task.log(f"{website.domain}/{row.rel_path}: {exc}")


def _handle_refresh(db: Session, task: _Task, params: dict) -> None:
    install, website = _install_and_site(db, task.job)
    refresh_install(db, task, install, website)


def _handle_core(db: Session, task: _Task, params: dict) -> None:
    install, website = _install_and_site(db, task.job)
    action = params.get("action")
    if action == "update":
        version = str(params.get("version") or "")
        if version and not re.fullmatch(r"\d+(\.\d+){1,3}", version):
            raise WpError("Invalid WordPress version")
        task.stage(f"Updating WordPress{' to ' + version if version else ''}")
        args = ["core", "update"] + ([f"--version={version}"] if version else [])
        task.log(_short(wp(website, install.path, args).stdout))
        task.stage("Updating the database")
        wp(website, install.path, ["core", "update-db"], check=False)
    elif action == "auto_update":
        mode = params.get("mode")
        value = {"off": "false", "minor": "'minor'", "all": "true"}.get(mode)
        if value is None:
            raise WpError("Choose off, minor or all")
        task.stage("Setting automatic core updates")
        config_set(website, install.path, "WP_AUTO_UPDATE_CORE", value)
    else:
        raise WpError("Unknown core action")
    refresh_install(db, task, install, website)


def _handle_switch(constant_kind: str):
    def handler(db: Session, task: _Task, params: dict) -> None:
        install, website = _install_and_site(db, task.job)
        on = bool(params.get("on"))
        if constant_kind == "maintenance":
            task.stage("Maintenance mode " + ("on" if on else "off"))
            wp(website, install.path, ["maintenance-mode", "activate" if on else "deactivate"], check=False)
        else:
            task.stage("Debugging " + ("on" if on else "off"))
            for name, value in (("WP_DEBUG", on), ("WP_DEBUG_LOG", on), ("WP_DEBUG_DISPLAY", False)):
                config_set(website, install.path, name, "true" if value else "false")
        refresh_install(db, task, install, website)
    return handler


def _handle_items(kind: str):
    def handler(db: Session, task: _Task, params: dict) -> None:
        install, website = _install_and_site(db, task.job)
        action = params.get("action")
        slugs = [slug for slug in params.get("slugs") or [] if SLUG_RE.fullmatch(str(slug))]
        noun = "plugin" if kind == "plugin" else "theme"
        if action not in ITEM_ACTIONS[kind]:
            raise WpError(f"Unknown {noun} action")
        if action != "update_all" and not slugs:
            raise WpError(f"Name the {noun}")
        task.stage(f"{noun.capitalize()}: {action.replace('_', ' ')} {' '.join(slugs)}".strip())
        if action == "install":
            args = [noun, "install", *slugs] + (["--activate"] if params.get("activate") else [])
            # Installing and activating runs the plugin's own activation code.
            result = wp(website, install.path, args, skip=not params.get("activate"))
        elif action == "activate":
            result = wp(website, install.path, [noun, "activate", *slugs], skip=False)
        elif action == "deactivate":
            result = wp(website, install.path, [noun, "deactivate", *slugs], skip=False)
        elif action == "update":
            result = wp(website, install.path, [noun, "update", *slugs])
        elif action == "update_all":
            result = wp(website, install.path, [noun, "update", "--all"])
        elif action == "delete":
            if kind == "plugin":
                wp(website, install.path, ["plugin", "deactivate", *slugs], skip=False, check=False)
            result = wp(website, install.path, [noun, "delete", *slugs])
        else:
            result = wp(website, install.path, [noun, "auto-updates", "enable" if action == "auto_on" else "disable", *slugs])
        task.log(_short(result.stdout))
        refresh_install(db, task, install, website)
    return handler


def _handle_update_everything(db: Session, task: _Task, params: dict) -> None:
    install, website = _install_and_site(db, task.job)
    for stage, args in (("Updating WordPress", ["core", "update"]), ("Updating the database", ["core", "update-db"]),
                        ("Updating plugins", ["plugin", "update", "--all"]), ("Updating themes", ["theme", "update", "--all"])):
        task.stage(stage)
        result = wp(website, install.path, args, check=False)
        task.log(_short(result.stdout or result.stderr))
    refresh_install(db, task, install, website)


# Where `wp config set` adds a constant wp-config.php does not have yet:
# WordPress's own marker, or - in a file without it, as OPanel wrote them
# before 2026-10-17 - just before wp-settings.php is loaded. Whole lines only:
# a constant put inside a line would break the file.
CONFIG_ANCHORS = (
    None,
    "require_once ABSPATH . 'wp-settings.php';",
    "require_once(ABSPATH . 'wp-settings.php');",
    "require_once( ABSPATH . 'wp-settings.php' );",
)


def config_set(website: Website, path: str, name: str, value: str) -> None:
    """Set a constant in wp-config.php, adding it where WP-CLI can."""
    last = None
    for anchor in CONFIG_ANCHORS:
        args = ["config", "set", name, value, "--raw", "--type=constant"]
        if anchor:
            args += [f"--anchor={anchor}", "--placement=before"]
        try:
            wp(website, path, args)
            return
        except WpError as exc:
            last = exc
            if "transformation" not in str(exc) and "anchor" not in str(exc).lower():
                raise
    raise last


def _short(text: str) -> str:
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    return "\n".join(lines[-12:])


def security_items(inventory: dict) -> list[dict]:
    """What the Security tab lists: each check, its state and what can be done.

    status: ok | warning | action (nothing wrong, a thing to do) | unknown
    (not checked yet: the full check compares files with wordpress.org).
    """
    if not inventory.get("core_version"):
        return []
    plugins = [item for item in inventory.get("plugins") or [] if item["status"] not in {"must-use", "dropin"}]
    updates = sum(1 for item in plugins + (inventory.get("themes") or []) if item["update"]) + (1 if inventory.get("core_update") else 0)
    inactive = sum(1 for item in plugins if item["status"] == "inactive")
    mode = inventory.get("config_mode") or ""
    world = bool(mode) and int(mode, 8) & 0o007 != 0
    full = inventory.get("security_full") or {}
    items = [
        {"id": "debug", "status": "warning" if inventory.get("debug") else "ok", "apply": bool(inventory.get("debug"))},
        {"id": "file_edit", "status": "ok" if inventory.get("file_edit_disallowed") else "warning",
         "apply": not inventory.get("file_edit_disallowed"), "revert": bool(inventory.get("file_edit_disallowed"))},
        {"id": "config_mode", "status": "warning" if world else ("ok" if mode else "unknown"), "mode": mode, "apply": world},
        {"id": "admin_user", "status": "warning" if any(a.get("login") == "admin" for a in inventory.get("admins") or []) else "ok"},
        {"id": "updates", "status": "warning" if updates else "ok", "count": updates},
        {"id": "inactive_plugins", "status": "warning" if inactive else "ok", "count": inactive},
        {"id": "search_visibility", "status": "ok" if inventory.get("blog_public", True) else "warning",
         "apply": not inventory.get("blog_public", True), "revert": bool(inventory.get("blog_public", True))},
    ]
    for key in ("core_checksums", "plugin_checksums"):
        result = full.get(key)
        if result is None:
            items.append({"id": key, "status": "unknown"})
        else:
            items.append({"id": key, "status": "ok" if not result.get("count") else "warning", "count": result.get("count", 0),
                          "detail": result})
    items.append({"id": "salts", "status": "action", "apply": True})
    return items


def security_warnings(inventory: dict) -> Optional[int]:
    items = security_items(inventory)
    return sum(1 for item in items if item["status"] == "warning") if items else None


def _handle_security_scan(db: Session, task: _Task, params: dict) -> None:
    install, website = _install_and_site(db, task.job)
    refresh_install(db, task, install, website)
    task.stage("Comparing WordPress's files with wordpress.org")
    core = wp(website, install.path, ["core", "verify-checksums"], check=False)
    core_files = sorted({line.split(":", 2)[-1].strip() for line in (core.stdout + core.stderr).splitlines()
                         if line.startswith("Warning:") and ":" in line})[:200]
    core_result = {"count": len(core_files) if core.returncode != 0 else 0, "modified": core_files if core.returncode != 0 else [],
                   "error": "" if core.returncode == 0 or core_files else _clean_error(core.stderr or core.stdout)}
    task.stage("Comparing the plugins' files with wordpress.org")
    plugins = wp(website, install.path, ["plugin", "verify-checksums", "--all", "--format=json"], check=False)
    failures = [{"plugin": str(item.get("plugin_name", "")), "file": str(item.get("file", "")), "message": str(item.get("message", ""))}
                for item in (_json(plugins) or []) if isinstance(item, dict)]
    # Plugins not on wordpress.org cannot be compared: not a finding.
    failures = [item for item in failures if "Could not retrieve the checksums" not in item["message"]][:200]
    inventory = json.loads(install.inventory or "{}")
    inventory["security_full"] = {
        "scanned_at": datetime.utcnow().isoformat() + "Z",
        "core_checksums": core_result,
        "plugin_checksums": {"count": len(failures), "failures": failures},
    }
    install.inventory = json.dumps(inventory)
    db.commit()
    task.log(f"Core: {core_result['count']} file(s) differ; plugins: {len(failures)} file(s) differ")


HARDEN_ITEMS = {"debug", "file_edit", "config_mode", "search_visibility", "salts"}


def _handle_harden(db: Session, task: _Task, params: dict) -> None:
    install, website = _install_and_site(db, task.job)
    item = params.get("item")
    revert = bool(params.get("revert"))
    if item not in HARDEN_ITEMS:
        raise WpError("Unknown security item")
    inventory = json.loads(install.inventory or "{}")
    if item == "debug":
        task.stage("Debugging off")
        for name in ("WP_DEBUG", "WP_DEBUG_LOG"):
            config_set(website, install.path, name, "false")
    elif item == "file_edit":
        task.stage("Editing files from wp-admin " + ("allowed" if revert else "blocked"))
        config_set(website, install.path, "DISALLOW_FILE_EDIT", "false" if revert else "true")
    elif item == "config_mode":
        config_path = inventory.get("config_path") or ""
        if not config_path.endswith("/wp-config.php"):
            raise WpError("wp-config.php was not found: refresh first")
        task.stage("wp-config.php readable by its owner only")
        # As the site's own user, on its own file: chmod needs no privilege.
        wp(website, install.path, ["eval", f"chmod({json.dumps(config_path)}, 0640); echo 'ok';"])
    elif item == "search_visibility":
        task.stage("Hidden from search engines" if revert else "Visible to search engines")
        wp(website, install.path, ["option", "update", "blog_public", "0" if revert else "1"])
    elif item == "salts":
        task.stage("New security keys")
        wp(website, install.path, ["config", "shuffle-salts"])
    refresh_install(db, task, install, website)


HANDLERS = {
    "scan": _handle_scan,
    "refresh": _handle_refresh,
    "core": _handle_core,
    "maintenance": _handle_switch("maintenance"),
    "debug": _handle_switch("debug"),
    "plugins": _handle_items("plugin"),
    "themes": _handle_items("theme"),
    "update": _handle_update_everything,
    "security_scan": _handle_security_scan,
    "harden": _handle_harden,
}


# --- Answered at once: the sign-in link, a new password, a search -----------------------

SSO_SOURCE = r"""<?php
/*
 * Plugin Name: OPanel sign-in
 * Description: One-time sign-in links to wp-admin from OPanel's WordPress Manager. A link works once, for 60 seconds. Removing this file turns them off.
 */
if (!defined('ABSPATH')) {
    exit;
}
add_action('init', function () {
    if (empty($_GET['opanel_sso']) || !is_string($_GET['opanel_sso'])) {
        return;
    }
    $token = $_GET['opanel_sso'];
    if (!preg_match('/^[A-Za-z0-9_-]{20,100}$/', $token)) {
        return;
    }
    $key = 'opanel_sso_' . substr(hash('sha256', $token), 0, 40);
    $user_id = (int) get_transient($key);
    delete_transient($key);
    if (!$user_id) {
        wp_die(esc_html__('This sign-in link has expired or was already used. Open wp-admin again from the panel.'), '', array('response' => 403));
    }
    $user = get_user_by('id', $user_id);
    if (!$user) {
        return;
    }
    wp_set_current_user($user->ID);
    wp_set_auth_cookie($user->ID, false, is_ssl());
    do_action('wp_login', $user->user_login, $user);
    wp_safe_redirect(admin_url());
    exit;
}, 1);
"""


def _ensure_sso_plugin(website: Website, path: str) -> None:
    payload = base64.b64encode(SSO_SOURCE.encode()).decode()
    code = (
        "$d = WPMU_PLUGIN_DIR; if (!is_dir($d)) { wp_mkdir_p($d); } "
        f"$f = $d . '/{SSO_PLUGIN}'; $s = base64_decode('{payload}'); "
        "if (!is_file($f) || file_get_contents($f) !== $s) { file_put_contents($f, $s); } echo 'ok';"
    )
    wp(website, path, ["eval", code])


def login_url(install: WpInstall, website: Website, user_id: Optional[int] = None) -> str:
    """A link that signs in to wp-admin once, within 60 seconds."""
    inventory = json.loads(install.inventory or "{}")
    admins = inventory.get("admins") or []
    if user_id is None:
        if not admins:
            raise WpError("No administrator is known yet: refresh this WordPress first.")
        user_id = admins[0]["id"]
    if user_id not in {admin["id"] for admin in admins}:
        raise WpError("Choose one of this WordPress's administrators")
    _ensure_sso_plugin(website, install.path)
    token = secrets.token_urlsafe(32)
    key = "opanel_sso_" + hashlib.sha256(token.encode()).hexdigest()[:40]
    wp(website, install.path, ["eval", f"set_transient('{key}', {int(user_id)}, 60); echo 'ok';"])
    home = (install.url or inventory.get("url") or f"https://{website.domain}").rstrip("/")
    return f"{home}/?opanel_sso={token}"


def new_password(install: WpInstall, website: Website, user_id: int) -> tuple[str, str]:
    inventory = json.loads(install.inventory or "{}")
    admin = next((item for item in inventory.get("admins") or [] if item["id"] == user_id), None)
    if not admin:
        raise WpError("Choose one of this WordPress's administrators")
    alphabet = string.ascii_letters + string.digits + "!@#%^*_-+="
    password = "".join(secrets.choice(alphabet) for _ in range(20))
    wp(website, install.path, ["user", "update", str(int(user_id)), "--prompt=user_pass"],
       input=password + "\n", sensitive=True)
    return admin["login"], password


def search_wordpress_org(kind: str, query: str) -> list[dict]:
    """Plugins or themes on wordpress.org matching a search."""
    query = (query or "").strip()[:80]
    if len(query) < 2:
        return []
    what = "plugins" if kind == "plugin" else "themes"
    action = "query_plugins" if kind == "plugin" else "query_themes"
    params = {"action": action, "request[search]": query, "request[per_page]": "12",
              "request[fields][short_description]": "0", "request[fields][description]": "0",
              "request[fields][sections]": "0", "request[fields][icons]": "0"}
    url = f"https://api.wordpress.org/{what}/info/1.2/?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "OPanel WordPress Manager"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8", "replace"))
    except (OSError, ValueError) as exc:
        raise WpError(f"wordpress.org did not answer: {exc}") from exc
    items = data.get(what) or []
    return [{
        "slug": str(item.get("slug", "")),
        "name": re.sub(r"<[^>]+>", "", str(item.get("name", ""))),
        "version": str(item.get("version", "")),
        "rating": int(item.get("rating") or 0),
        "active_installs": item.get("active_installs"),
    } for item in items if isinstance(item, dict) and SLUG_RE.fullmatch(str(item.get("slug", "")))]
