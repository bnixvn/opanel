"""The admin's SFTP drop folder for large backups (operator, 2026-10-06:
"Login admin như DA").

Archives past what a browser upload takes go up over SFTP as the admin's own
login into /home/admin/backups. What must hold: the Restore tab's This server
list reads that folder, leaving out a file still arriving; a pick there must be
a plain file directly in it; a restore moves it into the panel's folder -- as
root, through the helper -- before anything reads it, and the helper checks it
is a plain, settled file with no other link before handing it over.
"""
import inspect
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import maintenance
from app.core.config import settings
from app.services import restore_sources

PROJECT_ROOT = Path(__file__).resolve().parents[3]
HELPER = (PROJECT_ROOT / "installer" / "files" / "opanel-helper.sh").read_text(encoding="utf-8")
APP = (PROJECT_ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
ADMIN = SimpleNamespace(id=1, role="admin")
LOCAL = restore_sources.Source("local")


@pytest.fixture
def inbox(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "backup_root", str(tmp_path / "backups"))
    monkeypatch.setattr(settings, "da_backup_dir", str(tmp_path / "da"))
    folder = tmp_path / "inbox"
    folder.mkdir()
    monkeypatch.setattr(restore_sources, "INBOX_DIR", folder)

    def make(name: str, age: float = 3600) -> Path:
        path = folder / name
        path.write_bytes(b"x" * 10)
        stamp = time.time() - age
        os.utime(path, (stamp, stamp))
        return path

    return SimpleNamespace(folder=folder, make=make, root=tmp_path)


def test_this_servers_list_reads_the_drop_folder(inbox):
    inbox.make("user.admin.dave.tar.zst")
    inbox.make("erin-monday.tar.gz")
    inbox.make("user.admin.big.tar.zst.filepart")
    inbox.make("user.admin.fresh.tar.zst", age=5)
    inbox.make(".hidden.tar.gz")
    inbox.make("notes.txt")
    rows = sorted(restore_sources.list_inbox(), key=lambda row: row["filename"])
    assert [(row["filename"], row["kind"], row["account"], row["location"]) for row in rows] == [
        ("erin-monday.tar.gz", "opanel", "erin", "inbox"),
        ("user.admin.dave.tar.zst", "directadmin", "dave", "inbox"),
    ], "half-uploaded, fresh, hidden and non-archive files are not offered"
    assert {row["ref"] for row in restore_sources.list_local()} >= {row["ref"] for row in rows}


def test_a_pick_in_the_drop_folder_must_be_a_plain_file_directly_in_it(inbox):
    dave = inbox.make("user.admin.dave.tar.zst")
    assert restore_sources.check_ref(LOCAL, "directadmin", str(dave)) == str(dave)
    (inbox.folder / "sub").mkdir()
    (inbox.folder / "sub" / "x.tar.gz").write_bytes(b"x")
    for bad in (str(inbox.folder / "sub" / "x.tar.gz"), str(inbox.folder / ".." / "x.tar.gz"),
                str(inbox.folder / ".hidden.tar.gz")):
        with pytest.raises((ValueError, FileNotFoundError)):
            restore_sources.check_ref(LOCAL, "opanel", bad)
    with pytest.raises(FileNotFoundError):
        restore_sources.check_ref(LOCAL, "opanel", str(inbox.folder / "missing.tar.gz"))
    with pytest.raises(ValueError):
        restore_sources.check_ref(LOCAL, "opanel", str(dave)), "a DirectAdmin name is not an OPanel pick"


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_a_link_in_the_drop_folder_is_neither_listed_nor_picked(inbox):
    target = inbox.root / "secret.tar.gz"
    target.write_bytes(b"x")
    link = inbox.folder / "erin-monday.tar.gz"
    link.symlink_to(target)
    assert restore_sources.list_inbox() == []
    with pytest.raises(FileNotFoundError):
        restore_sources.check_ref(LOCAL, "opanel", str(link))


def test_taking_one_asks_the_helper_and_returns_where_it_went(inbox, monkeypatch):
    dave = inbox.make("user.admin.dave.tar.zst")
    calls = []

    def privileged(command, helper_args=None, **kwargs):
        calls.append((command, helper_args))
        return SimpleNamespace(returncode=0, stdout=f"{inbox.root}/da/user.admin.dave.tar.zst\n", stderr="")

    monkeypatch.setattr(restore_sources.shell, "privileged", privileged)
    assert restore_sources.take_from_inbox(str(dave), "directadmin") == f"{inbox.root}/da/user.admin.dave.tar.zst"
    assert calls == [("backup-inbox-take", ["user.admin.dave.tar.zst", "directadmin"])]

    monkeypatch.setattr(restore_sources.shell, "privileged", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout="", stderr="opanel-helper: user.admin.dave.tar.zst is still being uploaded"))
    with pytest.raises(restore_sources.RestoreSourceError, match="still being uploaded"):
        restore_sources.take_from_inbox(str(dave), "directadmin")


