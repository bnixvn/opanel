"""WordPress Manager (an addon, 2026-10-17: the user's first addon from SNPanel).

What must hold: WP-CLI only ever runs as the website's own Linux user, never
with a name or a slug the caller made up; an account reaches only the
WordPress in its own websites (a reseller its customers', an administrator
all); the sign-in link works once, is made for one of the site's
administrators and never reaches a log; the addon off means the routes are
not there; a website that goes takes its WordPress records with it.
"""
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import users as users_api
from app.api import websites as websites_api
from app.api.deps import get_current_user
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.entities import User, Website, WpInstall, WpJob
from app.services import provisioning, wp_manager

HELPER = (Path(__file__).resolve().parents[3] / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")


class InlineExecutor:
    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)


PLUGINS = [
    {"name": "akismet", "title": "Akismet", "status": "inactive", "version": "5.0", "update": "available", "update_version": "5.3", "auto_update": "off"},
    {"name": "hello", "title": "Hello Dolly", "status": "active", "version": "1.7.2", "update": "none", "update_version": "", "auto_update": "on"},
    {"name": "object-cache", "title": "", "status": "dropin", "version": "", "update": "none", "update_version": "", "auto_update": "off"},
]
THEMES = [{"name": "twentytwentyfour", "title": "Twenty Twenty-Four", "status": "active", "version": "1.0", "update": "available", "update_version": "1.2", "auto_update": "off"}]
ADMINS = [{"ID": 1, "user_login": "boss", "user_email": "boss@example.test", "display_name": "Boss"}]


def fake_wp_output(args):
    """What WP-CLI prints for each read the inventory makes."""
    head = args[:2]
    if head == ["core", "version"]:
        return 0, "6.4.3\n"
    if head == ["core", "check-update"]:
        return 0, json.dumps([{"version": "6.6.1", "update_type": "major"}, {"version": "6.4.5", "update_type": "minor"}])
    if head == ["plugin", "list"]:
        return 0, "PHP Notice: something\n" + json.dumps(PLUGINS)
    if head == ["theme", "list"]:
        return 0, json.dumps(THEMES)
    if head == ["user", "list"]:
        return 0, json.dumps(ADMINS)
    if head == ["option", "get"]:
        return 0, ("My Blog\n" if args[2] == "blogname" else "https://alice.example/\n")
    if head == ["maintenance-mode", "is-active"]:
        return 1, ""
    if head == ["config", "get"]:
        return (0, "1\n") if args[2] == "WP_DEBUG" else (1, "")
    return 0, "Success.\n"


