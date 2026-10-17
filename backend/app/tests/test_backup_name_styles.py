"""How a schedule names its archives, how many it keeps, and editing a schedule
or a destination (as SNPanel's, 2026-10-17).

A schedule names its archives by weekday (the default, what every schedule
did before), by date, with a timestamp, or by the account alone. Its prune
counts only its own style's names for the account - here, in the account's
S3 folder, and in an SFTP folder for the names that never repeat.
"""
import os
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.core.database import Base, get_db
from app.core.secrets import decrypt, encrypt
from app.core.security import hash_password
from app.main import app
from app.models.entities import BackupSchedule, BackupTarget, User
from app.services import backup, backup_scheduler, restore_sources

NOW = datetime(2026, 10, 17, 2, 0)  # a Saturday


def test_each_style_names_the_archive():
    assert backup.archive_filename("acme", "weekday", NOW) == "acme-saturday.tar.gz"
    assert backup.archive_filename("acme", "date", NOW) == "acme-2026-10-17.tar.gz"
    assert backup.archive_filename("acme", "none", NOW) == "acme.tar.gz"
    stamped = backup.archive_filename("acme", "timestamp", NOW)
    assert stamped.startswith("user-acme-") and len(stamped) == len("user-acme-20261017020000.tar.gz")


def test_a_family_is_one_accounts_and_one_styles():
    assert backup.in_family("acme-2026-10-17.tar.gz", "acme", "date")
    assert not backup.in_family("acme-shop-2026-10-17.tar.gz", "acme", "date")
    assert not backup.in_family("acme-saturday.tar.gz", "acme", "date")
    assert backup.in_family("acme-saturday.tar.gz", "acme", "weekday")
    assert not backup.in_family("acme-manual-saturday.tar.gz", "acme", "weekday")
    assert backup.in_family("user-acme-20261017020000.tar.gz", "acme", "timestamp")
    assert not backup.in_family("user-acme-shop-20261017020000.tar.gz", "acme", "timestamp")
    assert backup.in_family("acme.tar.gz", "acme", "none") and not backup.in_family("acme2.tar.gz", "acme", "none")
    for style in backup.NAME_STYLES:
        assert backup.scheduled_filename_ok("acme", backup.archive_filename("acme", style, NOW))
    assert not backup.scheduled_filename_ok("acme", "../etc/passwd")


def test_the_restore_list_knows_whose_each_name_is():
    for style in backup.NAME_STYLES:
        name = backup.archive_filename("acme", style, NOW)
        assert restore_sources.account_of(restore_sources.archive_kind(name), name, "acme") == "acme"
    assert restore_sources.account_of("opanel", "user-acme-20261017020000.tar.gz", "") == "acme"
    assert restore_sources.account_of("opanel", "acme-2026-10-17.tar.gz", "") == "acme"


def test_a_dated_prune_keeps_its_newest_and_leaves_the_rest_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "_user_backup_dir", lambda name: tmp_path)
    monkeypatch.setattr(backup.settings, "command_dry_run", False)
    names = [f"acme-2026-10-{day:02d}.tar.gz" for day in range(10, 18)] + ["acme-monday.tar.gz", "acme-manual-friday.tar.gz"]
    for index, name in enumerate(names):
        (tmp_path / name).write_bytes(b"x")
        os.utime(tmp_path / name, (1_760_000_000 + index * 60,) * 2)

    backup.prune_user_backups("acme", 3, "date")

    left = sorted(path.name for path in tmp_path.glob("*.tar.gz"))
    assert left == ["acme-2026-10-15.tar.gz", "acme-2026-10-16.tar.gz", "acme-2026-10-17.tar.gz",
                    "acme-manual-friday.tar.gz", "acme-monday.tar.gz"]


def test_the_schedule_writes_and_prunes_by_its_style(tmp_path, monkeypatch):
    root = tmp_path / "backups"
    monkeypatch.setattr(backup.settings, "backup_root", str(root))
    monkeypatch.setattr(backup_scheduler, "_schedule_users", lambda db, s: [SimpleNamespace(username="acme")])
    written, pruned = [], []

    def create(user, db, filename=None, skipped=None, on_progress=None):
        written.append(filename)
        return str(root / filename)

    monkeypatch.setattr(backup, "create_user_backup", create)
    monkeypatch.setattr(backup, "prune_user_backups", lambda user, keep, style: pruned.append((user, keep, style)))
    monkeypatch.setattr(backup_scheduler, "_upload_if_configured", lambda db, s, archive, username: archive)
    schedule = SimpleNamespace(target_id=None, retention=14, name_style="date", last_run_at=None,
                               last_status="", last_message="")

    assert backup_scheduler.run_schedule(SimpleNamespace(commit=lambda: None), schedule, NOW)
    assert written == ["acme-2026-10-17.tar.gz"] and pruned == [("acme", 14, "date")]


