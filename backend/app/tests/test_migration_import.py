"""The DirectAdmin Import and cPanel Import addons (2026-10-18).

A cPanel archive is laid out the way a DirectAdmin one is and imported by the
same importer; a cPanel or DirectAdmin server can be signed in to as root to
pack the accounts picked. DirectAdmin import was part of the panel before: a
server that used it has the addon turned on, once.
"""
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import maintenance
from app.core.config import settings
from app.models.entities import Base, DatabaseAccount, User, Website, WebsiteAlias
from app.schemas.schemas import RestoreRunIn
from app.services import addons, cpanel_import, da_import, mariadb, migrate_ssh, openlitespeed, restore_sources, site_users, waf

ADMIN = SimpleNamespace(id=1, role="admin")

KEY = "-----BEGIN PRIVATE KEY-----\nMIIkey\n-----END PRIVATE KEY-----"
CERT = "-----BEGIN CERTIFICATE-----\nMIIleaf\n-----END CERTIFICATE-----"
CHAIN = "-----BEGIN CERTIFICATE-----\nMIIca\n-----END CERTIFICATE-----"


def _addons(monkeypatch, **on):
    monkeypatch.setattr(restore_sources.addons, "is_enabled", lambda addon_id: bool(on.get(addon_id)))


# --- names ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("name, kind, account", [
    ("cpmove-bob.tar.gz", "cpanel", "bob"),
    ("backup-10.18.2026_01-02-03_carol.tar.gz", "cpanel", "carol"),
    ("user.admin.alice.tar.zst", "directadmin", "alice"),
    ("alice-monday.tar.gz", "opanel", "alice"),
    ("backup-carol.tar.bz2", "directadmin", "carol"),
])
def test_each_kind_is_known_by_its_name(name, kind, account):
    assert restore_sources.archive_kind(name) == kind
    assert restore_sources.account_of(kind, name) == account


# --- off means off -------------------------------------------------------------------------------

def test_with_the_addons_off_their_archives_are_listed_but_not_restored(monkeypatch):
    _addons(monkeypatch)
    monkeypatch.setattr(restore_sources, "list_local", lambda: [
        {"ref": "/x/cpmove-bob.tar.gz", "kind": "cpanel", "filename": "cpmove-bob.tar.gz"},
        {"ref": "/x/user.admin.al.tar.zst", "kind": "directadmin", "filename": "user.admin.al.tar.zst"},
        {"ref": "/x/al-monday.tar.gz", "kind": "opanel", "filename": "al-monday.tar.gz"},
    ])
    rows = {row["kind"]: row for row in restore_sources.list_source(restore_sources.Source("local"))}
    assert rows["cpanel"]["valid"] is False and "cPanel Import addon" in rows["cpanel"]["error"]
    assert rows["directadmin"]["valid"] is False and "DirectAdmin Import addon" in rows["directadmin"]["error"]
    assert "valid" not in rows["opanel"]
    with pytest.raises(ValueError, match="cPanel Import addon"):
        restore_sources.check_ref(restore_sources.Source("local"), "cpanel", "cpmove-bob.tar.gz")


def test_with_the_addons_off_the_old_endpoints_and_the_ssh_source_answer_403(monkeypatch):
    _addons(monkeypatch)
    with pytest.raises(HTTPException) as exc:
        maintenance.list_da_backups(current_user=ADMIN)
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException) as exc:
        maintenance._restore_source(RestoreRunIn(
            source="ssh", remote={"protocol": "ssh", "host": "cp.example.com", "password": "pw", "panel": "cpanel"},
            items=[{"kind": "cpanel", "ref": "bob"}]), db=None)
    assert exc.value.status_code == 403


