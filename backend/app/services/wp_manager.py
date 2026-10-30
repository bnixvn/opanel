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
import os
import re
import secrets
import string
import threading
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
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

# Tasks of different WordPress run side by side (one at a time per WordPress,
# see _run): a bulk update of 110 sites two at a time took four hours on .41.
WORKERS = max(2, min(4, (os.cpu_count() or 2) // 3))
# A scan reads each WordPress in its own WP-CLI process; these run in parallel.
SCAN_WORKERS = max(2, min(6, (os.cpu_count() or 2) // 2))
_executor = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="opanel-wpm")
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
                              input=input, sensitive=sensitive, fallback=["wp", *argv], timeout=900)
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


# Everything read_inventory needs, in one WP-CLI process instead of sixteen:
# each one loaded WordPress again, and a check took 26 s on .41. The same
# sources WP-CLI's own commands read - the update transients after the same
# wp_*_check calls (which ask wordpress.org at most every 12 hours), the
# plugin and theme headers, the auto-update options, the .maintenance file.
INVENTORY_MARK = "OPANEL-INVENTORY:"
INVENTORY_PHP = r"""
require_once ABSPATH . 'wp-admin/includes/plugin.php';
require_once ABSPATH . 'wp-admin/includes/update.php';
require_once ABSPATH . WPINC . '/update.php';
global $wp_version;
$current = str_replace('-src', '', $wp_version);
$out = array('core_version' => $wp_version, 'core_offers' => array());
wp_version_check();
$core = get_site_transient('update_core');
if ($core && !empty($core->updates)) {
    $have = explode('.', $current);
    foreach ($core->updates as $offer) {
        if (empty($offer->version) || !version_compare($offer->version, $current, '>')) continue;
        $v = explode('.', $offer->version);
        $same = ($v[0] ?? '') === ($have[0] ?? '') && ($v[1] ?? '0') === ($have[1] ?? '0');
        $out['core_offers'][] = array('version' => $offer->version, 'update_type' => $same ? 'minor' : 'major');
    }
}
wp_update_plugins();
$updates = get_site_transient('update_plugins');
$auto = (array) get_site_option('auto_update_plugins', array());
$plugins = array();
foreach (get_plugins() as $file => $data) {
    $name = dirname($file) === '.' ? preg_replace('/\.php$/', '', basename($file)) : dirname($file);
    $status = is_plugin_active_for_network($file) ? 'active-network' : (is_plugin_active($file) ? 'active' : 'inactive');
    $offer = isset($updates->response[$file]) ? $updates->response[$file] : null;
    $plugins[] = array('name' => $name, 'title' => $data['Name'], 'status' => $status, 'version' => $data['Version'],
        'update' => $offer ? 'available' : 'none', 'update_version' => ($offer && isset($offer->new_version)) ? $offer->new_version : '',
        'auto_update' => in_array($file, $auto, true) ? 'on' : 'off');
}
foreach (get_mu_plugins() as $file => $data) {
    $plugins[] = array('name' => preg_replace('/\.php$/', '', basename($file)), 'title' => $data['Name'], 'status' => 'must-use',
        'version' => $data['Version'], 'update' => 'none', 'update_version' => '', 'auto_update' => 'off');
}
foreach (get_dropins() as $file => $data) {
    // WP-CLI lists a drop-in with no title; WordPress puts the file name there.
    $plugins[] = array('name' => $file, 'title' => '', 'status' => 'dropin', 'version' => $data['Version'],
        'update' => 'none', 'update_version' => '', 'auto_update' => 'off');
}
$out['plugins'] = $plugins;
wp_update_themes();
$theme_updates = get_site_transient('update_themes');
$theme_auto = (array) get_site_option('auto_update_themes', array());
$stylesheet = get_stylesheet();
$template = get_template();
$themes = array();
foreach (wp_get_themes() as $slug => $theme) {
    $offer = isset($theme_updates->response[$slug]) ? $theme_updates->response[$slug] : null;
    $themes[] = array('name' => $slug, 'title' => $theme->get('Name'), 'version' => $theme->get('Version'),
        'status' => $slug === $stylesheet ? 'active' : ($slug === $template ? 'parent' : 'inactive'),
        'update' => $offer ? 'available' : 'none', 'update_version' => ($offer && isset($offer['new_version'])) ? $offer['new_version'] : '',
        'auto_update' => in_array($slug, $theme_auto, true) ? 'on' : 'off');
}
$out['themes'] = $themes;
$admins = array();
foreach (get_users(array('role' => 'administrator', 'fields' => array('ID', 'user_login', 'user_email', 'display_name'))) as $user) {
    $admins[] = array('ID' => (int) $user->ID, 'user_login' => $user->user_login, 'user_email' => $user->user_email, 'display_name' => $user->display_name);
}
$out['admins'] = $admins;
$out['title'] = (string) get_option('blogname');
$out['home'] = (string) get_option('home');
$out['blog_public'] = (string) get_option('blog_public');
$maintenance = false;
if (is_file(ABSPATH . '.maintenance')) {
    $text = (string) @file_get_contents(ABSPATH . '.maintenance');
    if (preg_match('/upgrading\s*=\s*(\d+)\s*;/i', $text, $m)) $maintenance = (time() - (int) $m[1]) < 600;
}
$out['maintenance'] = $maintenance;
$out['debug'] = defined('WP_DEBUG') && WP_DEBUG;
$out['auto_update_core'] = defined('WP_AUTO_UPDATE_CORE') ? (WP_AUTO_UPDATE_CORE === true ? 'true' : (WP_AUTO_UPDATE_CORE === false ? 'false' : (string) WP_AUTO_UPDATE_CORE)) : '';
$out['file_edit_disallowed'] = defined('DISALLOW_FILE_EDIT') && DISALLOW_FILE_EDIT;
$config = '';
if (is_file(ABSPATH . 'wp-config.php')) {
    $config = ABSPATH . 'wp-config.php';
} elseif (is_file(dirname(ABSPATH) . '/wp-config.php') && !is_file(dirname(ABSPATH) . '/wp-settings.php')) {
    $config = dirname(ABSPATH) . '/wp-config.php';
}
$out['config_path'] = $config;
$out['config_mode'] = $config ? sprintf('%o', fileperms($config) & 0777) : '';
echo "OPANEL-INVENTORY:" . json_encode($out);
"""


def _read_inventory_once(website: Website, path: str) -> Optional[dict]:
    """The inventory from one `wp eval`, or None for the per-command read."""
    result = wp(website, path, ["eval", INVENTORY_PHP], check=False)
    text = result.stdout or ""
    if result.returncode != 0 or INVENTORY_MARK not in text:
        return None
    try:
        data = json.loads(text.split(INVENTORY_MARK, 1)[1].strip())
    except ValueError:
        return None
    if not isinstance(data, dict) or not data.get("core_version"):
        return None
    offers = [item for item in data.get("core_offers") or [] if isinstance(item, dict) and item.get("version")]
    latest = max((item["version"] for item in offers), key=_version_key, default="")
    minor = max((item["version"] for item in offers if item.get("update_type") == "minor"), key=_version_key, default="")
    auto_value = str(data.get("auto_update_core") or "").strip("'\"").lower()
    return {
        "core_version": str(data["core_version"]),
        "core_update": latest,
        "core_minor_update": minor,
        "title": str(data.get("title") or ""),
        "url": str(data.get("home") or ""),
        "plugins": [_item(entry) for entry in data.get("plugins") or [] if isinstance(entry, dict)],
        "themes": [_item(entry) for entry in data.get("themes") or [] if isinstance(entry, dict)],
        "admins": [{"id": int(entry.get("ID") or 0), "login": entry.get("user_login", ""), "email": entry.get("user_email", ""),
                    "name": entry.get("display_name", "")} for entry in data.get("admins") or [] if isinstance(entry, dict)],
        "maintenance": bool(data.get("maintenance")),
        "debug": bool(data.get("debug")),
        "auto_update_core": "all" if auto_value in {"1", "true"} else "off" if auto_value in {"false", "0"} else "minor",
        "file_edit_disallowed": bool(data.get("file_edit_disallowed")),
        "blog_public": str(data.get("blog_public", "1")) != "0",
        "config_path": str(data.get("config_path") or ""),
        "config_mode": str(data.get("config_mode") or ""),
    }


def read_inventory(website: Website, path: str) -> dict:
    """What a WordPress runs: one WP-CLI process, or command by command when
    that one cannot run (a WordPress too old or too broken for it)."""
    inventory = _read_inventory_once(website, path)
    return inventory if inventory is not None else _read_inventory_slow(website, path)


def _read_inventory_slow(website: Website, path: str) -> dict:
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
    # What a check does not read: the last full security check, the restore
    # points, and whether one is taken before an update.
    for key in ("security_full", "points", "auto_points"):
        if previous.get(key) is not None and key not in inventory:
            inventory[key] = previous[key]
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
    # WP-CLI runs in parallel, one process per WordPress; the database is
    # written here, in this thread, which owns the session. 170 WordPress took
    # 2 h 10 min one after another on .41.
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS, thread_name_prefix="opanel-wpm-scan") as pool:
        futures = {pool.submit(read_inventory, _SiteRef(website), row.path): (row, website) for row, website in checked}
        done = 0
        for future in as_completed(futures):
            row, website = futures[future]
            done += 1
            task.stage(f"Checking {row.url or website.domain} ({done}/{len(checked)})")
            try:
                apply_inventory(row, future.result())
            except Exception as exc:
                row.last_error = str(exc)[:2000]
                row.last_checked_at = datetime.utcnow()
                task.log(f"{website.domain}/{row.rel_path}: {exc}")
            db.commit()


class _SiteRef:
    """What WP-CLI needs of a website, read before it leaves this thread: the
    worker threads do not touch the database session."""

    def __init__(self, website: Website):
        self.domain = website.domain
        self.root_path = website.root_path
        self.linux_user = site_users.require_site_linux_user(website)


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
        _point_first(db, task, params, install, website, "core update")
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
        if action in ("update", "update_all"):
            _point_first(db, task, params, install, website, f"{noun} update")
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
    _point_first(db, task, params, install, website, "update everything")
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


# --- Restore points ---------------------------------------------------------------------
# A WordPress's files without its uploads and caches, and its database, in the
# account's own home - taken before an update, and on request; the newest two
# are kept. Made and put back as the account itself (opanel-helper's
# wp-point-*), so they count against its disk like anything else of its own.

POINTS_DIR = ".opanel-wp-points"
KEEP_POINTS = 2


def _point_dir(website: Website, install: WpInstall, name: str) -> str:
    key = hashlib.sha256(install.path.encode()).hexdigest()[:16]
    # The helper takes /home/<user>/... and nothing else.
    return f"/home/{_linux_user(website)}/{POINTS_DIR}/{key}/{name}"


def _points(install: WpInstall) -> list[dict]:
    return list(json.loads(install.inventory or "{}").get("points") or [])


def _save_points(db: Session, install: WpInstall, points: list[dict]) -> None:
    inventory = json.loads(install.inventory or "{}")
    inventory["points"] = points
    install.inventory = json.dumps(inventory)
    db.commit()


def create_point(db: Session, task: _Task, install: WpInstall, website: Website, reason: str) -> dict:
    name = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    folder = _point_dir(website, install, name)
    task.stage("Taking a restore point: the files")
    result = shell.privileged("wp-point-create", helper_args=[_linux_user(website), install.path, folder], check=False)
    if result.returncode != 0:
        raise WpError("The restore point could not be taken: " + _clean_error(result.stderr or result.stdout))
    lines = [line for line in (result.stdout or "").split() if line.isdigit()]
    task.stage("Taking a restore point: the database")
    wp(website, install.path, ["db", "export", f"{folder}/db.sql"])
    point = {"name": name, "created_at": datetime.utcnow().isoformat() + "Z", "reason": reason,
             "size_bytes": int(lines[-1]) if lines else None, "core_version": install.core_version}
    points = _points(install) + [point]
    # The newest are kept; older points go.
    for old in points[:-KEEP_POINTS]:
        shell.privileged("wp-point-delete", helper_args=[_linux_user(website), _point_dir(website, install, old["name"])], check=False)
    _save_points(db, install, points[-KEEP_POINTS:])
    task.log(f"Restore point {name} taken")
    return point


def _handle_point_create(db: Session, task: _Task, params: dict) -> None:
    install, website = _install_and_site(db, task.job)
    create_point(db, task, install, website, "manual")


def _handle_point_restore(db: Session, task: _Task, params: dict) -> None:
    install, website = _install_and_site(db, task.job)
    point = next((item for item in _points(install) if item["name"] == params.get("name")), None)
    if not point:
        raise WpError("That restore point is gone")
    folder = _point_dir(website, install, point["name"])
    task.stage("Putting the files back")
    result = shell.privileged("wp-point-restore", helper_args=[_linux_user(website), install.path, folder], check=False)
    if result.returncode != 0:
        raise WpError("The files could not be put back: " + _clean_error(result.stderr or result.stdout))
    task.stage("Putting the database back")
    wp(website, install.path, ["db", "import", f"{folder}/db.sql"])
    wp(website, install.path, ["cache", "flush"], check=False)
    refresh_install(db, task, install, website)


def _handle_point_delete(db: Session, task: _Task, params: dict) -> None:
    install, website = _install_and_site(db, task.job)
    points = _points(install)
    point = next((item for item in points if item["name"] == params.get("name")), None)
    if not point:
        return
    task.stage("Deleting the restore point")
    shell.privileged("wp-point-delete", helper_args=[_linux_user(website), _point_dir(website, install, point["name"])], check=False)
    _save_points(db, install, [item for item in points if item["name"] != point["name"]])


def auto_points(install: WpInstall) -> bool:
    """Whether a restore point is taken before every update: off unless this
    WordPress's owner turned it on (operator, 2026-10-24; it was on by default
    from 2026-10-19). A point is still taken by hand at any time."""
    return json.loads(install.inventory or "{}").get("auto_points") is True


def set_auto_points(db: Session, install: WpInstall, on: bool) -> None:
    inventory = json.loads(install.inventory or "{}")
    inventory["auto_points"] = bool(on)
    install.inventory = json.dumps(inventory)
    db.commit()


def _point_first(db: Session, task: _Task, params: dict, install: WpInstall, website: Website, reason: str) -> None:
    """Before an update: a restore point, unless the task was asked not to or
    the WordPress has them switched off."""
    if params.get("restore_point", True) and auto_points(install):
        create_point(db, task, install, website, reason)
    else:
        task.log("No restore point: switched off for this WordPress")


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


def _parse_core_checksums(output: str) -> dict:
    """Categorise the Warning: lines `wp core verify-checksums` emits.

    Only "doesn't verify against checksum" means a core file actually DIFFERS
    from wordpress.org. "doesn't exist" is a missing core file - routinely
    xmlrpc.php, readme.html or license.txt that a download left out or hardening
    removed, not a modification. "should not exist" is an extra file, and "could
    not retrieve the checksums" is a version/network problem. The old parser
    counted every Warning: line as a modified core file, so a re-uploaded site
    merely missing readme.html reported "core files differ from wordpress.org"
    when nothing had changed.
    """
    modified: list[str] = []
    missing: list[str] = []
    extra: list[str] = []
    unverifiable = False
    for raw in (output or "").splitlines():
        line = raw.strip()
        if not line.startswith("Warning:"):
            continue
        low = line.lower()
        tail = line.split(":")[-1].strip()
        if "verify against checksum" in low:
            modified.append(tail)
        elif "should not exist" in low or "shouldn't exist" in low:
            extra.append(tail)
        elif "doesn't exist" in low or "does not exist" in low:
            missing.append(tail)
        elif "could not retrieve the checksums" in low:
            unverifiable = True
    return {
        "count": len(set(modified)),
        "modified": sorted(set(modified))[:200],
        "missing": sorted(set(missing))[:200],
        "extra": sorted(set(extra))[:200],
        "unverifiable": unverifiable,
    }


def _handle_security_scan(db: Session, task: _Task, params: dict) -> None:
    install, website = _install_and_site(db, task.job)
    refresh_install(db, task, install, website)
    task.stage("Comparing WordPress's files with wordpress.org")
    core = wp(website, install.path, ["core", "verify-checksums"], check=False)
    parsed = _parse_core_checksums(core.stdout + core.stderr)
    core_result = {
        "count": parsed["count"],
        "modified": parsed["modified"],
        "missing": parsed["missing"],
        "extra": parsed["extra"],
        "error": (_clean_error(core.stderr or core.stdout)
                  if (parsed["unverifiable"] and not parsed["count"]
                      and not parsed["missing"] and not parsed["extra"]) else ""),
    }
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
    task.log(
        f"Core: {core_result['count']} file(s) differ, {len(core_result['missing'])} missing, "
        f"{len(core_result['extra'])} extra; plugins: {len(failures)} file(s) differ"
    )


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
    "point_create": _handle_point_create,
    "point_restore": _handle_point_restore,
    "point_delete": _handle_point_delete,
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
