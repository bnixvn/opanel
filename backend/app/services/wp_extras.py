"""WordPress Manager, further (as SNPanel's, 2026-10-17): staging copies, a
plugin or theme from a .zip, the LiteSpeed Cache plugin, a daily check.

A staging copy is the WordPress - files and database - copied into another
website of the same account whose folder holds nothing yet, with its address
put in and search engines asked to stay away; pushed back, it replaces the
live site's files (its wp-config.php kept) and database, a restore point of
the live site taken first. Every file is copied, and every WP-CLI command
run, as the account's own Linux user.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from app.models.entities import DatabaseAccount, Website, WpInstall
from app.services import da_import, mariadb, openlitespeed, site_users, wp_manager
from app.services.shell import shell

STAGE_DIR = ".opanel-wp-stage"
UPLOAD_MAX_BYTES = 64 * 1024 * 1024
LSCACHE_SLUG = "litespeed-cache"
DAILY = timedelta(hours=24)
DAILY_PER_TICK = 3
# What an empty new website's folder may hold: the panel's own page and
# certificate folder, nothing of the customer's.
EMPTY_ENTRIES = {".well-known", "cgi-bin", "index.html", ".htaccess"}


# --- where a copy can go ---------------------------------------------------------------------

def folder_is_empty(website: Website) -> tuple[bool, str]:
    """Whether the website's public folder holds nothing but what a new website gets."""
    root = wp_manager.public_root(website)
    try:
        entries = list(root.iterdir())
    except FileNotFoundError:
        return True, ""
    except OSError:
        return False, "Its folder cannot be read"
    for entry in entries:
        if entry.name not in EMPTY_ENTRIES:
            return False, "Its folder is not empty"
        if entry.name == "cgi-bin" and any(entry.iterdir()):
            return False, "Its folder is not empty"
        if entry.name == "index.html":
            try:
                text = entry.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return False, "Its folder is not empty"
            # The panel's placeholder names the domain and the panel; a page of the customer's does not.
            if website.domain not in text or len(text) > 64 * 1024:
                return False, "Its index.html is not the panel's placeholder"
    return True, ""


def clone_targets(db: Session, install: WpInstall, website: Website) -> list[dict]:
    """The account's other websites, each with whether it can take a copy."""
    out = []
    for site in db.query(Website).filter(Website.owner_id == website.owner_id, Website.id != website.id).order_by(Website.domain):
        reason = ""
        if (site.linux_user or "") != (website.linux_user or ""):
            reason = "It runs as another Linux user"
        elif (site.app_type or "wordpress") == "static":
            reason = "It is a static website: it does not run PHP"
        elif db.query(WpInstall).filter(WpInstall.website_id == site.id, WpInstall.rel_path == "", WpInstall.status == "ok").first():
            reason = "It has a WordPress already"
        else:
            ok, why = folder_is_empty(site)
            reason = "" if ok else why
        out.append({"id": site.id, "domain": site.domain, "ok": not reason, "reason": reason})
    return out


# --- the account's own scratch folder ---------------------------------------------------------

def _stage_dir(website: Website) -> str:
    user = wp_manager._linux_user(website)
    result = shell.privileged("wp-stage-dir", helper_args=[user], check=False)
    if result.returncode != 0:
        raise wp_manager.WpError("The export folder could not be made: " + wp_manager._clean_error(result.stderr or result.stdout))
    return f"/home/{user}/{STAGE_DIR}"


def _stage_clear(website: Website) -> None:
    shell.privileged("wp-stage-clear", helper_args=[wp_manager._linux_user(website)], check=False)


def _copy_files(website: Website, source: str, target: str, *, keep_config: bool) -> None:
    result = shell.privileged("wp-copy", helper_args=[wp_manager._linux_user(website), source, target,
                                                      "keep-config" if keep_config else "all"], check=False)
    if result.returncode != 0:
        raise wp_manager.WpError("The files could not be copied: " + wp_manager._clean_error(result.stderr or result.stdout))