def test_an_ssh_restore_is_queued_with_the_password_in_memory_only(monkeypatch):
    _addons(monkeypatch, cpanel=True)
    submitted, recorded = [], []
    monkeypatch.setattr(maintenance._backup_job_executor, "submit", lambda fn, *args: submitted.append(args))
    monkeypatch.setattr(maintenance, "log_action", lambda *a, **k: None)
    monkeypatch.setattr(maintenance, "_backup_jobs", {})
    monkeypatch.setattr(maintenance, "_da_import_jobs", {})
    monkeypatch.setattr(maintenance.restore_jobs, "create", lambda db, **kw: recorded.append(kw) or SimpleNamespace(id=1))
    payload = RestoreRunIn(source="ssh", remote={"protocol": "ssh", "host": "cp.example.com", "password": "r00t-pw",
                                                 "panel": "cpanel"},
                           items=[{"kind": "cpanel", "ref": "bob"}])
    maintenance.run_restore(payload, request=None, db=None, current_user=ADMIN)
    source, items = submitted[0][2], submitted[0][3]
    assert source.kind == "ssh" and source.remote.username == "root" and source.remote.panel == "cpanel"
    assert items == [{"kind": "cpanel", "ref": "bob"}]
    assert "r00t-pw" not in repr(recorded) and recorded[0]["label"] == "cPanel server root@cp.example.com:22"
    # A pick is an account name, never a path or a command.
    for bad in ("../etc", "bob;reboot", "Bob"):
        with pytest.raises(ValueError):
            restore_sources.check_ref(source, "cpanel", bad)
    with pytest.raises(ValueError):
        restore_sources.check_ref(source, "directadmin", "bob")


# --- the userdata files --------------------------------------------------------------------------

def test_cpanels_userdata_is_read():
    data = cpanel_import.parse_userdata(
        "---\naddon_domains:\n  shop.test: shop.bob.test\ncp_php_magic_include_path.conf: 0\n"
        "main_domain: bob.test\nparked_domains:\n  - bob.net\nsub_domains:\n  - shop.bob.test\n  - blog.bob.test\n"
        "documentroot: '/home/bob/public_html'\n")
    assert data["main_domain"] == "bob.test" and data["addon_domains"] == {"shop.test": "shop.bob.test"}
    assert data["parked_domains"] == ["bob.net"] and data["sub_domains"] == ["shop.bob.test", "blog.bob.test"]
    assert data["documentroot"] == "/home/bob/public_html"


# --- a whole cPanel account, through the DirectAdmin importer -----------------------------------

def _cpmove(tmp_path: Path) -> Path:
    files = {
        "cp/bob": "USER=bob\nCONTACTEMAIL=bob@mail.test\nDNS=bob.test\n",
        "userdata/main": ("---\naddon_domains:\n  shop.test: shop.bob.test\nmain_domain: bob.test\n"
                          "parked_domains:\n  - bob.net\nsub_domains:\n  - shop.bob.test\n  - blog.bob.test\n"),
        "userdata/bob.test": "documentroot: /home/bob/public_html\nhomedir: /home/bob\nphpversion: ea-php74\n",
        "userdata/shop.bob.test": "documentroot: /home/bob/public_html/shop.test\nhomedir: /home/bob\n",
        "userdata/blog.bob.test": "documentroot: /home/bob/public_html/blog\nhomedir: /home/bob\n",
        "homedir/public_html/index.php": "<?php echo 'main';",
        "homedir/public_html/wp-config.php": ("<?php define('DB_NAME', 'bob_wp'); define('DB_USER', 'bob_wp');"
                                              " define('DB_PASSWORD', 'site-db-pw'); define('DB_HOST', 'localhost');"),
        "homedir/public_html/shop.test/index.html": "shop",
        "homedir/public_html/blog/index.php": "<?php echo 'blog';",
        "homedir/mail/bob.test/info/cur/1": "mail",
        "mysql/bob_wp.sql": "CREATE TABLE t (id int);",
        "mysql/bob_wp.create": "CREATE DATABASE bob_wp;",
        "mysql/roundcube.sql": "-- cPanel's own",
        "apache_tls/bob.test": "\n".join([KEY, CERT, CHAIN]),
        "cron/bob": "MAILTO=\"\"\n*/5 * * * * php /home/bob/public_html/cron.php\n",
    }
    archive = tmp_path / "cpmove-bob.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(f"cpmove-bob/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return archive


