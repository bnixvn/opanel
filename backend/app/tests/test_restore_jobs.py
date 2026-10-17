"""Restores as records: each archive's outcome, interrupted runs, and a retry (as SNPanel's, 2026-10-17)."""
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import maintenance
from app.api.deps import get_current_user
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.entities import RestoreJob, User
from app.services import restore_jobs


@pytest.fixture
def env(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(restore_jobs, "SessionLocal", Session)
    monkeypatch.setattr(restore_jobs, "_live", set())
    db = Session()
    boss = User(username="boss", email="boss@example.test", role="admin", is_active=True,
                hashed_password=hash_password("PasswordLongEnough1"))
    db.add(boss)
    db.commit()
    submitted = []
    monkeypatch.setattr(maintenance._backup_job_executor, "submit", lambda fn, *args: submitted.append(args))
    monkeypatch.setattr(maintenance, "_backup_jobs", {})
    monkeypatch.setattr(maintenance, "_da_import_jobs", {})
    monkeypatch.setattr(maintenance.restore_sources, "check_ref", lambda source, kind, ref: ref)

    def get_test_db():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = get_test_db
    app.dependency_overrides[get_current_user] = lambda: boss
    try:
        yield SimpleNamespace(db=db, client=TestClient(app), boss=boss, submitted=submitted)
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
        db.close()


ITEMS = [{"kind": "opanel", "ref": "/b/alice-monday.tar.gz"}, {"kind": "opanel", "ref": "/b/bob-monday.tar.gz"},
         {"kind": "directadmin", "ref": "/b/user.admin.carol.tar.zst"}]


def test_each_archive_is_recorded_with_how_it_went(env):
    row = restore_jobs.create(env.db, user_id=env.boss.id, source_kind="local", target_id=None, label="this server",
                              overwrite=False, items=ITEMS)
    restore_jobs.started(row.id)
    restore_jobs.item(row.id, 0, status="done", note="alice: 2 site(s)")
    restore_jobs.item(row.id, 1, status="failed", error="disk full")
    restore_jobs.finished(row.id)
    body = env.client.get("/api/maintenance/restore/jobs").json()["jobs"][0]
    assert body["status"] == "error" and body["done"] == 1 and body["failed"] == 1
    assert [item["status"] for item in body["items"]] == ["done", "failed", "pending"]
    assert body["items"][1]["error"] == "disk full" and body["can_retry"] is True


def test_a_run_a_restart_cut_short_reads_as_interrupted(env):
    row = restore_jobs.create(env.db, user_id=env.boss.id, source_kind="local", target_id=None, label="this server",
                              overwrite=False, items=ITEMS)
    restore_jobs.started(row.id)
    restore_jobs.item(row.id, 0, status="done", note="ok")
    restore_jobs.item(row.id, 1, status="running")
    restore_jobs._live.clear()  # the panel restarted
    body = env.client.get("/api/maintenance/restore/jobs").json()["jobs"][0]
    assert body["status"] == "interrupted"
    assert [item["status"] for item in body["items"]] == ["done", "interrupted", "interrupted"]


def test_retry_runs_only_what_did_not_finish(env):
    row = restore_jobs.create(env.db, user_id=env.boss.id, source_kind="local", target_id=None, label="this server",
                              overwrite=True, items=ITEMS)
    restore_jobs.item(row.id, 0, status="done")
    restore_jobs.item(row.id, 1, status="failed", error="x")
    restore_jobs.item(row.id, 2, status="failed", error="y")
    restore_jobs.finished(row.id)
    answer = env.client.post(f"/api/maintenance/restore/jobs/{row.id}/retry")
    assert answer.status_code == 200, answer.text
    job_id, user_id, source, items, overwrite, record_id = env.submitted[0]
    assert [item["ref"] for item in items] == ["/b/bob-monday.tar.gz", "/b/user.admin.carol.tar.zst"]
    assert overwrite is True and source.kind == "local"
    new = env.db.get(RestoreJob, record_id)
    env.db.refresh(new)
    assert new.retry_of == row.id and len(json.loads(new.items)) == 2


def test_a_typed_in_server_is_not_retried_from_here(env):
    row = restore_jobs.create(env.db, user_id=env.boss.id, source_kind="remote", target_id=None,
                              label="sftp://admin@old:22", overwrite=False, items=ITEMS[:1])
    restore_jobs.item(row.id, 0, status="failed", error="timeout")
    restore_jobs.finished(row.id)
    assert env.client.get("/api/maintenance/restore/jobs").json()["jobs"][0]["can_retry"] is False
    assert env.client.post(f"/api/maintenance/restore/jobs/{row.id}/retry").status_code == 400
    assert env.submitted == []