def test_a_restore_from_the_drop_folder_moves_it_first_and_keeps_it(inbox, monkeypatch):
    erin = inbox.make("erin-monday.tar.gz")
    moved = inbox.root / "backups" / "restore" / "erin-monday.tar.gz"
    order = []

    def take(ref, kind):
        order.append(("take", Path(ref).name, kind))
        moved.parent.mkdir(parents=True, exist_ok=True)
        os.replace(ref, moved)
        return str(moved)

    def restore(path, db, on_progress=None):
        order.append(("restore", path))
        return {"username": "erin", "websites": []}

    monkeypatch.setattr(restore_sources, "take_from_inbox", take)
    monkeypatch.setattr(maintenance.backup, "restore_user_backup", restore)
    monkeypatch.setattr(maintenance, "SessionLocal", lambda: SimpleNamespace(rollback=lambda: None, close=lambda: None))
    monkeypatch.setattr(maintenance, "log_action", lambda *a, **k: None)
    monkeypatch.setattr(maintenance, "_backup_jobs", {})
    job = maintenance._queue_backup_job(ADMIN, "user_restore_batch", "queued", count=1)
    maintenance._run_restore_job(job["job_id"], 1, LOCAL, [{"kind": "opanel", "ref": str(erin)}], False)
    assert order == [("take", "erin-monday.tar.gz", "opanel"), ("restore", str(moved))]
    assert maintenance._backup_jobs[job["job_id"]]["status"] == "done"
    assert moved.exists(), "moved, not copied: it stays like an upload"


def test_the_drop_folder_can_be_cleared_from_the_tab(inbox, monkeypatch):
    monkeypatch.setattr(maintenance, "log_action", lambda *a, **k: None)
    dave = inbox.make("user.admin.dave.tar.zst")
    assert maintenance.delete_restore_archive(str(dave), request=None, db=None,
                                              current_user=ADMIN) == {"deleted": "user.admin.dave.tar.zst"}
    assert not dave.exists()
    with pytest.raises(HTTPException) as exc:
        maintenance.delete_restore_archive(str(inbox.folder / "sub" / "x.tar.gz"), request=None, db=None,
                                           current_user=ADMIN)
    assert exc.value.status_code == 400


def test_the_helper_takes_a_plain_settled_file_and_hands_it_over_after_the_move():
    take = HELPER.split("backup_inbox_take() {", 1)[1].split("\n}\n", 1)[0]
    checks = ['[[ -f "$src" && ! -L "$src" ]]', 'stat -c %h -- "$src")" == "1"', "(( age >= 60 ))",
              'mv -T -n -- "$src" "$dest"', '[[ ! -e "$src" && -f "$dest" && ! -L "$dest" ]]',
              'chown -h -- opanel:opanel "$dest"']
    positions = [take.index(check) for check in checks]
    assert positions == sorted(positions), "checked, moved, checked again, and only then given away"
    assert 'install -d -m 2770 -o "$owner" -g opanel "$BACKUP_INBOX"' in HELPER
    assert 'BACKUP_INBOX="/home/admin/backups"' in HELPER
    assert restore_sources.INBOX_DIR == Path("/home/admin/backups")
    assert "backup-inbox-take)" in HELPER and "backup-inbox-ensure)" in HELPER


def test_the_drop_folder_is_made_on_install_and_on_update():
    for script in ("install.sh", "update.sh"):
        text = (PROJECT_ROOT / "installer" / script).read_text(encoding="utf-8")
        assert 'install -d -o "$inbox_owner" -g opanel -m 2770 /home/admin/backups' in text, script


def test_the_restore_tab_says_where_large_backups_go():
    assert "'/home/admin/backups')" in APP
    assert "['uploaded', 'da', 'inbox'].includes(chosen.location)" in APP
    assert "chosen.location === 'inbox' && <span className=\"badge restore-kind\"" in APP
    assert "restore_sources.take_from_inbox(ref, kind)" in inspect.getsource(maintenance._run_restore_job)