def _scheme_url(website: Website, rel_path: str) -> str:
    scheme = "https" if website.ssl_enabled else "http"
    return f"{scheme}://{website.domain}" + (f"/{rel_path}" if rel_path else "")


def _search_replace(website: Website, path: str, old: str, new: str, task) -> None:
    """The old address for the new one, everywhere WordPress keeps it - serialised data too."""
    old = old.rstrip("/")
    new = new.rstrip("/")
    if not old or old == new:
        return
    host_old = re.sub(r"^https?://", "", old)
    host_new = re.sub(r"^https?://", "", new)
    # Plain, as JSON writes it (https:\/\/...), and scheme-relative.
    escaped_new = new.replace("/", "\\/")
    pairs = [(f"https://{host_old}", new), (f"http://{host_old}", new),
             (f"https:\\/\\/{host_old}", escaped_new), (f"http:\\/\\/{host_old}", escaped_new),
             (f"//{host_old}", f"//{host_new}")]
    for before, after in pairs:
        result = wp_manager.wp(website, path, ["search-replace", before, after, "--all-tables-with-prefix",
                                               "--skip-columns=guid", "--precise", "--quiet"], check=False)
        if result.returncode != 0:
            task.log(f"search-replace {before}: {wp_manager._clean_error(result.stderr or result.stdout)}")


def _new_database(db: Session, website: Website) -> dict:
    """A database for the copy, the owner's limit checked again now."""
    from app.models.entities import User

    owner = db.get(User, website.owner_id)
    limit = getattr(owner, "database_limit", 0) or 0
    if owner and owner.role != "admin" and limit > 0:
        used = db.query(DatabaseAccount).filter(DatabaseAccount.owner_id == owner.id).count()
        if used >= limit:
            raise wp_manager.WpError(f"Database limit reached ({used}/{limit})")
    base = mariadb.safe_db_identifier(website.domain, "wp")[:40]
    for _ in range(5):
        name = f"{base}_{secrets.token_hex(2)}"[:63]
        if not db.query(DatabaseAccount).filter(DatabaseAccount.db_name == name).first() and not mariadb.identifier_in_use(name, name):
            break
    else:
        raise wp_manager.WpError("No free database name was found")
    password = mariadb.random_password()
    mariadb.create_database_credentials(name, name, password)
    from app.core.secrets import encrypt

    db.add(DatabaseAccount(owner_id=website.owner_id, website_id=website.id, db_name=name, db_user=name,
                           db_password=encrypt(password)))
    db.commit()
    return {"db_name": name, "db_user": name, "db_password": password}


def _point_config_at(website: Website, path: str, creds: dict) -> None:
    """The copy's wp-config.php given its own database, written as the site user."""
    root = Path(website.root_path).resolve()
    write = da_import._site_config_writer(str(root), website.linux_user)
    da_import._update_config_dir(Path(path), creds["db_name"], creds["db_user"], creds["db_password"], write)


# --- the tasks ---------------------------------------------------------------------------------

