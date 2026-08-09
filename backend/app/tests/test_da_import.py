from io import BytesIO
import tarfile

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
        def __init__(self, args, stdout=None, stderr=None):
            calls.append(args)
            self.stdout = BytesIO(raw_tar.getvalue())
            self.returncode = 0

        def communicate(self, timeout=None):
            return b"", b""

    monkeypatch.setattr(da_import.shutil, "which", lambda name: "/usr/bin/zstdcat" if name == "zstdcat" else None)
    monkeypatch.setattr(da_import.subprocess, "Popen", FakePopen)

    da_import._safe_extract_tar(zst_archive, destination)

    assert calls == [["/usr/bin/zstdcat", str(zst_archive)]]
    assert (destination / "domains/example.test/public_html/index.html").read_text(encoding="utf-8") == "restored"
