"""0027 renames the target table and adds the S3 columns.

backup_schedules.target_id points at this table, so the rename has to leave
existing schedules still resolving to their target. SQLite does not enforce the
foreign key here, which means a broken reference would not raise -- it would
just silently stop finding the target at 2am.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

BACKEND = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND / "alembic.ini"


def _alembic(db_path: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *args],
        cwd=str(BACKEND),
        capture_output=True,
        text=True,
        env={**os.environ, "DATABASE_URL": f"sqlite:///{db_path.as_posix()}"},
    )


@pytest.fixture
def seeded_db(tmp_path):
    db_path = tmp_path / "opanel.db"
    result = _alembic(db_path, "upgrade", "0026_drop_website_http_flood")
    if result.returncode != 0:
        pytest.skip(f"alembic unavailable: {result.stderr[-400:]}")

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO sftp_backup_targets (id, name, host, port, username, password, remote_path, is_active) "
            "VALUES (1, 'offsite', 'backup.example.test', 22, 'bk', 'enc', '/backups/opanel', 1)"
        ))
        conn.execute(text(
            "INSERT INTO backup_schedules (id, target_id, schedule, retention, is_active, last_status) "
            "VALUES (5, 1, '0 2 * * *', 7, 1, 'pending')"
        ))
    engine.dispose()
    return db_path


def test_upgrade_renames_the_table_and_keeps_the_row(seeded_db):
    result = _alembic(seeded_db, "upgrade", "head")
    assert result.returncode == 0, result.stderr

    engine = create_engine(f"sqlite:///{seeded_db.as_posix()}")
    with engine.connect() as conn:
        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert "backup_targets" in tables
        assert "sftp_backup_targets" not in tables

        row = conn.execute(text("SELECT name, host, kind FROM backup_targets WHERE id = 1")).one()
        assert row == ("offsite", "backup.example.test", "sftp")
    engine.dispose()


def test_a_schedule_still_finds_its_target_after_the_rename(seeded_db):
    """The foreign key is not enforced by SQLite, so a broken reference would
    surface as a missing target during a scheduled run, not as an error here."""
    assert _alembic(seeded_db, "upgrade", "head").returncode == 0

    engine = create_engine(f"sqlite:///{seeded_db.as_posix()}")
    with engine.connect() as conn:
        joined = conn.execute(text(
            "SELECT t.name FROM backup_schedules s JOIN backup_targets t ON t.id = s.target_id "
            "WHERE s.id = 5"
        )).scalar()
        assert joined == "offsite"
    engine.dispose()


def test_s3_columns_exist_with_usable_defaults(seeded_db):
    assert _alembic(seeded_db, "upgrade", "head").returncode == 0

    engine = create_engine(f"sqlite:///{seeded_db.as_posix()}")
    with engine.connect() as conn:
        columns = {row[1]: row for row in conn.execute(text("PRAGMA table_info(backup_targets)"))}
        for name in ("kind", "s3_endpoint", "s3_region", "s3_bucket",
                     "s3_access_key", "s3_secret_key", "s3_use_path_style"):
            assert name in columns, name

        # An existing SFTP row must read back with usable values, not NULLs the
        # ORM would then have to special-case.
        row = conn.execute(text(
            "SELECT s3_endpoint, s3_region, s3_bucket, s3_access_key, s3_use_path_style "
            "FROM backup_targets WHERE id = 1"
        )).one()
        assert row == ("", "us-east-1", "", "", 0)
    engine.dispose()


def test_an_s3_row_can_be_inserted(seeded_db):
    assert _alembic(seeded_db, "upgrade", "head").returncode == 0

    engine = create_engine(f"sqlite:///{seeded_db.as_posix()}")
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO backup_targets (id, name, kind, s3_bucket, s3_access_key, s3_secret_key, "
            "s3_endpoint, s3_region, s3_use_path_style, remote_path, is_active, host, port, username) "
            "VALUES (2, 'wasabi', 's3', 'opanel-backups', 'AK', 'enc', 'https://s3.wasabisys.com', "
            "'ap-northeast-1', 0, 'opanel', 1, '', 22, '')"
        ))
    with engine.connect() as conn:
        kinds = dict(conn.execute(text("SELECT name, kind FROM backup_targets")).all())
        assert kinds == {"offsite": "sftp", "wasabi": "s3"}
    engine.dispose()


def test_downgrade_restores_the_old_shape(seeded_db):
    assert _alembic(seeded_db, "upgrade", "head").returncode == 0
    result = _alembic(seeded_db, "downgrade", "0026_drop_website_http_flood")
    assert result.returncode == 0, result.stderr

    engine = create_engine(f"sqlite:///{seeded_db.as_posix()}")
    with engine.connect() as conn:
        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
        assert "sftp_backup_targets" in tables
        assert conn.execute(text("SELECT count(*) FROM sftp_backup_targets")).scalar() == 1
    engine.dispose()
