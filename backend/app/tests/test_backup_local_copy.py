"""A backup sent off-server is not kept on this disk too.

Reported from a box with an S3 schedule for 30 accounts: one Run now left
26 GB of <user>-wednesday.tar.gz under /var/backups/opanel/users as well as on
S3, and the local prune held seven of them per account -- a week of full
backups in both places.
"""
import tarfile
from types import SimpleNamespace

import pytest

from app.services import backup, backup_scheduler


class _DB:
    def commit(self):
        pass


def _schedule(target_id):
    return SimpleNamespace(target_id=target_id, retention=7, last_run_at=None,
                           last_status="", last_message="")


@pytest.fixture
def run(tmp_path, monkeypatch):
    """run_schedule for one account, with the archive written for real and
    the upload replaced by `upload`."""
    root = tmp_path / "backups"
    monkeypatch.setattr(backup.settings, "backup_root", str(root))
    monkeypatch.setattr(backup_scheduler, "_schedule_users", lambda db, s: [SimpleNamespace(username="acme")])

    def create(user, db, filename=None, skipped=None, on_progress=None):
        path = root / "users" / user.username / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"archive")
        return str(path)

    monkeypatch.setattr(backup, "create_user_backup", create)

    def go(schedule, upload):
        monkeypatch.setattr(backup_scheduler, "_upload_if_configured", upload)
        ok = backup_scheduler.run_schedule(_DB(), schedule)
        return ok, sorted(p.name for p in (root / "users" / "acme").iterdir())

    return go


def test_an_uploaded_archive_is_not_kept_here(run):
    ok, left = run(_schedule(1), lambda db, s, archive, username: "idrive:acme/acme-monday.tar.gz")
    assert ok
    assert left == []


def test_a_failed_upload_keeps_the_only_copy(run):
    def fail(db, s, archive, username):
        raise RuntimeError("S3 unreachable")

    ok, left = run(_schedule(1), fail)
    assert not ok
    assert len(left) == 1


def test_a_schedule_without_a_destination_keeps_its_archives(run):
    ok, left = run(_schedule(None), lambda db, s, archive, username: archive)
    assert ok
    assert len(left) == 1


def test_discard_never_reaches_outside_the_backup_root(tmp_path, monkeypatch):
    monkeypatch.setattr(backup.settings, "backup_root", str(tmp_path / "backups"))
    outside = tmp_path / "elsewhere.tar.gz"
    outside.write_bytes(b"x")

    backup.discard_local_copy(str(outside))

    assert outside.exists()


def test_manual_uploads_discard_the_local_copy_too():
    import inspect

    from app.api import maintenance

    for job in (maintenance._run_user_backup_job, maintenance._run_sftp_backup_job):
        source = inspect.getsource(job)
        assert source.index("upload_archive_to_target(") < source.index("backup.discard_local_copy(archive)")


def test_a_site_backup_leaves_no_loose_sql_dump(tmp_path, monkeypatch):
    site_root = tmp_path / "site"
    (site_root / "public_html").mkdir(parents=True)
    (site_root / "public_html" / "index.php").write_text("<?php", encoding="utf-8")
    monkeypatch.setattr(backup.settings, "backup_root", str(tmp_path / "backups"))
    monkeypatch.setattr(backup.shell, "run", lambda *a, **k: None)
    monkeypatch.setattr(backup.mariadb, "export_database",
                        lambda name, out: open(out, "w", encoding="utf-8").write("-- dump\n"))

    archive = backup.create_backup(SimpleNamespace(domain="acme.test", root_path=str(site_root)), "acme_wp")

    with tarfile.open(archive) as tar:
        assert any(name.startswith("database/") and name.endswith(".sql") for name in tar.getnames())
    assert list((tmp_path / "backups" / "sites" / "acme.test").glob("*.sql")) == []