def handle_clone(db: Session, task, params: dict) -> None:
    install, website = wp_manager._install_and_site(db, task.job)
    target = db.get(Website, int(params.get("website_id") or 0))
    if target is None:
        raise wp_manager.WpError("That website is gone")
    allowed = {item["id"]: item for item in clone_targets(db, install, website)}
    if target.id not in allowed or not allowed[target.id]["ok"]:
        raise wp_manager.WpError(allowed.get(target.id, {}).get("reason") or "That website cannot take a copy")
    target_path = str(wp_manager.public_root(target))
    old_url = (json.loads(install.inventory or "{}").get("url") or install.url or _scheme_url(website, install.rel_path))
    new_url = _scheme_url(target, "")
    task.stage("Making a database for the copy")
    creds = _new_database(db, target)
    try:
        task.stage("Copying the files")
        _copy_files(website, install.path, target_path, keep_config=False)
        task.stage("Exporting the database")
        stage = _stage_dir(website)
        dump = f"{stage}/clone-{secrets.token_hex(6)}.sql"
        wp_manager.wp(website, install.path, ["db", "export", dump])
        task.stage("Giving the copy its own database")
        _point_config_at(target, target_path, creds)
        wp_manager.wp(target, target_path, ["db", "import", dump])
        task.stage(f"Putting in its address: {new_url}")
        _search_replace(target, target_path, old_url, new_url, task)
        wp_manager.wp(target, target_path, ["option", "update", "blog_public", "0"], check=False)
        wp_manager.wp(target, target_path, ["cache", "flush"], check=False)
    except Exception:
        task.log("The copy was not finished; its database is kept for a look, its files stay in the website's folder")
        raise
    finally:
        _stage_clear(website)
    copy = db.query(WpInstall).filter(WpInstall.website_id == target.id, WpInstall.rel_path == "").first()
    if copy is None:
        copy = WpInstall(website_id=target.id, owner_id=target.owner_id, rel_path="", path=target_path)
        db.add(copy)
    copy.status, copy.state, copy.staging_of = "ok", "installed", install.id
    # The copy's website now serves WordPress: its front controller and CSP.
    target.app_type = "wordpress"
    target.nginx_rewrite_mode = "front_controller"
    db.commit()
    try:
        from app.api.websites import _rewrite_website_vhost

        _rewrite_website_vhost(target)
    except (RuntimeError, ValueError) as exc:
        task.log(f"The copy's web server settings were not rewritten: {exc}")
    wp_manager.refresh_install(db, task, copy, target)
    task.log(f"Staging copy ready at {new_url}")


def handle_push(db: Session, task, params: dict) -> None:
    staging, staging_site = wp_manager._install_and_site(db, task.job)
    live = db.get(WpInstall, staging.staging_of or 0)
    if live is None:
        raise wp_manager.WpError("This is not a staging copy, or its live site is gone")
    live_site = db.get(Website, live.website_id)
    if live_site is None or (live_site.linux_user or "") != (staging_site.linux_user or ""):
        raise wp_manager.WpError("The live site is gone, or runs as another Linux user")
    staging_url = json.loads(staging.inventory or "{}").get("url") or _scheme_url(staging_site, staging.rel_path)
    live_inv = json.loads(live.inventory or "{}")
    live_url = live_inv.get("url") or _scheme_url(live_site, live.rel_path)
    was_public = wp_manager.wp(live_site, live.path, ["option", "get", "blog_public"], check=False).stdout.strip() or "1"
    wp_manager.create_point(db, task, live, live_site, "before a push from staging")
    try:
        task.stage("Copying the files to the live site")
        _copy_files(live_site, staging.path, live.path, keep_config=True)
        task.stage("Copying the database to the live site")
        stage = _stage_dir(live_site)
        dump = f"{stage}/push-{secrets.token_hex(6)}.sql"
        wp_manager.wp(staging_site, staging.path, ["db", "export", dump])
        wp_manager.wp(live_site, live.path, ["db", "import", dump])
        task.stage(f"Putting back its address: {live_url}")
        _search_replace(live_site, live.path, staging_url, live_url, task)
        wp_manager.wp(live_site, live.path, ["option", "update", "blog_public", was_public if was_public in {"0", "1"} else "1"], check=False)
        wp_manager.wp(live_site, live.path, ["cache", "flush"], check=False)
    finally:
        _stage_clear(live_site)
    wp_manager.refresh_install(db, task, live, live_site)
    task.log(f"{live_url} now runs the staging copy; a restore point of before is on its Restore points tab")


