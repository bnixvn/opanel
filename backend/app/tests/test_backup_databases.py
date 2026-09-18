"""A full user backup covers every database the account owns.

Found on production: an account's backup contained no database at all. The
backup looked databases up by Website.id, but POST /databases stores a row with
an owner and no website_id, so every database made on the Databases page was
invisible to it -- including the live one the site's .env pointed at.

Ownership is the criterion. A site declares the database it uses in its own
source, in whatever shape it likes; the panel cannot infer that, and must not
try.
"""
import json
import os
import sys
import tarfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.services import backup


class _User:
    id = 7
    username = "babatapapp"
    email = "owner@example.test"
    hashed_password = ""
    role = "user"
    is_active = True
    website_limit = 5
    storage_limit_mb = 0


class _Site:
    def __init__(self, site_id, domain, root_path):
        self.id = site_id
        self.domain = domain
        self.root_path = str(root_path)
        self.php_version = "8.3"
        self.app_type = "php"
        self.status = "active"
        self.document_root = "public_html"
        self.nginx_custom = ""
        self.nginx_rewrite_mode = "none"
        self.waf_enabled = False
        self.waf_default_rules = ""
        self.waf_custom_rules = ""
        self.aliases = []


class _DbAccount:
    def __init__(self, account_id, db_name, owner_id, website_id=None):
        self.id = account_id
        self.db_name = db_name
        self.db_user = db_name[:16]
        self.db_password = "enc"
        self.owner_id = owner_id
        self.website_id = website_id


def _db_with(sites, accounts):
    class _Query:
        def __init__(self, rows):
            self.rows = rows

        def filter(self, *a, **k):
            return self

        def order_by(self, *a, **k):
            return self

        def all(self):
            return self.rows

        def first(self):
            return self.rows[0] if self.rows else None

    class _DB:
        def query(self, model, *a, **k):
            name = getattr(model, "__name__", "")
            if name == "Website":
                return _Query(sites)
            if name == "DatabaseAccount":
                return _Query(accounts)
            return _Query([])

    return _DB()


@pytest.fixture
def site_tree(tmp_path):
    root = tmp_path / "api.babatap.com"
    (root / "public_html").mkdir(parents=True)
    (root / "public_html" / "index.php").write_text("<?php", encoding="utf-8")
    return root


@pytest.fixture
def dumping(monkeypatch):
    """Record which databases were dumped and write a stand-in file."""
    dumped = []

    def export(db_name, path):
        dumped.append(db_name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"-- dump of {db_name}\n")

    monkeypatch.setattr(backup.mariadb, "export_database", export)
    monkeypatch.setattr(backup, "decrypt", lambda value: "secret")
    return dumped


def _run(tmp_path, monkeypatch, sites, accounts, skipped=None):
    monkeypatch.setattr(backup, "_user_backup_dir", lambda name: tmp_path / "out")
    (tmp_path / "out").mkdir(exist_ok=True)
    archive = backup.create_user_backup(
        _User(), _db_with(sites, accounts), skipped=skipped if skipped is not None else []
    )
    with tarfile.open(archive) as tar:
        names = tar.getnames()
        manifest = json.loads(tar.extractfile(backup.BACKUP_MANIFEST).read().decode("utf-8"))
    return names, manifest


def test_a_database_with_no_website_is_still_backed_up(tmp_path, monkeypatch, site_tree, dumping):
    """The production case: created on the Databases page, so website_id is
    NULL, and the account's backup silently held no data."""
    sites = [_Site(1, "api.babatap.com", site_tree)]
    accounts = [_DbAccount(1, "act_babatap_api", owner_id=7, website_id=None)]

    names, manifest = _run(tmp_path, monkeypatch, sites, accounts)

    assert dumping == ["act_babatap_api"]
    assert "databases/act_babatap_api.sql" in names
    assert [entry["db_name"] for entry in manifest["databases"]] == ["act_babatap_api"]
    assert manifest["databases"][0]["website_domain"] == ""