def test_an_sftp_destination_is_held_to_the_retention_for_dated_names(monkeypatch):
    calls = []
    monkeypatch.setattr(backup, "upload_to_sftp", lambda archive, **kw: {
        "remote_file": f"/b/{os.path.basename(archive)}", "host_key_type": "ssh-ed25519", "host_key_fingerprint": "SHA256:x"})
    monkeypatch.setattr(backup, "prune_sftp_backups", lambda **kw: calls.append(kw) or 0)
    target = SimpleNamespace(id=1, name="box", kind="sftp", is_active=True, host="h", port=22, username="u",
                             remote_path="/b", password=encrypt("pw"), private_key=None,
                             host_key_type="ssh-ed25519", host_key_fingerprint="SHA256:x")

    class Query:
        def filter(self, *a):
            return self

        def first(self):
            return target

    db = SimpleNamespace(query=lambda model: Query(), commit=lambda: None)
    for style, expected in (("date", 1), ("timestamp", 1), ("weekday", 0), ("none", 0)):
        calls.clear()
        schedule = SimpleNamespace(target_id=1, retention=5, name_style=style)
        backup_scheduler._upload_if_configured(db, schedule, "/x/acme-2026-10-17.tar.gz", "acme")
        assert len(calls) == expected, style
    family = None
    calls.clear()
    backup_scheduler._upload_if_configured(db, SimpleNamespace(target_id=1, retention=5, name_style="date"),
                                           "/x/acme-2026-10-17.tar.gz", "acme")
    family = calls[0]["family"]
    assert calls[0]["keep"] == 5 and family("acme-2026-10-01.tar.gz") and not family("bob-2026-10-01.tar.gz")


# --- the API -------------------------------------------------------------------------------

@pytest.fixture
def api(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    boss = User(username="boss", email="boss@example.test", role="admin", is_active=True,
                hashed_password=hash_password("PasswordLongEnough1"))
    acme = User(username="acme", email="acme@example.test", role="end_user", is_active=True,
                hashed_password=hash_password("PasswordLongEnough1"))
    db.add_all([boss, acme])
    db.commit()

    def get_test_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = get_test_db
    app.dependency_overrides[get_current_user] = lambda: boss
    try:
        yield SimpleNamespace(db=db, client=TestClient(app), acme=acme)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


def test_a_schedule_is_made_and_changed_with_its_style(api):
    made = api.client.post("/api/maintenance/backup-schedules", json={
        "user_ids": [api.acme.id], "schedule": "0 2 * * *", "retention": 7, "name_style": "date"})
    assert made.status_code == 200, made.text
    assert made.json()["name_style"] == "date"
    schedule_id = made.json()["id"]
    changed = api.client.put(f"/api/maintenance/backup-schedules/{schedule_id}", json={
        "all_users": True, "schedule": "30 3 * * 0", "retention": 4, "name_style": "timestamp", "is_active": False})
    assert changed.status_code == 200, changed.text
    row = api.db.get(BackupSchedule, schedule_id)
    api.db.refresh(row)
    assert (row.all_users, row.schedule, row.retention, row.name_style, row.is_active) == (True, "30 3 * * 0", 4, "timestamp", False)
    assert api.client.put(f"/api/maintenance/backup-schedules/{schedule_id}", json={
        "all_users": True, "name_style": "hourly"}).status_code == 422
    assert api.client.put("/api/maintenance/backup-schedules/999", json={"all_users": True}).status_code == 404


def test_a_destination_is_changed_and_its_secrets_kept_when_left_empty(api):
    made = api.client.post("/api/maintenance/backup-targets", json={
        "name": "box", "kind": "sftp", "host": "a.example", "username": "u", "password": "first-pass", "remote_path": "/b"})
    assert made.status_code == 200, made.text
    target_id = made.json()["id"]
    row = api.db.get(BackupTarget, target_id)
    row.host_key_type, row.host_key_fingerprint = "ssh-ed25519", "SHA256:pinned"
    api.db.commit()

    same_host = api.client.put(f"/api/maintenance/backup-targets/{target_id}", json={
        "name": "box two", "kind": "sftp", "host": "a.example", "username": "u2", "remote_path": "/c"})
    assert same_host.status_code == 200, same_host.text
    api.db.refresh(row)
    assert (row.name, row.username, row.remote_path) == ("box two", "u2", "/c")
    assert decrypt(row.password) == "first-pass" and row.host_key_fingerprint == "SHA256:pinned"

    moved = api.client.put(f"/api/maintenance/backup-targets/{target_id}", json={
        "name": "box two", "kind": "sftp", "host": "b.example", "username": "u2", "remote_path": "/c", "password": "second-pass"})
    assert moved.status_code == 200
    api.db.refresh(row)
    assert decrypt(row.password) == "second-pass" and row.host_key_fingerprint is None

    assert api.client.put(f"/api/maintenance/backup-targets/{target_id}", json={
        "name": "box two", "kind": "s3", "s3_bucket": "bucket", "s3_access_key": "k", "remote_path": "p"}).status_code == 400


def test_an_sftp_destination_is_tested_and_its_key_pinned(api, monkeypatch):
    made = api.client.post("/api/maintenance/backup-targets", json={
        "name": "box", "kind": "sftp", "host": "a.example", "username": "u", "password": "pw-pw-pw", "remote_path": "/b"})
    target_id = made.json()["id"]
    seen = {}

    def fake_test(**kw):
        seen.update(kw)
        return {"host_key_type": "ssh-ed25519", "host_key_fingerprint": "SHA256:new"}

    monkeypatch.setattr(backup, "test_sftp_target", fake_test)
    answer = api.client.post(f"/api/maintenance/backup-targets/{target_id}/test")
    assert answer.status_code == 200, answer.text
    assert seen["password"] == "pw-pw-pw" and seen["expected_host_key_fingerprint"] is None
    row = api.db.get(BackupTarget, target_id)
    api.db.refresh(row)
    assert row.host_key_fingerprint == "SHA256:new"