def test_a_cpanel_account_comes_over_whole(tmp_path, monkeypatch):
    folder = tmp_path / "cpanel"
    folder.mkdir()
    archive = _cpmove(folder)
    monkeypatch.setattr(settings, "cpanel_backup_dir", str(folder))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    copied, created, imported, certs, vhosts = {}, [], [], [], []
    monkeypatch.setattr(site_users, "ensure_panel_user", lambda username, password: username)
    monkeypatch.setattr(site_users, "site_root_for_panel_user", lambda user, domain: f"/home/{user}/{domain}")
    monkeypatch.setattr(site_users, "ensure_site_runtime", lambda *a, **k: None)
    monkeypatch.setattr(site_users, "ensure_document_root", lambda root, doc, user: Path(root) / doc)
    monkeypatch.setattr(site_users, "import_site_files", lambda root, doc, user, source: copied.__setitem__(
        Path(root).name, sorted(p.name for p in Path(source).iterdir())))
    monkeypatch.setattr(site_users, "fix_site_permissions", lambda *a, **k: None)
    monkeypatch.setattr(site_users, "site_lsphp_socket", lambda *a, **k: "/tmp/sock")
    monkeypatch.setattr(mariadb, "assert_db_user_available", lambda *a, **k: None)
    monkeypatch.setattr(mariadb, "create_database_credentials", lambda name, user, pw, **k: created.append((name, user, pw)))
    monkeypatch.setattr(mariadb, "import_database", lambda name, path: imported.append(name))
    monkeypatch.setattr(da_import, "_update_app_db_config", lambda *a, **k: [])
    monkeypatch.setattr(da_import, "_temporary_sql_file", lambda sql: Path(sql))
    monkeypatch.setattr(openlitespeed, "rewrite_vhost", lambda domain, *a, **k: vhosts.append((domain, k.get("aliases"))))
    monkeypatch.setattr(openlitespeed, "reload_service", lambda: None)
    monkeypatch.setattr(waf, "sync_website_rules", lambda *a, **k: SimpleNamespace(returncode=0))
    from app.services import ssl as ssl_service
    monkeypatch.setattr(ssl_service, "install_site_certificate", lambda domain, cert, key, ca: certs.append(
        (domain, cert.decode().strip(), key.decode().strip(), ca.decode().strip())) or {"cert": "c", "key": "k", "ca": "a"})
    monkeypatch.setattr(ssl_service, "certificate_expiry", lambda cert: None)
    import app.api.websites as websites_api
    monkeypatch.setattr(websites_api, "_rewrite_website_vhost", lambda website, **k: vhosts.append((website.domain, "aliases")))
    import socket
    monkeypatch.setattr(socket, "gethostbyname", lambda name: (_ for _ in ()).throw(socket.gaierror()))

    summary = cpanel_import.import_cpanel_backup(archive.name, db)

    # The main, addon and subdomain become websites; the addon's backing subdomain does not.
    assert sorted(summary["imported_domains"]) == ["blog.bob.test", "bob.test", "shop.test"]
    assert sorted(site.domain for site in db.query(Website)) == ["blog.bob.test", "bob.test", "shop.test"]
    assert db.query(User).filter_by(username="bob").one().email == "bob@mail.test"
    # Nested document roots were moved out of public_html before it was copied.
    assert copied["bob.test"] == ["index.php", "wp-config.php"]
    assert copied["shop.test"] == ["index.html"] and copied["blog.bob.test"] == ["index.php"]
    # The database, with the password the site already uses; cPanel's own is left.
    assert imported == ["bob_wp"] and created == [("bob_wp", "bob_wp", "site-db-pw")]
    assert db.query(DatabaseAccount).filter_by(db_name="bob_wp").one().website_id == \
        db.query(Website).filter_by(domain="bob.test").one().id
    # The certificate from apache_tls, split into key, certificate and chain.
    assert certs == [("bob.test", CERT, KEY, CHAIN)] and "bob.test" in summary["ssl_enabled_domains"]
    # The parked domain is an alias of the main website.
    alias = db.query(WebsiteAlias).one()
    assert alias.domain == "bob.net" and alias.website.domain == "bob.test" and summary["aliases"] == ["bob.net"]
    # What was not imported is said.
    warnings = " | ".join(summary["warnings"])
    assert "1 mailbox(es)" in warnings and "1 cron job(s)" in warnings and "PHP 7.4" in warnings
    assert (folder / "cpmove-bob-credentials.txt").is_file()