@pytest.fixture
def env(monkeypatch, tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    people = {}
    for name, role in (("root_admin", "admin"), ("alice", "end_user"), ("bob", "end_user")):
        people[name] = User(username=name, email=f"{name}@example.test", role=role, is_active=True,
                            hashed_password=hash_password("PasswordLongEnough1"))
        db.add(people[name])
    db.commit()
    sites = {}
    for owner in ("alice", "bob"):
        root = tmp_path / owner / f"{owner}.example"
        (root / "public_html" / "wp-includes").mkdir(parents=True)
        (root / "public_html" / "wp-includes" / "version.php").write_text("<?php")
        (root / "public_html" / "wp-config.php").write_text("<?php")
        sites[owner] = Website(domain=f"{owner}.example", owner_id=people[owner].id, root_path=str(root),
                               linux_user=owner, php_version="8.3", app_type="wordpress")
        db.add(sites[owner])
    db.commit()
    calls = []

    def fake_privileged(command, helper_args=None, check=True, input=None, sensitive=False, fallback=None):
        args = list(helper_args or [])
        calls.append({"command": command, "user": args[0] if args else None, "args": args[1:], "input": input, "sensitive": sensitive})
        code, out = fake_wp_output([a for a in args[1:] if not a.startswith("--path=") and a not in ("--skip-plugins", "--skip-themes")])
        return SimpleNamespace(returncode=code, stdout=out, stderr="")

    monkeypatch.setattr(wp_manager.shell, "privileged", fake_privileged)
    monkeypatch.setattr(wp_manager, "_executor", InlineExecutor())
    monkeypatch.setattr(wp_manager, "_session_factory", Session)
    addon = {"on": True}
    monkeypatch.setattr(wp_manager, "enabled", lambda: addon["on"])

    def get_test_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    state = {"user": people["alice"]}
    app.dependency_overrides[get_db] = get_test_db
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    client = TestClient(app)

    def as_user(name):
        state["user"] = db.get(User, people[name].id)
        return client

    try:
        yield SimpleNamespace(db=db, client=client, people=people, sites=sites, calls=calls, as_user=as_user, addon=addon, tmp=tmp_path)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


def _scan(env, owner="alice"):
    client = env.as_user(owner)
    answer = client.post("/api/wordpress/scan", json={})
    assert answer.status_code == 200, answer.text
    return answer.json()["job"]


def test_the_routes_are_not_there_while_the_addon_is_off(env):
    client = env.as_user("alice")
    assert client.get("/api/wordpress/info").json() == {"enabled": True}
    env.addon["on"] = False
    assert client.get("/api/wordpress").status_code == 404
    assert client.post("/api/wordpress/scan", json={}).status_code == 404
    # Every role may ask whether it is on.
    assert client.get("/api/wordpress/info").json() == {"enabled": False}


def test_a_scan_finds_the_website_and_a_blog_under_it(env):
    blog = Path(env.sites["alice"].root_path) / "public_html" / "blog"
    (blog / "wp-includes").mkdir(parents=True)
    (blog / "wp-includes" / "version.php").write_text("<?php")
    # Not a site of its own: WordPress's own folder.
    (Path(env.sites["alice"].root_path) / "public_html" / "wp-content").mkdir()
    _scan(env)
    rows = {row.rel_path: row for row in env.db.query(WpInstall).all()}
    assert set(rows) == {"", "blog"}
    assert rows[""].state == "installed" and rows["blog"].state == "setup"
    # Only the installed one was checked, with the site's own user.
    assert rows[""].core_version == "6.4.3" and rows["blog"].core_version == ""
    assert {call["user"] for call in env.calls} == {"alice"}
    assert all(call["command"] == "wp-site" for call in env.calls)


def test_the_inventory_is_read_through_notices_and_counts_updates(env):
    _scan(env)
    row = env.db.query(WpInstall).one()
    env.db.refresh(row)
    assert row.core_update == "6.6.1"
    assert (row.plugins_total, row.plugins_updates, row.themes_total, row.themes_updates) == (2, 1, 1, 1)
    inventory = json.loads(row.inventory)
    assert inventory["core_minor_update"] == "6.4.5"
    assert inventory["debug"] is True and inventory["maintenance"] is False and inventory["auto_update_core"] == "minor"
    assert inventory["admins"] == [{"id": 1, "login": "boss", "email": "boss@example.test", "name": "Boss"}]
    assert row.title == "My Blog" and row.url == "https://alice.example/"


def test_an_account_sees_and_reaches_only_its_own_wordpress(env):
    _scan(env, "alice")
    _scan(env, "bob")
    mine = env.as_user("alice").get("/api/wordpress").json()
    assert [item["domain"] for item in mine["items"]] == ["alice.example"]
    bobs = env.db.query(WpInstall).filter(WpInstall.owner_id == env.people["bob"].id).one()
    client = env.as_user("alice")
    assert client.get(f"/api/wordpress/installs/{bobs.id}").status_code == 403
    assert client.post(f"/api/wordpress/installs/{bobs.id}/login", json={}).status_code == 403
    every = env.as_user("root_admin").get("/api/wordpress").json()
    assert sorted(item["domain"] for item in every["items"]) == ["alice.example", "bob.example"]


def test_plugin_tasks_name_only_real_slugs_and_run_as_the_site_user(env):
    _scan(env)
    row = env.db.query(WpInstall).one()
    client = env.as_user("alice")
    bad = client.post(f"/api/wordpress/installs/{row.id}/plugins", json={"action": "install", "slugs": ["../../evil"]})
    assert bad.status_code == 400
    unknown = client.post(f"/api/wordpress/installs/{row.id}/plugins", json={"action": "eval", "slugs": ["hello"]})
    assert unknown.status_code == 400
    env.calls.clear()
    done = client.post(f"/api/wordpress/installs/{row.id}/plugins", json={"action": "delete", "slugs": ["akismet"]})
    # Queued, or (the test's executor runs it at once) done already.
    assert done.status_code == 200 and done.json()["job"]["status"] in ("queued", "completed")
    job = env.db.query(WpJob).order_by(WpJob.id.desc()).first()
    env.db.refresh(job)
    assert job.status == "completed", job.error
    commands = [call["args"][:3] for call in env.calls]
    # A plugin is deactivated before it is deleted.
    assert commands.index(["plugin", "deactivate", "akismet"]) < commands.index(["plugin", "delete", "akismet"])
    assert {call["user"] for call in env.calls} == {"alice"}


def test_the_sign_in_link_is_for_an_administrator_and_works_once(env):
    _scan(env)
    row = env.db.query(WpInstall).one()
    client = env.as_user("alice")
    assert client.post(f"/api/wordpress/installs/{row.id}/login", json={"user_id": 99}).status_code == 400
    env.calls.clear()
    answer = client.post(f"/api/wordpress/installs/{row.id}/login", json={})
    assert answer.status_code == 200, answer.text
    url = answer.json()["url"]
    assert url.startswith("https://alice.example/?opanel_sso=")
    token = url.split("opanel_sso=")[1]
    evals = [call["args"][1] for call in env.calls if call["args"][:1] == ["eval"]]
    # The must-use plugin first, then a 60-second transient keyed by the
    # token's hash - the token itself is never written anywhere.
    assert "WPMU_PLUGIN_DIR" in evals[0] and "opanel-sso.php" in evals[0]
    assert "set_transient('opanel_sso_" in evals[1] and ", 1, 60)" in evals[1]
    assert token not in " ".join(evals)
    assert "wp_set_auth_cookie" in wp_manager.SSO_SOURCE and "delete_transient($key)" in wp_manager.SSO_SOURCE


def test_a_new_password_goes_through_stdin(env):
    _scan(env)
    row = env.db.query(WpInstall).one()
    env.calls.clear()
    answer = env.as_user("alice").post(f"/api/wordpress/installs/{row.id}/password", json={"user_id": 1})
    assert answer.status_code == 200
    password = answer.json()["password"]
    call = next(call for call in env.calls if call["args"][:2] == ["user", "update"])
    assert "--prompt=user_pass" in call["args"] and call["input"] == password + "\n" and call["sensitive"]
    assert password not in " ".join(call["args"])


def test_a_website_that_goes_takes_its_wordpress_records_with_it():
    assert "wp_manager.forget_website(db, website.id)" in inspect.getsource(websites_api.delete_website)
    assert "wp_manager.forget_website(db, website.id)" in inspect.getsource(users_api._delete_owned_website)
    assert "wp_manager.forget_website(db, website.id)" in inspect.getsource(provisioning.terminate_account)


def test_a_task_a_restart_cut_short_says_so(env):
    job = WpJob(kind="refresh", status="running")
    env.db.add(job)
    env.db.commit()
    wp_manager.settle_stale(env.db)
    env.db.refresh(job)
    assert job.status == "interrupted"


def test_a_constant_is_added_above_wp_settings_when_the_marker_is_missing(env, monkeypatch):
    """OPanel's wp-config.php had no "That's all, stop editing!" line, and
    `wp config set` could not add WP_DEBUG_LOG to it."""
    seen = []

    def fake_wp(website, path, args, **kwargs):
        seen.append(args)
        if not any(arg.startswith("--anchor=") for arg in args):
            raise wp_manager.WpError("Error: Could not process the 'wp-config.php' transformation.")
        return SimpleNamespace(returncode=0, stdout="Success", stderr="")

    monkeypatch.setattr(wp_manager, "wp", fake_wp)
    wp_manager.config_set(env.sites["alice"], "/x", "WP_DEBUG_LOG", "true")
    assert seen[-1][-2:] == ["--anchor=require_once ABSPATH . 'wp-settings.php';", "--placement=before"]


def test_new_installs_carry_wordpress_s_own_marker():
    from app.services import wordpress

    text = wordpress._render_wp_config("db", "user", "pass")
    assert text.index("That's all, stop editing!") < text.index("require_once ABSPATH . 'wp-settings.php';")


def test_the_security_checks_say_what_is_wrong(env):
    _scan(env)
    row = env.db.query(WpInstall).one()
    detail = env.as_user("alice").get(f"/api/wordpress/installs/{row.id}").json()
    items = {item["id"]: item for item in detail["security"]["items"]}
    # The fake site has debugging on, file editing allowed, an inactive plugin
    # and updates waiting; nothing has been compared with wordpress.org yet.
    assert items["debug"]["status"] == "warning" and items["debug"]["apply"]
    assert items["file_edit"]["status"] == "warning"
    assert items["inactive_plugins"] == {"id": "inactive_plugins", "status": "warning", "count": 1}
    assert items["updates"]["status"] == "warning" and items["updates"]["count"] == 3
    assert items["core_checksums"]["status"] == "unknown" and items["salts"]["status"] == "action"
    listed = env.as_user("alice").get("/api/wordpress").json()["items"][0]
    assert listed["security_warnings"] == 4


def test_hardening_runs_only_known_items_as_the_site_user(env):
    _scan(env)
    row = env.db.query(WpInstall).one()
    client = env.as_user("alice")
    assert client.post(f"/api/wordpress/installs/{row.id}/security/apply", json={"item": "rm"}).status_code == 400
    env.calls.clear()
    answer = client.post(f"/api/wordpress/installs/{row.id}/security/apply", json={"item": "file_edit"})
    assert answer.status_code == 200
    sets = [call["args"][:4] for call in env.calls if call["args"][:2] == ["config", "set"]]
    assert ["config", "set", "DISALLOW_FILE_EDIT", "true"] in sets
    assert {call["user"] for call in env.calls} == {"alice"}


def _helper_case(name: str) -> str:
    start = HELPER.index(f"  {name})")
    return HELPER[start: HELPER.index(";;\n\n", start)]


def test_restore_points_are_made_and_put_back_as_the_site_user():
    body = _helper_case("wp-point-create|wp-point-restore")
    # Only a folder of the account's own, and only as the account.
    assert '^/home/${user}/\\.opanel-wp-points/[a-f0-9]{16}/[0-9]{8}T[0-9]{6}$' in body
    assert 'exec runuser -u "$user" -- bash -c' in body and 'exec runuser -u "$user" -- tar -xzf' in body
    assert "--exclude=./wp-content/uploads" in body
    delete = _helper_case("wp-point-delete")
    assert 'exec runuser -u "$user" -- rm -rf -- "$point"' in delete


def test_an_update_takes_a_restore_point_first_and_two_are_kept(env):
    _scan(env)
    row = env.db.query(WpInstall).one()
    client = env.as_user("alice")
    for _ in range(3):
        env.calls.clear()
        answer = client.post(f"/api/wordpress/installs/{row.id}/update", json={})
        assert answer.status_code == 200
    commands = [call["command"] for call in env.calls]
    # The point (files, then the database) comes before the first update.
    first_update = next(i for i, call in enumerate(env.calls) if call["args"][:2] == ["core", "update"])
    assert commands.index("wp-point-create") < first_update
    export = next(call for call in env.calls if call["args"][:2] == ["db", "export"])
    assert export["args"][2].startswith("/home/alice/.opanel-wp-points/") and export["args"][2].endswith("/db.sql")
    env.db.refresh(row)
    points = json.loads(row.inventory)["points"]
    assert len(points) == 2 and points[-1]["reason"] == "update everything"


def test_a_restore_names_a_point_the_site_has(env):
    _scan(env)
    row = env.db.query(WpInstall).one()
    client = env.as_user("alice")
    assert client.post(f"/api/wordpress/installs/{row.id}/points/restore", json={"name": "../../etc"}).status_code == 422
    client.post(f"/api/wordpress/installs/{row.id}/points", json={})
    env.db.refresh(row)
    name = json.loads(row.inventory)["points"][0]["name"]
    env.calls.clear()
    assert client.post(f"/api/wordpress/installs/{row.id}/points/restore", json={"name": name}).status_code == 200
    restored = [call for call in env.calls if call["command"] == "wp-point-restore"]
    assert restored and restored[0]["user"] == "alice"
    assert any(call["args"][:2] == ["db", "import"] for call in env.calls)
