from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import maintenance
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.entities import DatabaseAccount, User
from app.schemas.schemas import DAImportBatch
from app.services import da_import

ADMIN = SimpleNamespace(id=1, role="admin")


@pytest.fixture
def queue(tmp_path, monkeypatch):
    """A DA backup dir with three archives, and an executor that only records."""
    backup_dir = tmp_path / "da"
    backup_dir.mkdir()
    for name in ("a.tar.gz", "b.tar.zst", "c.tar"):
        (backup_dir / name).write_bytes(b"x")
    monkeypatch.setattr(settings, "da_backup_dir", str(backup_dir))
    submitted = []
    monkeypatch.setattr(maintenance._da_import_job_executor, "submit",
                        lambda fn, job_id, backup_file, overwrite: submitted.append((backup_file, overwrite)))
    monkeypatch.setattr(maintenance, "log_action", lambda *a, **k: None)
    monkeypatch.setattr(maintenance, "_da_import_jobs", {})
    return submitted


def _batch(files, overwrite=False):
    return maintenance.start_da_import_batch(
        DAImportBatch(backup_files=files, overwrite=overwrite), request=None, db=None, current_user=ADMIN,
    )


def test_batch_queues_one_job_per_archive_with_the_overwrite_flag(queue):
    result = _batch(["a.tar.gz", "b.tar.zst", "a.tar.gz"], overwrite=True)

    assert queue == [("a.tar.gz", True), ("b.tar.zst", True)]
    assert [job["backup_file"] for job in result["jobs"]] == ["a.tar.gz", "b.tar.zst"]
    assert all(job["overwrite"] and job["status"] == "queued" for job in result["jobs"])
    assert result["skipped"] == []


def test_batch_queues_nothing_when_one_name_is_missing(queue):
    with pytest.raises(HTTPException) as exc:
        _batch(["a.tar.gz", "gone.tar.gz"])

    assert exc.value.status_code == 404
    assert queue == []


def test_batch_skips_an_archive_already_waiting(queue):
    _batch(["a.tar.gz"])
    result = _batch(["a.tar.gz", "c.tar"])

    assert queue == [("a.tar.gz", False), ("c.tar", False)]
    assert result["skipped"] == ["a.tar.gz"]


def test_batch_is_admin_only(queue):
    with pytest.raises(HTTPException) as exc:
        maintenance.start_da_import_batch(
            DAImportBatch(backup_files=["a.tar.gz"]), request=None, db=None,
            current_user=SimpleNamespace(id=2, role="end_user"),
        )
    assert exc.value.status_code == 403
    assert queue == []


def test_single_import_rejects_a_missing_archive(queue):
    with pytest.raises(HTTPException) as exc:
        maintenance.start_da_import("gone.tar.gz", request=None, db=None, current_user=ADMIN)
    assert exc.value.status_code == 404
    assert queue == []


# ---------------------------------------------------------------------------
# Which databases an import (re)loads
# ---------------------------------------------------------------------------

@pytest.fixture
def accounts():
    db = SessionLocal()
    owner = User(username="dbowner", email="o@example.test",
                 hashed_password=hash_password("PasswordLongEnough1"), role="end_user")
    other = User(username="dbother", email="t@example.test",
                 hashed_password=hash_password("PasswordLongEnough1"), role="end_user")
    db.add_all([owner, other])
    db.flush()
    db.add_all([
        DatabaseAccount(owner_id=owner.id, website_id=None, db_name="dbowner_wp", db_user="dbowner_wp", db_password="x"),
        DatabaseAccount(owner_id=other.id, website_id=None, db_name="dbother_wp", db_user="dbother_wp", db_password="x"),
    ])
    db.commit()
    try:
        yield db, owner
    finally:
        db.rollback()
        db.query(DatabaseAccount).filter(DatabaseAccount.db_name.in_(["dbowner_wp", "dbother_wp"])).delete()
        db.query(User).filter(User.username.in_(["dbowner", "dbother"])).delete()
        db.commit()
        db.close()


def _row(db, name):
    return db.query(DatabaseAccount).filter(DatabaseAccount.db_name == name).first()


def test_a_new_database_is_imported(accounts):
    db, owner = accounts
    assert da_import._claim_database(db, "dbowner_new", owner.id, False, set()) == (None, None)


def test_the_accounts_own_database_is_kept_without_overwrite(accounts):
    db, owner = accounts
    assert da_import._claim_database(db, "dbowner_wp", owner.id, False, set()) == ("already imported", None)


def test_overwrite_reloads_the_accounts_own_database_into_its_record(accounts):
    """Overwrite used to skip a database it had imported before, unlinked or not.

    The record is handed back, not deleted, so a link to a website the archive
    does not carry and the password that site uses both survive.
    """
    db, owner = accounts
    reason, reuse = da_import._claim_database(db, "dbowner_wp", owner.id, True, set())
    assert reason is None
    assert reuse is _row(db, "dbowner_wp")


def test_overwrite_never_takes_another_accounts_database(accounts):
    db, owner = accounts
    reason, reuse = da_import._claim_database(db, "dbother_wp", owner.id, True, set())
    assert reason == da_import.FOREIGN_DATABASE
    assert reuse is None


def test_a_database_is_loaded_once_per_run_even_under_overwrite(accounts):
    db, owner = accounts
    reason, reuse = da_import._claim_database(db, "dbowner_wp", owner.id, True, {"dbowner_wp"})
    assert reason == "already imported from this archive"
    assert reuse is None


def test_an_unreadable_stored_password_falls_back_to_a_fresh_one(accounts):
    db, owner = accounts
    assert da_import._stored_password(_row(db, "dbowner_wp")) is None
