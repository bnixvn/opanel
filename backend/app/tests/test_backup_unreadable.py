"""One unreadable file must not cost the operator the whole backup.

Reported from production: a scheduled run died with

    babatapapp: [Errno 13] Permission denied:
    '/home/babatapapp/api.babatap.com/public_html/bootstrap/cache/packages.php'

Site PHP runs as the site's own user, so it can write a file the panel cannot
read at any moment -- Laravel writes bootstrap/cache/*.php with nothing set for
"other". tarfile.add() walks the tree itself and aborts on the first such file,
so a 52-site account lost every site's backup to one regenerable cache file.
"""
import json
import os
import sys
import tarfile

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.services import backup


class _User:
    id = 1
    username = "babatapapp"
    email = "owner@example.test"
    hashed_password = ""
    role = "user"
    is_active = True
    website_limit = 5
    storage_limit_mb = 0


class _Site:
    def __init__(self, domain, root_path):
        self.id = 1
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


def _db_with(sites):
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
            return None

    class _DB:
        def query(self, model, *a, **k):
            name = getattr(model, "__name__", "")
            return _Query(sites if name == "Website" else [])

    return _DB()


def _unreadable_tree(tmp_path):
    """A site tree shaped like the one that failed."""
    root = tmp_path / "api.babatap.com"
    cache = root / "public_html" / "bootstrap" / "cache"
    cache.mkdir(parents=True)
    (root / "public_html" / "index.php").write_text("<?php echo 1;", encoding="utf-8")
    (root / "public_html" / "composer.json").write_text("{}", encoding="utf-8")
    blocked = cache / "packages.php"
    blocked.write_text("<?php return [];", encoding="utf-8")
    return root, blocked


def _deny_reading(monkeypatch, blocked):
    """Refuse to open one path, the way the kernel refuses mode 0750 to another
    uid. Patching the call is what makes this behave the same on a machine with
    no POSIX permissions and when the suite runs as root, where chmod proves
    nothing."""
    real = backup.tarfile.TarFile.gettarinfo

    def gated(self, name=None, arcname=None, fileobj=None):
        if name and os.path.abspath(str(name)) == os.path.abspath(str(blocked)):
            raise PermissionError(13, "Permission denied", str(blocked))
        return real(self, name, arcname, fileobj)

    monkeypatch.setattr(backup.tarfile.TarFile, "gettarinfo", gated)


def test_a_site_backup_survives_an_unreadable_file(tmp_path, monkeypatch):
    root, blocked = _unreadable_tree(tmp_path)
    _deny_reading(monkeypatch, blocked)
    monkeypatch.setattr(backup.settings, "backup_root", str(tmp_path / "backups"))
    monkeypatch.setattr(backup.shell, "run", lambda *a, **k: None)

    skipped = []
    archive = backup.create_backup(_Site("api.babatap.com", root), None, skipped=skipped)

    assert [item["path"] for item in skipped] == [str(blocked)]
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert "site/public_html/index.php" in names
    assert "site/public_html/composer.json" in names
    assert "site/public_html/bootstrap/cache/packages.php" not in names


def test_a_user_backup_survives_and_records_the_gap(tmp_path, monkeypatch):
    root, blocked = _unreadable_tree(tmp_path)
    _deny_reading(monkeypatch, blocked)
    monkeypatch.setattr(backup, "_user_backup_dir", lambda name: tmp_path / "out")
    (tmp_path / "out").mkdir()

    skipped = []
    archive = backup.create_user_backup(
        _User(), _db_with([_Site("api.babatap.com", root)]), skipped=skipped
    )

    assert [item["path"] for item in skipped] == [str(blocked)]
    with tarfile.open(archive) as tar:
        names = tar.getnames()
        recorded = json.loads(tar.extractfile(backup.BACKUP_SKIPPED).read().decode("utf-8"))

    assert "sites/api.babatap.com/site/public_html/index.php" in names
    # The gap travels with the archive, so a restore can see what is missing.
    assert [item["path"] for item in recorded] == [str(blocked)]
    # ...in its own member. The manifest stays first so listing an archive does
    # not have to decompress it to name its owner.
    assert names[0] == backup.BACKUP_MANIFEST
    assert names[-1] == backup.BACKUP_SKIPPED


