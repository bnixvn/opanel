"""WordPress Manager, further (as SNPanel's, 2026-10-17): a staging copy and a push
back, a plugin or theme from a .zip, the LiteSpeed Cache plugin, the daily check.

Every file is copied and every WP-CLI command run as the account's own user;
a copy goes only into another website of the same account whose folder holds
nothing yet; a push takes a restore point of the live site first and keeps
its wp-config.php.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models.entities import DatabaseAccount, Website, WpInstall
from app.services import da_import, mariadb, wp_extras, wp_manager
from app.tests.test_wordpress_manager import env  # noqa: F401 - the shared fixture

HELPER = (Path(__file__).resolve().parents[3] / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")


def _scan(env):
    assert env.as_user("alice").post("/api/wordpress/scan", json={}).status_code == 200
    return env.db.query(WpInstall).filter_by(website_id=env.sites["alice"].id, rel_path="").one()


def _staging_site(env, *, placeholder=True):
    root = env.tmp / "alice" / "staging.alice.example"
    (root / "public_html" / ".well-known").mkdir(parents=True)
    if placeholder:
        (root / "public_html" / "index.html").write_text("<h1>staging.alice.example is ready</h1>", encoding="utf-8")
    site = Website(domain="staging.alice.example", owner_id=env.people["alice"].id, root_path=str(root),
                   linux_user="alice", php_version="8.3", app_type="php")
    env.db.add(site)
    env.db.commit()
    return site


@pytest.fixture
def quiet(monkeypatch):
    """MariaDB, the config writer and the vhost, as a test sees them."""
    made = []
    monkeypatch.setattr(mariadb, "identifier_in_use", lambda *a: "")
    monkeypatch.setattr(mariadb, "create_database_credentials", lambda *a, **k: made.append(a))
    written = []
    monkeypatch.setattr(da_import, "_update_config_dir", lambda directory, name, user, password, write: written.append((str(directory), name)) or [])
    import app.api.websites as websites_api
    monkeypatch.setattr(websites_api, "_rewrite_website_vhost", lambda website, **kw: "")
    return SimpleNamespace(made=made, written=written)


def test_only_an_empty_website_of_the_same_account_takes_a_copy(env):
    install = _scan(env)
    empty = _staging_site(env)
    targets = {item["domain"]: item for item in wp_extras.clone_targets(env.db, install, env.sites["alice"])}
    assert targets["staging.alice.example"]["ok"] is True
    (Path(empty.root_path) / "public_html" / "shop.php").write_text("<?php")
    targets = {item["domain"]: item for item in wp_extras.clone_targets(env.db, install, env.sites["alice"])}
    assert targets["staging.alice.example"]["ok"] is False
    assert "bob.example" not in targets  # another account's website is never offered


def test_a_staging_copy_is_made_as_the_account(env, quiet):
    install = _scan(env)
    target = _staging_site(env)
    answer = env.as_user("alice").post(f"/api/wordpress/installs/{install.id}/clone", json={"website_id": target.id})
    assert answer.status_code == 200, answer.text
    assert answer.json()["job"]["status"] == "completed", answer.json()["job"]["error"]
    commands = [(call["command"], call["user"], call["args"][:3]) for call in env.calls]
    assert ("wp-copy", "alice", [install.path, str(Path(target.root_path) / "public_html"), "all"]) in commands
    exports = [call for call in env.calls if call["command"] == "wp-site" and call["args"][:2] == ["db", "export"]]
    imports = [call for call in env.calls if call["command"] == "wp-site" and call["args"][:2] == ["db", "import"]]
    assert exports and imports and all(call["user"] == "alice" for call in exports + imports)
    replaced = [call["args"][1:3] for call in env.calls if call["command"] == "wp-site" and call["args"][0] == "search-replace"]
    assert ["https://alice.example", "http://staging.alice.example"] in replaced
    assert any(call["args"][:4] == ["option", "update", "blog_public", "0"] for call in env.calls if call["command"] == "wp-site")
    copy = env.db.query(WpInstall).filter_by(website_id=target.id).one()
    assert copy.staging_of == install.id
    assert env.db.query(DatabaseAccount).filter_by(website_id=target.id).count() == 1
    detail = env.as_user("alice").get(f"/api/wordpress/installs/{install.id}").json()
    assert detail["staging_copies"][0]["domain"] == "staging.alice.example"


def test_a_push_takes_a_restore_point_and_keeps_the_live_config(env, quiet):
    install = _scan(env)
    target = _staging_site(env)
    env.as_user("alice").post(f"/api/wordpress/installs/{install.id}/clone", json={"website_id": target.id})
    copy = env.db.query(WpInstall).filter_by(website_id=target.id).one()
    env.calls.clear()
    answer = env.as_user("alice").post(f"/api/wordpress/installs/{copy.id}/push")
    assert answer.status_code == 200 and answer.json()["job"]["status"] == "completed", answer.text
    commands = [call["command"] for call in env.calls]
    assert commands.index("wp-point-create") < commands.index("wp-copy")
    copy_call = next(call for call in env.calls if call["command"] == "wp-copy")
    assert copy_call["args"][2] == "keep-config" and copy_call["args"][1] == install.path
    # Only a staging copy is pushed.
    assert env.as_user("alice").post(f"/api/wordpress/installs/{install.id}/push").status_code == 400


def test_a_zip_is_taken_in_and_installed_as_the_account(env, monkeypatch, tmp_path):
    install = _scan(env)
    monkeypatch.setattr("app.api.wordpress_manager.UPLOAD_DIR", tmp_path / "uploads")
    client = env.as_user("alice")
    bad = client.post(f"/api/wordpress/installs/{install.id}/plugins/upload", files={"file": ("x.zip", b"not a zip")})
    assert bad.status_code == 400
    good = client.post(f"/api/wordpress/installs/{install.id}/plugins/upload",
                       files={"file": ("My Plugin.zip", b"PK\x03\x04rest")}, data={"activate": "1"})
    assert good.status_code == 200 and good.json()["job"]["status"] == "completed", good.text
    place = next(call for call in env.calls if call["command"] == "wp-upload-place")
    assert place["user"] == "alice" and place["args"][1] == "My-Plugin.zip"
    install_call = next(call for call in env.calls if call["command"] == "wp-site" and call["args"][:2] == ["plugin", "install"])
    assert install_call["args"][2] == "/home/alice/.opanel-wp-stage/My-Plugin.zip" and "--activate" in install_call["args"]
    assert not list((tmp_path / "uploads").iterdir())  # the waiting copy is gone


def test_litespeed_cache_purge_loads_the_plugin(env):
    install = _scan(env)
    answer = env.as_user("alice").post(f"/api/wordpress/installs/{install.id}/lscache", json={"action": "purge"})
    assert answer.status_code == 200, answer.text
    purge = next(call for call in env.calls if call["command"] == "wp-site" and call["args"][0] == "litespeed-purge")
    assert "--skip-plugins" not in purge["args"]
    assert env.as_user("alice").post(f"/api/wordpress/installs/{install.id}/lscache", json={"action": "rm -rf"}).status_code == 422


def test_the_daily_check_reads_a_day_old_install_again(env):
    install = _scan(env)
    install.last_checked_at = datetime.utcnow() - timedelta(days=2)
    env.db.commit()
    env.calls.clear()
    assert wp_extras.daily_tick(env.db) == 1
    assert any(call["args"][:2] == ["core", "version"] for call in env.calls if call["command"] == "wp-site")
    assert wp_extras.daily_tick(env.db) == 0


def test_the_helper_copies_as_the_account_and_never_across_accounts():
    body = HELPER.split("  wp-copy)", 1)[1].split(";;", 1)[0]
    assert 'from=$(require_managed_path "$2" "$user")' in body and 'to=$(require_managed_path "$3" "$user")' in body
    assert 'runuser -u "$user" -- rsync' in body and "the two folders overlap" in body
    place = HELPER.split("  wp-upload-place)", 1)[1].split(";;", 1)[0]
    assert "^/var/lib/opanel/wp-uploads/[a-f0-9]{24}\\.zip$" in place


def test_a_second_wordpress_in_a_folder_gets_its_own_database(env, monkeypatch):
    """The name came from the domain alone: the folder's install re-passworded
    the root one's database user and found WordPress already in it."""
    from app.services import wordpress

    made, installs = [], []
    monkeypatch.setattr(mariadb, "identifier_in_use", lambda name, user: "")
    monkeypatch.setattr(mariadb, "create_database", lambda seed, db_name=None, **kw: made.append(db_name) or
                        {"db_name": db_name, "db_user": mariadb.safe_db_identifier(db_name, "u"), "db_password": "x" * 20})
    monkeypatch.setattr(wordpress, "install_wordpress", lambda *a, **kw: installs.append(kw.get("subfolder")))
    import app.api.websites as websites_api
    monkeypatch.setattr(websites_api, "_rewrite_website_vhost", lambda website, **kw: "")
    site = env.sites["alice"]
    (Path(site.root_path) / "public_html" / "wp-config.php").unlink()
    client = env.as_user("alice")
    body = {"admin_user": "boss", "admin_password": "a-long-password-1", "admin_email": "boss@alice.example"}
    assert client.post(f"/api/maintenance/wordpress/{site.id}/install", json=body).status_code == 200
    assert client.post(f"/api/maintenance/wordpress/{site.id}/install", json={**body, "subfolder": "blog"}).status_code == 200
    assert made == ["wp_alice_example", "wp_alice_example_blog"] and installs == ["", "blog"]
    names = sorted(row.db_name for row in env.db.query(DatabaseAccount).filter_by(website_id=site.id))
    assert names == ["wp_alice_example", "wp_alice_example_blog"]
    env.db.refresh(site)
    assert site.app_type == "wordpress"
    assert client.post(f"/api/maintenance/wordpress/{site.id}/install", json={**body, "subfolder": "wp-admin"}).status_code == 400


def test_a_purge_empties_the_cache_here_even_when_the_plugin_cannot_reach_the_site(env, monkeypatch):
    """`wp litespeed-purge all` asks the site's own address, which a site just
    moved here (or a .test one) does not resolve to: the job used to fail."""
    from app.services import openlitespeed

    install = _scan(env)
    purged = []
    monkeypatch.setattr(openlitespeed, "purge_site_cache", lambda domain: purged.append(domain) or 7)

    def unreachable(website, path, args, **kw):
        if args[:1] == ["litespeed-purge"]:
            raise wp_manager.WpError("cURL error 6: Could not resolve host")
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(wp_manager, "wp", unreachable)
    monkeypatch.setattr(wp_manager, "refresh_install", lambda *a, **k: None)
    answer = env.as_user("alice").post(f"/api/wordpress/installs/{install.id}/lscache", json={"action": "purge"})
    assert answer.status_code == 200 and answer.json()["job"]["status"] == "completed", answer.text
    assert purged == ["alice.example"]
    assert "7 cached page(s)" in answer.json()["job"]["log"]