def test_a_cpanel_upload_lands_in_its_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cpanel_backup_dir", str(tmp_path))
    item = cpanel_import.save_backup("cpmove-bob.tar.gz", io.BytesIO(b"x"))
    assert Path(item["path"]).parent == tmp_path and item["filename"] == "cpmove-bob.tar.gz"
    with pytest.raises(ValueError):
        cpanel_import.save_backup("bob.tar.gz", io.BytesIO(b"x"))
    assert [row["filename"] for row in cpanel_import.list_backups()] == ["cpmove-bob.tar.gz"]
    with pytest.raises(FileNotFoundError):
        cpanel_import.resolve_path("../cpmove-bob.tar.gz")


# --- signing in to the other server ---------------------------------------------------------------

def test_cpanel_and_directadmin_account_lists_are_read():
    rows = migrate_ssh.parse_cpanel_accounts(json.dumps({"data": {"acct": [
        {"user": "bob", "domain": "Bob.test", "diskused": "1.5G", "suspended": 0},
        {"user": "bad user", "domain": "x.test"},
        {"user": "eve", "domain": "eve.test", "diskused": "none", "suspended": 1}]}}))
    assert [(r["account"], r["domain"], r["size"], r["note"]) for r in rows] == [
        ("bob", "bob.test", int(1.5 * 1024 ** 3), ""), ("eve", "eve.test", 0, "suspended")]
    assert migrate_ssh.parse_cpanel_accounts("TRUEUSERDOMAINS\nbob.test: bob\n")[0]["account"] == "bob"
    with pytest.raises(migrate_ssh.MigrateError):
        migrate_ssh.parse_cpanel_accounts("NOT_CPANEL")
    rows = migrate_ssh.parse_directadmin_accounts("al\tuser\tadmin\tal.test\tno\t120.5\nrs\treseller\tadmin\trs.test\tyes\t\n")
    assert [(r["account"], r["domain"], r["note"]) for r in rows] == [("al", "al.test", ""), ("rs", "rs.test", "reseller, suspended")]
    assert rows[0]["kind"] == "directadmin" and rows[0]["size"] == int(120.5 * 1024 ** 2)


class _FakeServer:
    """What the other server answers, and what it was asked."""

    def __init__(self, panel, fail_pack=False):
        self.panel, self.fail_pack, self.commands, self.removed = panel, fail_pack, [], []
        self.folder = "/home/opanel-migrate.Ab12Cd34Ef"

    def run(self, client, command, timeout):
        self.commands.append(command)
        if command.startswith("umask 077; mktemp"):
            return 0, self.folder + "\n", ""
        if command.startswith("rm -rf -- "):
            self.removed.append(command)
            return 0, "", ""
        if "pkgacct" in command or "taskq" in command:
            return (1, "", "pkgacct: no such user") if self.fail_pack else (0, "", "")
        if command.startswith("grep -l '^usertype=admin'"):
            return 0, "/usr/local/directadmin/data/users/admin/user.conf\n", ""
        if command.startswith("chown"):
            return 0, "", ""
        if command.startswith("find "):
            name = "cpmove-bob.tar.gz" if self.panel == "cpanel" else "user.admin.bob.tar.zst"
            return 0, f"{name}\t1234\n", ""
        raise AssertionError(command)

    def open_sftp(self):
        server = self

        class Sftp:
            def stat(self, path):
                return SimpleNamespace(st_size=1234)

            def get(self, remote, local, callback=None):
                server.downloaded = remote
                Path(local).write_bytes(b"archive")
                if callback:
                    callback(1234, 1234)

            def close(self):
                pass

        return Sftp()