def test_a_clean_tree_records_nothing(tmp_path, monkeypatch):
    root, _ = _unreadable_tree(tmp_path)
    monkeypatch.setattr(backup, "_user_backup_dir", lambda name: tmp_path / "out")
    (tmp_path / "out").mkdir()

    skipped = []
    archive = backup.create_user_backup(
        _User(), _db_with([_Site("api.babatap.com", root)]), skipped=skipped
    )

    assert skipped == []
    with tarfile.open(archive) as tar:
        assert "sites/api.babatap.com/site/public_html/bootstrap/cache/packages.php" in tar.getnames()


def test_the_message_names_the_files_and_the_remedy():
    note = backup.describe_skipped([{"path": "/home/a/site/x.php", "reason": "denied"}])

    assert "/home/a/site/x.php" in note
    assert "Fix permissions" in note
    assert backup.describe_skipped([]) == ""


def test_the_message_stays_short_when_many_files_are_skipped():
    note = backup.describe_skipped([{"path": f"/home/a/{n}.php", "reason": "x"} for n in range(40)])

    assert "and 37 more" in note
    assert len(note) < 300


def test_a_symlink_is_stored_but_never_walked(tmp_path, monkeypatch):
    """A link pointing up its own tree would otherwise loop forever."""
    root = tmp_path / "site"
    (root / "public_html").mkdir(parents=True)
    (root / "public_html" / "index.php").write_text("x", encoding="utf-8")
    try:
        (root / "public_html" / "loop").symlink_to(root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")

    monkeypatch.setattr(backup, "_user_backup_dir", lambda name: tmp_path / "out")
    (tmp_path / "out").mkdir()

    skipped = []
    archive = backup.create_user_backup(
        _User(), _db_with([_Site("site.test", root)]), skipped=skipped
    )

    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert "sites/site.test/site/public_html/loop" in names
    assert not any("loop/public_html" in name for name in names)


def test_an_unreadable_directory_is_recorded_not_fatal(tmp_path, monkeypatch):
    root = tmp_path / "site"
    (root / "public_html").mkdir(parents=True)
    (root / "public_html" / "index.php").write_text("x", encoding="utf-8")
    locked = root / "public_html" / "private"
    locked.mkdir()

    real_iterdir = backup.Path.iterdir

    def gated(self):
        if os.path.abspath(str(self)) == os.path.abspath(str(locked)):
            raise PermissionError(13, "Permission denied", str(locked))
        return real_iterdir(self)

    monkeypatch.setattr(backup.Path, "iterdir", gated)
    monkeypatch.setattr(backup, "_user_backup_dir", lambda name: tmp_path / "out")
    (tmp_path / "out").mkdir()

    skipped = []
    backup.create_user_backup(_User(), _db_with([_Site("site.test", root)]), skipped=skipped)

    assert [item["path"] for item in skipped] == [str(locked)]


def test_the_manifest_is_the_first_member(tmp_path, monkeypatch):
    """An archive is read sequentially. The restore list describes every backup
    it offers, so a manifest at the tail cost a full decompress per archive --
    13 seconds each on a 2.4 GB one."""
    root, _ = _unreadable_tree(tmp_path)
    monkeypatch.setattr(backup, "_user_backup_dir", lambda name: tmp_path / "out")
    (tmp_path / "out").mkdir()

    archive = backup.create_user_backup(_User(), _db_with([_Site("api.babatap.com", root)]))

    with tarfile.open(archive) as tar:
        assert tar.getnames()[0] == backup.BACKUP_MANIFEST


def test_reading_the_gap_list_back_is_offered_separately(tmp_path, monkeypatch):
    root, blocked = _unreadable_tree(tmp_path)
    _deny_reading(monkeypatch, blocked)
    monkeypatch.setattr(backup, "_user_backup_dir", lambda name: tmp_path / "out")
    monkeypatch.setattr(backup.settings, "backup_root", str(tmp_path))
    (tmp_path / "out").mkdir()

    archive = backup.create_user_backup(_User(), _db_with([_Site("api.babatap.com", root)]))

    assert [item["path"] for item in backup.read_backup_skipped(archive)] == [str(blocked)]


def test_an_archive_without_a_gap_list_reads_as_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(backup.settings, "backup_root", str(tmp_path))
    archive = tmp_path / "old.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        backup._add_bytes(tar, backup.BACKUP_MANIFEST, b"{}")

    assert backup.read_backup_skipped(str(archive)) == []