def test_every_database_of_the_account_is_taken_not_one_per_site(tmp_path, monkeypatch, site_tree, dumping):
    sites = [_Site(2, "taplookspa.com", site_tree)]
    accounts = [
        _DbAccount(1, "taplookspa_db", owner_id=7, website_id=2),
        _DbAccount(2, "taplookspa_franchise", owner_id=7, website_id=None),
        _DbAccount(3, "taplookspa_partner", owner_id=7, website_id=None),
    ]

    names, manifest = _run(tmp_path, monkeypatch, sites, accounts)

    assert sorted(dumping) == ["taplookspa_db", "taplookspa_franchise", "taplookspa_partner"]
    for db_name in dumping:
        assert f"databases/{db_name}.sql" in names
    assert len(manifest["databases"]) == 3


def test_another_accounts_database_is_never_taken(tmp_path, monkeypatch, site_tree, dumping):
    sites = [_Site(1, "api.babatap.com", site_tree)]
    accounts = [
        _DbAccount(1, "mine", owner_id=7, website_id=None),
        _DbAccount(2, "someone_else", owner_id=99, website_id=None),
    ]

    _, manifest = _run(tmp_path, monkeypatch, sites, accounts)

    assert [entry["db_name"] for entry in manifest["databases"]] == ["mine"]
    assert "someone_else" not in dumping


def test_a_database_attached_to_the_users_site_counts_even_if_the_owner_is_stale(
    tmp_path, monkeypatch, site_tree, dumping
):
    sites = [_Site(1, "api.babatap.com", site_tree)]
    accounts = [_DbAccount(1, "attached", owner_id=99, website_id=1)]

    _, manifest = _run(tmp_path, monkeypatch, sites, accounts)

    assert [entry["db_name"] for entry in manifest["databases"]] == ["attached"]
    assert manifest["databases"][0]["website_domain"] == "api.babatap.com"


def test_the_site_entry_still_carries_its_database(tmp_path, monkeypatch, site_tree, dumping):
    """An older panel restoring this archive reads the per-site field."""
    sites = [_Site(1, "api.babatap.com", site_tree)]
    accounts = [_DbAccount(1, "linked_db", owner_id=7, website_id=1)]

    _, manifest = _run(tmp_path, monkeypatch, sites, accounts)

    assert manifest["websites"][0]["database"]["db_name"] == "linked_db"
    assert manifest["websites"][0]["database"]["sql_member"] == "databases/linked_db.sql"


def test_a_dropped_database_is_recorded_not_fatal(tmp_path, monkeypatch, site_tree):
    """A row left behind after the database was dropped outside the panel must
    not cost the account every site it owns."""
    def explode(db_name, path):
        raise RuntimeError("Unknown database 'ghost'")

    monkeypatch.setattr(backup.mariadb, "export_database", explode)
    monkeypatch.setattr(backup, "decrypt", lambda value: "secret")
    sites = [_Site(1, "api.babatap.com", site_tree)]
    accounts = [_DbAccount(1, "ghost", owner_id=7, website_id=None)]

    skipped = []
    names, manifest = _run(tmp_path, monkeypatch, sites, accounts, skipped=skipped)

    assert manifest["databases"] == []
    assert skipped[0]["path"] == "database:ghost"
    assert "sites/api.babatap.com/site/public_html/index.php" in names


def test_a_database_name_that_is_a_path_is_refused(tmp_path, monkeypatch, site_tree, dumping):
    sites = [_Site(1, "api.babatap.com", site_tree)]
    accounts = [_DbAccount(1, "../../etc/passwd", owner_id=7, website_id=None)]

    skipped = []
    _, manifest = _run(tmp_path, monkeypatch, sites, accounts, skipped=skipped)

    assert manifest["databases"] == []
    assert skipped[0]["reason"] == "Invalid database name"
    assert dumping == []


def test_restore_reads_the_owned_list_and_ignores_the_per_site_copy():
    """Both are written, so a restore must not import the same dump twice."""
    import inspect

    source = inspect.getsource(backup.restore_user_backup)

    assert 'owned_databases = manifest.get("databases") or []' in source
    assert 'db_info = None if owned_databases else (site_info.get("database") or None)' in source


def test_restore_attaches_a_database_to_its_site_and_leaves_the_rest_standalone():
    import inspect

    source = inspect.getsource(backup.restore_user_backup)

    assert 'website_by_domain.get((entry.get("website_domain") or "").strip().lower())' in source
    assert "website_id=website.id if website else None" in source
