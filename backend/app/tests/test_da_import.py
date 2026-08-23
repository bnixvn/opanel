from io import BytesIO
import os
import tarfile
import threading

import pytest

from app.core.config import settings
from app.services import da_import


def test_import_da_backup_resolves_filename_from_da_backup_dir(tmp_path, monkeypatch):
    backup_dir = tmp_path / "da"
    backup_dir.mkdir()
    archive = backup_dir / "user.admin.tar.zst"
    archive.write_bytes(b"placeholder")
    seen = {}

    def fake_extract(path, stage):
        seen["archive"] = path
        seen["stage_exists"] = stage.exists()

    def fake_process(stage, archive_name, db, credentials):
        credentials.append("panel-user: password")
        return {"archive": archive_name, "created_users": ["panel-user"]}

    monkeypatch.setattr(settings, "da_backup_dir", str(backup_dir))
    monkeypatch.setattr(da_import, "_safe_extract_tar", fake_extract)
    monkeypatch.setattr(da_import, "_process_archive", fake_process)

    summary = da_import.import_da_backup("user.admin.tar.zst", db=object())

    assert summary["archive"] == "user.admin.tar.zst"
    assert seen == {"archive": archive.resolve(), "stage_exists": True}
    assert (backup_dir / "user.admin-credentials.txt").read_text(encoding="utf-8").endswith(
        "panel-user: password\n"
    )


def test_delete_da_backup_accepts_filename_only(tmp_path, monkeypatch):
    backup_dir = tmp_path / "da"
    backup_dir.mkdir()
    archive = backup_dir / "user.admin.tar.zst"
    archive.write_bytes(b"placeholder")
    monkeypatch.setattr(settings, "da_backup_dir", str(backup_dir))

    deleted = da_import.delete_da_backup("user.admin.tar.zst")

    assert deleted == "user.admin.tar.zst"
    assert not archive.exists()


def test_delete_da_backup_rejects_nested_relative_path(tmp_path, monkeypatch):
    backup_dir = tmp_path / "da"
    nested = backup_dir / "nested"
    nested.mkdir(parents=True)
    (nested / "user.admin.tar.zst").write_bytes(b"placeholder")
    monkeypatch.setattr(settings, "da_backup_dir", str(backup_dir))

    with pytest.raises(FileNotFoundError, match="Backup not found"):
        da_import.delete_da_backup("nested/user.admin.tar.zst")


def test_safe_extract_tar_zst_uses_zstdcat_without_decompress_flag(tmp_path, monkeypatch):
    raw_tar = BytesIO()
    source = tmp_path / "index.html"
    source.write_text("restored", encoding="utf-8")
    with tarfile.open(fileobj=raw_tar, mode="w") as archive:
        archive.add(source, arcname="domains/example.test/public_html/index.html")
    raw_tar.seek(0)

    zst_archive = tmp_path / "user.admin.tar.zst"
    zst_archive.write_bytes(b"compressed-placeholder")
    destination = tmp_path / "stage"
    destination.mkdir()
    calls = []

    class FakePopen:
        """Feeds the tar bytes through a real OS pipe (non-seekable), like a
        genuine subprocess.Popen(..., stdout=PIPE) would — a BytesIO stand-in
        would stay seekable and silently miss bugs that only show up on a
        real pipe (e.g. "[Errno 29] Illegal seek")."""

        def __init__(self, args, stdout=None, stderr=None):
            calls.append(args)
            read_fd, write_fd = os.pipe()
            self.stdout = os.fdopen(read_fd, "rb")
            self.returncode = 0

            def _feed():
                with os.fdopen(write_fd, "wb") as w:
                    w.write(raw_tar.getvalue())

            threading.Thread(target=_feed, daemon=True).start()

        def communicate(self, timeout=None):
            return b"", b""

    monkeypatch.setattr(da_import.shutil, "which", lambda name: "/usr/bin/zstdcat" if name == "zstdcat" else None)
    monkeypatch.setattr(da_import.subprocess, "Popen", FakePopen)

    da_import._safe_extract_tar(zst_archive, destination)

    assert calls == [["/usr/bin/zstdcat", str(zst_archive)]]
    assert (destination / "domains/example.test/public_html/index.html").read_text(encoding="utf-8") == "restored"


def test_da_db_credentials_reads_a_plaintext_password(tmp_path):
    backup = tmp_path / "backup"
    backup.mkdir()
    dump = backup / "admin_shop.sql"
    dump.write_text("-- dump", encoding="utf-8")
    (backup / "admin_shop.conf").write_text(
        "user=admin_shop\npasswd=S3cret!pass\n", encoding="utf-8"
    )

    assert da_import._da_db_credentials(dump, tmp_path) == {"password": "S3cret!pass", "password_hash": ""}


def test_da_db_credentials_reports_a_hash_separately(tmp_path):
    """A mysql_native hash cannot be shown to the admin as a password, so it
    must not be handed back as one -- the import falls back to a fresh
    password and says so in the log."""
    backup = tmp_path / "backup"
    backup.mkdir()
    dump = backup / "admin_blog.sql.gz"
    dump.write_bytes(b"")
    (backup / "admin_blog.conf").write_text(
        "admin_blog=*A1B2C3D4E5F60718293A4B5C6D7E8F90A1B2C3D4\n", encoding="utf-8"
    )

    creds = da_import._da_db_credentials(dump, tmp_path)

    assert creds["password"] == ""
    assert creds["password_hash"].startswith("*")


def test_da_db_credentials_returns_empty_without_a_conf(tmp_path):
    dump = tmp_path / "admin_none.sql"
    dump.write_text("-- dump", encoding="utf-8")

    assert da_import._da_db_credentials(dump, tmp_path) == {"password": "", "password_hash": ""}


def test_importer_reuses_the_password_from_the_site_config():
    """Regression: the importer always generated a random database password and
    relied on rewriting wp-config.php. Wherever that rewrite missed the config
    the app actually reads, the site was left pointing at a password MariaDB
    had just replaced, and the admin had to reset it by hand."""
    password, reused = da_import._import_db_password(
        {"DB_NAME": "shop", "DB_PASSWORD": "keep-me"}, {"password": "", "password_hash": ""}
    )

    assert (password, reused) == ("keep-me", True)


def test_importer_falls_back_to_the_backup_conf_password():
    password, reused = da_import._import_db_password({}, {"password": "from-conf", "password_hash": ""})

    assert (password, reused) == ("from-conf", True)


def test_importer_generates_a_password_when_the_backup_has_none():
    password, reused = da_import._import_db_password({"DB_NAME": "shop"}, {"password": "", "password_hash": "*AB"})

    assert reused is False
    assert len(password) >= 16
