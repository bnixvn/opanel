"""OPanel/BPanel Import: an account pulled from ANOTHER server whose username is
already here is someone else's account (2026-11-19).

Restoring this server's own backup over its own account is right, and stays so.
But the import reused that path for archives from other servers, where the same
username is a different account: the archive's sites were added to it and its
databases of the same name re-imported. Now such an account is refused -- before
it is even packed on the other server -- unless the operator ticks "Restore over
accounts already on this server"; the list marks those accounts.
"""
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api import maintenance
from app.services import backup, restore_sources

PROJECT_ROOT = Path(__file__).resolve().parents[3]
APP = (PROJECT_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
ADMIN = SimpleNamespace(id=1, role="admin")
OTHER_SERVER = restore_sources.Source(
    "ssh", remote=restore_sources.Remote(protocol="ssh", host="203.0.113.7", port=22, panel="bnix"))
LOCAL = restore_sources.Source("local")


class _Query:
    def __init__(self, found):
        self.found = found

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self.found


def _db(existing_username=None):
    found = SimpleNamespace(id=7, username=existing_username) if existing_username else None
    return SimpleNamespace(query=lambda *models: _Query(found), rollback=lambda: None, close=lambda: None)


def test_the_restore_refuses_an_account_already_here_when_asked(monkeypatch):
    monkeypatch.setattr(backup, "user_backup_path", lambda name: Path(name))
    monkeypatch.setattr(backup, "read_backup_manifest",
                        lambda path: {"kind": "bpanel_user", "user": {"username": "alice"}})
    with pytest.raises(ValueError, match="already on this server"):
        backup.restore_user_backup("user-alice.tar.gz", _db("alice"), refuse_existing_user=True)


def _run(monkeypatch, *, existing, overwrite, source=OTHER_SERVER, ref="alice"):
    calls = SimpleNamespace(fetched=[], restored=[])

    def fetch(src, kind, ref_, size=0, on_progress=None, on_status=None):
        calls.fetched.append(ref_)
        return f"/tmp/user-{ref_}.tar.gz"

    def restore(path, db, on_progress=None, refuse_existing_user=False):
        calls.restored.append((path, refuse_existing_user))
        return {"username": ref, "websites": [{"domain": "alice.example"}]}

    monkeypatch.setattr(restore_sources, "fetch", fetch)
    monkeypatch.setattr(maintenance.backup, "restore_user_backup", restore)
    monkeypatch.setattr(maintenance, "SessionLocal", lambda: _db(existing))
    monkeypatch.setattr(maintenance, "log_action", lambda *a, **k: None)
    monkeypatch.setattr(maintenance.notifications, "notify_admin", lambda *a, **k: None)
    monkeypatch.setattr(maintenance, "_backup_jobs", {})
    job = maintenance._queue_backup_job(ADMIN, "user_restore_batch", "queued", count=1)
    maintenance._run_restore_job(job["job_id"], 1, source, [{"kind": "opanel", "ref": ref}], overwrite)
    return calls, maintenance._backup_jobs[job["job_id"]]


def test_an_account_already_here_is_refused_before_it_is_packed_there(monkeypatch):
    calls, job = _run(monkeypatch, existing="alice", overwrite=False)
    assert calls.fetched == [], "nothing is packed or downloaded on the other server"
    assert calls.restored == []
    assert job["status"] == "error"
    assert "already on this server" in job["error"]


def test_ticking_restore_over_restores_it(monkeypatch):
    calls, job = _run(monkeypatch, existing="alice", overwrite=True)
    assert calls.fetched == ["alice"]
    assert calls.restored == [("/tmp/user-alice.tar.gz", False)]
    assert job["status"] == "done"


def test_a_new_account_from_the_other_server_still_restores_and_stays_guarded(monkeypatch):
    calls, job = _run(monkeypatch, existing=None, overwrite=False)
    assert calls.fetched == ["alice"]
    assert calls.restored == [("/tmp/user-alice.tar.gz", True)], \
        "the restore checks again: the archive could name a user other than the one listed"
    assert job["status"] == "done"


def test_this_servers_own_backup_still_restores_over_its_own_account(monkeypatch):
    calls, job = _run(monkeypatch, existing="alice", overwrite=False, source=LOCAL, ref="/tmp/user-alice.tar.gz")
    assert calls.restored == [("/tmp/user-alice.tar.gz", False)]
    assert job["status"] == "done"


def test_the_list_marks_accounts_already_here():
    source = inspect.getsource(maintenance.list_restore_source)
    assert 'result["existing"]' in source
    assert "User.username.in_(accounts)" in source


def test_the_page_marks_them_and_offers_to_restore_over_them():
    assert "restoreList?.existing" in APP
    assert 'tr("Already here")' in APP
    assert 'tr("Restore over accounts already on this server ({0})"' in APP
    assert "Already on this server, so skipped: {0}." in APP