def handle_upload(db: Session, task, params: dict) -> None:
    install, website = wp_manager._install_and_site(db, task.job)
    kind = "plugin" if params.get("kind") == "plugin" else "theme"
    staged = str(params.get("file") or "")
    name = re.sub(r"[^A-Za-z0-9._-]", "-", Path(str(params.get("name") or "upload.zip")).name)[:80] or "upload.zip"
    if not name.lower().endswith(".zip"):
        name += ".zip"
    try:
        task.stage(f"Taking in {name}")
        user = wp_manager._linux_user(website)
        result = shell.privileged("wp-upload-place", helper_args=[user, staged, name], check=False)
        if result.returncode != 0:
            raise wp_manager.WpError("The file could not be taken in: " + wp_manager._clean_error(result.stderr or result.stdout))
        placed = f"/home/{user}/{STAGE_DIR}/{name}"
        task.stage(f"Installing {name}")
        args = [kind, "install", placed, "--force"] + (["--activate"] if params.get("activate") else [])
        out = wp_manager.wp(website, install.path, args, skip=not params.get("activate"))
        task.log(wp_manager._short(out.stdout))
    finally:
        Path(staged).unlink(missing_ok=True)
        _stage_clear(website)
    wp_manager.refresh_install(db, task, install, website)


def handle_lscache(db: Session, task, params: dict) -> None:
    install, website = wp_manager._install_and_site(db, task.job)
    action = params.get("action")
    task.stage(f"LiteSpeed Cache: {action}")
    if action == "install":
        out = wp_manager.wp(website, install.path, ["plugin", "install", LSCACHE_SLUG, "--activate"], skip=False)
    elif action == "activate":
        out = wp_manager.wp(website, install.path, ["plugin", "activate", LSCACHE_SLUG], skip=False)
    elif action == "deactivate":
        out = wp_manager.wp(website, install.path, ["plugin", "deactivate", LSCACHE_SLUG], skip=False)
    elif action == "purge":
        # The cache is on this server, so it is emptied here first. The
        # plugin's own purge asks the site's address over HTTP, which fails
        # while the domain points elsewhere or nowhere -- a site just moved
        # here -- so it only follows, and a failure of it is only noted.
        files = openlitespeed.purge_site_cache(website.domain)
        task.log(f"Emptied the website's LiteSpeed cache: {files} cached page(s)")
        try:
            # The plugin's own command needs the plugin loaded.
            out = wp_manager.wp(website, install.path, ["litespeed-purge", "all"], skip=False)
        except wp_manager.WpError as exc:
            task.log(f"The plugin's own purge did not run: {wp_manager._short(str(exc))}")
            wp_manager.refresh_install(db, task, install, website)
            return
    else:
        raise wp_manager.WpError("Unknown LiteSpeed Cache action")
    task.log(wp_manager._short(out.stdout))
    wp_manager.refresh_install(db, task, install, website)


wp_manager.HANDLERS.update({
    "clone": handle_clone,
    "push": handle_push,
    "upload": handle_upload,
    "lscache": handle_lscache,
})


# --- the daily check -----------------------------------------------------------------------------

def daily_tick(db: Optional[Session] = None) -> int:
    """A few WordPress installs each minute whose last check is a day old: read again."""
    if not wp_manager.enabled():
        return 0
    own = db is None
    if own:
        from app.core.database import SessionLocal
        db = SessionLocal()
    checked = 0
    try:
        cutoff = datetime.utcnow() - DAILY
        due = (db.query(WpInstall).filter(WpInstall.status == "ok", WpInstall.state == "installed")
               .filter((WpInstall.last_checked_at.is_(None)) | (WpInstall.last_checked_at < cutoff))
               .order_by(WpInstall.last_checked_at.asc().nullsfirst()).limit(DAILY_PER_TICK).all())
        for install in due:
            website = db.get(Website, install.website_id)
            if website is None:
                continue
            try:
                wp_manager.refresh_install(db, None, install, website)
            except Exception:  # noqa: BLE001 - recorded on the install, the next one goes on
                pass
            checked += 1
        return checked
    finally:
        if own:
            db.close()