@pytest.mark.parametrize("panel, name", [("cpanel", "cpmove-bob.tar.gz"), ("directadmin", "user.admin.bob.tar.zst")])
def test_an_account_is_packed_there_downloaded_and_the_folder_removed(tmp_path, monkeypatch, panel, name):
    server = _FakeServer(panel)
    monkeypatch.setattr(migrate_ssh, "_exec", server.run)
    monkeypatch.setattr(migrate_ssh.time, "sleep", lambda s: None)
    got = migrate_ssh.pack_and_fetch(server, panel, "bob", lambda n: tmp_path / n, lambda *a: None)
    assert Path(got) == tmp_path / name and Path(got).read_bytes() == b"archive"
    assert server.downloaded == f"{server.folder}/{name}"
    assert server.removed == [f"rm -rf -- {server.folder}"]
    pack = next(c for c in server.commands if "pkgacct" in c or "taskq" in c)
    if panel == "cpanel":
        assert pack.startswith(f"/usr/local/cpanel/scripts/pkgacct bob {server.folder} ")
    else:
        assert "select%30=bob" in pack and "owner=admin" in pack and "local%5Fpath=%2Fhome%2Fopanel-migrate.Ab12Cd34Ef" in pack


def test_a_failed_pack_still_removes_the_folder_there(tmp_path, monkeypatch):
    server = _FakeServer("cpanel", fail_pack=True)
    monkeypatch.setattr(migrate_ssh, "_exec", server.run)
    with pytest.raises(migrate_ssh.MigrateError, match="no such user"):
        migrate_ssh.pack_and_fetch(server, "cpanel", "bob", lambda n: tmp_path / n, lambda *a: None)
    assert server.removed and not list(tmp_path.iterdir())
    with pytest.raises(ValueError):
        migrate_ssh.pack_and_fetch(server, "cpanel", "bob; reboot", lambda n: tmp_path / n, lambda *a: None)


# --- DirectAdmin import was in the panel before -------------------------------------------------

def test_a_server_that_imported_from_directadmin_keeps_it_on_once(tmp_path, monkeypatch):
    monkeypatch.setattr(addons, "STATE_FILE", tmp_path / "addons.json")
    da_dir = tmp_path / "da"
    da_dir.mkdir()
    monkeypatch.setattr(settings, "da_backup_dir", str(da_dir))
    monkeypatch.setattr(addons, "_directadmin_import_was_used", lambda: any(da_dir.iterdir()))
    (da_dir / "user.admin.al-credentials.txt").write_text("x")
    assert addons.is_enabled("directadmin") is True
    assert json.loads((tmp_path / "addons.json").read_text())["directadmin"]["installed"] is True
    # The admin turns it off: that stands.
    addons.uninstall("directadmin")
    assert addons.is_enabled("directadmin") is False


def test_a_fresh_server_starts_with_directadmin_import_off(tmp_path, monkeypatch):
    monkeypatch.setattr(addons, "STATE_FILE", tmp_path / "addons.json")
    monkeypatch.setattr(addons, "_directadmin_import_was_used", lambda: False)
    assert addons.is_enabled("directadmin") is False
    assert addons.is_enabled("cpanel") is False
    assert json.loads((tmp_path / "addons.json").read_text())["directadmin"] == {"installed": False, "running": False}


def test_the_cpanel_staging_folder_is_one_the_helper_copies_from():
    """site-import-copy refuses a source outside /tmp/opanel-da-import-*: the
    first cPanel import on .41 stopped there."""
    helper = (Path(__file__).resolve().parents[3] / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
    assert "/tmp/opanel-da-import-*) ;;" in helper
    assert ("/tmp/" + cpanel_import.STAGE_PREFIX).startswith("/tmp/opanel-da-import-")
