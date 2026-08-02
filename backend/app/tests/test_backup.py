import tarfile
from pathlib import Path

from app.core.config import settings
from app.models.entities import Website
from app.services import backup


def _site_backup(backup_root: Path) -> Path:
    domain_dir = backup_root / "example.test"
    domain_dir.mkdir(parents=True)
    archive_path = domain_dir / "example.test-20260802000000.tar.gz"
    source = backup_root / "index.html"
    source.write_text("restored", encoding="utf-8")
    database = backup_root / "database.sql"
    database.write_text("SELECT 1;", encoding="utf-8")
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(source, arcname="site/public_html/index.html")
        archive.add(database, arcname="database/example.sql")
    return archive_path


def test_normalized_site_backup_strips_prefix_and_database(tmp_path):
    archive_path = _site_backup(tmp_path / "backups")

    normalized = backup._normalized_site_backup(archive_path)
    try:
        with tarfile.open(normalized, "r:gz") as archive:
            assert archive.getnames() == ["public_html/index.html"]
            assert archive.extractfile("public_html/index.html").read() == b"restored"
    finally:
        normalized.unlink(missing_ok=True)


def test_restore_backup_for_site_user_uses_privileged_extract(tmp_path, monkeypatch):
    backup_root = tmp_path / "backups"
    archive_path = _site_backup(backup_root)
    site_root = tmp_path / "site"
    site_root.mkdir()
    calls = []

    def fake_privileged(helper_command, helper_args=None, **kwargs):
        calls.append((helper_command, helper_args, kwargs))
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(settings, "backup_root", str(backup_root))
    monkeypatch.setattr(backup.shell, "privileged", fake_privileged)
    website = Website(
        domain="example.test",
        owner_id=1,
        root_path=str(site_root),
        linux_user="siteuser",
    )

    restored = backup.restore_backup(website, str(archive_path))

    assert restored == str(site_root.resolve())
    assert [call[0] for call in calls] == ["site-file-install", "site-archive-extract", "site-file-delete"]
    assert calls[1][1][0:5] == ["siteuser", str(site_root), calls[0][1][2], ".", "tar.gz"]
    assert not Path(calls[0][1][3]).exists()


def test_restore_helper_can_delete_staged_archive():
    helper = (Path(__file__).resolve().parents[3] / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
    assert "site-file-delete)" in helper
    assert "refusing to delete through a symlink" in helper
