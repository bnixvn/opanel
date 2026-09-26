"""Restore, DirectAdmin style: pick a source, pick the accounts, restore.

One list for OPanel and DirectAdmin archives, from this server, a saved Backup
Destination, or another server over SFTP/FTP/FTPS. These tests pin what the
list is built from, what a pick may point at, where a download may land, and
that another server's credentials go nowhere but memory.
"""
import ftplib
import inspect
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api import maintenance
from app.core.config import settings
from app.schemas.schemas import RestoreRemoteIn, RestoreRunIn, RestoreSourceIn
from app.services import backup, restore_sources

ADMIN = SimpleNamespace(id=1, role="admin")
KIND_OP, KIND_DA = restore_sources.KIND_OPANEL, restore_sources.KIND_DA


# --- what an archive is -------------------------------------------------------

@pytest.mark.parametrize("name, kind, account", [
    ("user.admin.alice.tar.zst", KIND_DA, "alice"),
    ("user.reseller1.bob.tar.gz", KIND_DA, "bob"),  # DirectAdmin can write .tar.gz too
    ("reseller.admin.res1.tar.zst", KIND_DA, "res1"),
    ("backup-carol.tar.bz2", KIND_DA, "carol"),  # any non-gz tar is DirectAdmin
    ("alice-monday.tar.gz", KIND_OP, "alice"),
    ("shop-a-manual-sunday.tar.gz", KIND_OP, "shop-a"),
    ("something.tar.gz", KIND_OP, ""),  # described from its manifest later
    ("notes.txt", None, None),
    ("alice-monday.tar.gz.part", None, None),  # a download in progress is never offered
])
def test_archive_kind_and_account(name, kind, account):
    assert restore_sources.archive_kind(name) == kind
    if kind:
        assert restore_sources.account_of(kind, name) == account


def test_a_folder_names_the_account_only_when_the_file_name_does_not():
    assert restore_sources.account_of(KIND_OP, "export.tar.gz", "dave") == "dave"
    assert restore_sources.account_of(KIND_OP, "alice-friday.tar.gz", "2026-09-27") == "alice"


# --- what a pick may point at ---------------------------------------------------

def _remote(path="/backups", protocol="sftp"):
    return restore_sources.Source("remote", remote=restore_sources.Remote(
        protocol=protocol, host="old.example.com", port=22, username="admin", password="pw", path=path))


@pytest.mark.parametrize("ref, ok", [
    ("/backups/user.admin.alice.tar.zst", True),
    ("/backups/2026-09-27/user.admin.alice.tar.zst", True),
    ("/backups/../etc/user.admin.alice.tar.zst", False),
    ("/etc/user.admin.alice.tar.zst", False),
    ("/backupsX/user.admin.alice.tar.zst", False),
])
def test_a_remote_pick_must_be_inside_the_folder_that_was_listed(ref, ok):
    source = _remote()
    if ok:
        assert restore_sources.check_ref(source, KIND_DA, ref) == ref
    else:
        with pytest.raises(ValueError):
            restore_sources.check_ref(source, KIND_DA, ref)


def test_a_relative_login_folder_keeps_picks_below_it():
    source = _remote(path="")
    assert restore_sources.check_ref(source, KIND_DA, "./user.admin.a.tar.zst") == "user.admin.a.tar.zst"
    with pytest.raises(ValueError):
        restore_sources.check_ref(source, KIND_DA, "../user.admin.a.tar.zst")
    with pytest.raises(ValueError):
        restore_sources.check_ref(source, KIND_DA, "/root/user.admin.a.tar.zst")


def test_a_pick_is_refused_when_its_kind_does_not_match_its_name():
    with pytest.raises(ValueError):
        restore_sources.check_ref(_remote(), KIND_OP, "/backups/user.admin.alice.tar.zst")


def test_a_control_character_never_reaches_an_ftp_command():
    # Over FTP, CR/LF in a path is a second command.
    with pytest.raises(ValueError):
        restore_sources.check_ref(_remote(protocol="ftp"), KIND_DA, "/backups/a.tar\r\nDELE x.tar")
    with pytest.raises(ValidationError):
        RestoreRemoteIn(host="h", path="/b\r\nDELE x")


def test_an_s3_pick_must_sit_under_the_destination_prefix():
    source = restore_sources.Source("target", s3=restore_sources.S3Bucket(
        endpoint="", region="us-east-1", bucket="b", access_key="a", secret_key="s", prefix="opanel"))
    assert restore_sources.check_ref(source, KIND_OP, "opanel/alice/alice-monday.tar.gz")
    with pytest.raises(ValueError):
        restore_sources.check_ref(source, KIND_OP, "other/alice-monday.tar.gz")


def test_a_local_pick_must_be_a_real_archive_in_the_panels_folders(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "backup_root", str(tmp_path / "backups"))
    monkeypatch.setattr(settings, "da_backup_dir", str(tmp_path / "da"))
    (tmp_path / "backups" / "users" / "alice").mkdir(parents=True)
    (tmp_path / "da").mkdir()
    own = tmp_path / "backups" / "users" / "alice" / "alice-monday.tar.gz"
    own.write_bytes(b"x")
    da = tmp_path / "da" / "user.admin.bob.tar.zst"
    da.write_bytes(b"x")
    stray = tmp_path / "elsewhere.tar.gz"
    stray.write_bytes(b"x")
    local = restore_sources.Source("local")

    assert restore_sources.check_ref(local, KIND_OP, str(own)) == str(own.resolve())
    assert restore_sources.check_ref(local, KIND_DA, str(da)) == str(da.resolve())
    with pytest.raises(FileNotFoundError):
        restore_sources.check_ref(local, KIND_OP, str(stray))


# --- where a download lands -----------------------------------------------------

def test_a_download_never_overwrites_an_archive_already_here(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "backup_root", str(tmp_path / "backups"))
    monkeypatch.setattr(settings, "da_backup_dir", str(tmp_path / "da"))
    first = restore_sources._destination(KIND_OP, "alice-monday.tar.gz")
    assert first.parent == backup._user_restore_dir().resolve()
    first.write_bytes(b"uploaded by someone")

    second = restore_sources._destination(KIND_OP, "alice-monday.tar.gz")
    assert second != first and second.name.startswith("alice-monday-") and second.name.endswith(".tar.gz")

    da = restore_sources._destination(KIND_DA, "user.admin.bob.tar.zst")
    assert da.parent == (tmp_path / "da").resolve()


def test_a_download_name_is_reduced_to_a_bare_file_name(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "da_backup_dir", str(tmp_path / "da"))
    target = restore_sources._destination(KIND_DA, "../../etc/user.admin.x.tar.zst")
    assert target.parent == (tmp_path / "da").resolve()
    with pytest.raises(ValueError):
        restore_sources._destination(KIND_DA, "passwd")


def test_a_download_that_does_not_fit_is_refused_before_it_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(restore_sources.shutil, "disk_usage", lambda _p: SimpleNamespace(free=1024 ** 3))
    with pytest.raises(restore_sources.RestoreSourceError, match="Not enough disk space"):
        restore_sources._ensure_room(tmp_path, 2 * 1024 ** 3, "big.tar.zst")
    restore_sources._ensure_room(tmp_path, 100 * 1024 ** 2, "small.tar.zst")


def test_a_failed_download_leaves_nothing_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "da_backup_dir", str(tmp_path / "da"))

    class _Ftp:
        def size(self, _p): return 10
        def retrbinary(self, _cmd, callback, blocksize):
            callback(b"half")
            raise ftplib.error_temp("426 Connection closed; transfer aborted")

    class _Ctx:
        def __enter__(self): return _Ftp()
        def __exit__(self, *a): return False

    monkeypatch.setattr(restore_sources, "_ftp", lambda _remote: _Ctx())
    with pytest.raises(ftplib.error_temp):
        restore_sources.fetch(_remote(protocol="ftp"), KIND_DA, "/backups/user.admin.a.tar.zst")
    assert list((tmp_path / "da").iterdir()) == []


# --- listing ---------------------------------------------------------------------

def test_listing_walks_one_level_of_folders_and_keeps_only_archives():
    tree = {
        "/b": [("user.admin.a.tar.zst", False, 5, "2026-09-26T00:00:00Z"), ("readme.txt", False, 1, ""),
               ("2026-09-27", True, 0, ""), (".cache", True, 0, "")],
        "/b/2026-09-27": [("user.admin.b.tar.zst", False, 7, "2026-09-27T00:00:00Z"), ("deeper", True, 0, "")],
    }
    rows = restore_sources._walk(lambda path: tree[path], "/b", "sftp")
    assert [(row["ref"], row["account"], row["kind"]) for row in rows] == [
        ("/b/2026-09-27/user.admin.b.tar.zst", "b", KIND_DA),
        ("/b/user.admin.a.tar.zst", "a", KIND_DA),
    ]


def test_ftp_listing_uses_mlsd_and_falls_back_to_nlst():
    class _Mlsd:
        def mlsd(self, path, facts):
            return iter([(".", {"type": "cdir"}), ("x", {"type": "dir"}),
                         ("user.admin.a.tar.zst", {"type": "file", "size": "12", "modify": "20260927031500"})])

    assert restore_sources._ftp_entries(_Mlsd(), "/b") == [
        ("x", True, 0, ""), ("user.admin.a.tar.zst", False, 12, "2026-09-27T03:15:00Z")]

    class _Old:
        def mlsd(self, path, facts):
            raise ftplib.error_perm("500 Unknown command")
        def nlst(self, path):
            return ["/b/user.admin.a.tar.zst", "/b/sub"]
        def size(self, path):
            if path.endswith("sub"):
                raise ftplib.error_perm("550 not a plain file")
            return 12
        def voidcmd(self, cmd):
            return "213 20260927031500"

    assert restore_sources._ftp_entries(_Old(), "/b") == [
        ("user.admin.a.tar.zst", False, 12, "2026-09-27T03:15:00Z"), ("sub", True, 0, "")]


def test_sftp_listing_tells_folders_from_files():
    class _Sftp:
        def listdir_attr(self, path):
            return [SimpleNamespace(filename="d", st_mode=stat.S_IFDIR | 0o755, st_size=0, st_mtime=0),
                    SimpleNamespace(filename="alice-monday.tar.gz", st_mode=stat.S_IFREG | 0o600,
                                    st_size=9, st_mtime=0),
                    SimpleNamespace(filename="link", st_mode=stat.S_IFLNK | 0o777, st_size=0, st_mtime=0)]

    assert [entry[:3] for entry in restore_sources._sftp_entries(_Sftp(), ".")] == [
        ("d", True, 0), ("alice-monday.tar.gz", False, 9)]


# --- SFTP host keys ------------------------------------------------------------------

class _Key:
    def __init__(self, blob): self.blob = blob
    def asbytes(self): return self.blob
    def get_name(self): return "ssh-ed25519"


def test_a_pinned_host_key_is_accepted_and_any_other_refused():
    """The pin is a fingerprint only, so paramiko always asks the policy. It
    used to refuse unconditionally -- every SFTP upload after the first."""
    pinned = backup._fingerprint(_Key(b"right"))
    policy = backup._PinnedHostKeyPolicy("ssh-ed25519", pinned)
    policy.missing_host_key(None, "h", _Key(b"right"))
    with pytest.raises(backup.SftpHostKeyMismatch):
        policy.missing_host_key(None, "h", _Key(b"wrong"))


# --- the API ---------------------------------------------------------------------------

def test_the_source_needs_what_it_names():
    with pytest.raises(ValidationError):
        RestoreSourceIn(source="target")
    with pytest.raises(ValidationError):
        RestoreSourceIn(source="remote")
    with pytest.raises(ValidationError):
        RestoreRemoteIn(host="bad host; rm -rf /")
    assert RestoreRemoteIn(host=" 203.0.113.9 ", protocol="ftps", port=21).host == "203.0.113.9"


@pytest.fixture
def runner(monkeypatch):
    submitted = []
    monkeypatch.setattr(maintenance._backup_job_executor, "submit",
                        lambda fn, *args: submitted.append(args))
    monkeypatch.setattr(maintenance, "log_action", lambda *a, **k: None)
    monkeypatch.setattr(maintenance, "_backup_jobs", {})
    monkeypatch.setattr(maintenance, "_da_import_jobs", {})
    return submitted


def _run(**payload):
    return maintenance.run_restore(RestoreRunIn(**payload), request=None, db=None, current_user=ADMIN)


def test_a_remote_restore_is_queued_without_writing_the_password_anywhere(runner):
    job = _run(source="remote", remote={"protocol": "sftp", "host": "old.example.com", "username": "admin",
                                        "password": "s3cret-pw", "path": "/backups"},
               items=[{"kind": "directadmin", "ref": "/backups/user.admin.alice.tar.zst"},
                      {"kind": "directadmin", "ref": "/backups/user.admin.alice.tar.zst"}])
    assert job["status"] == "queued"
    job_id, user_id, source, items, overwrite = runner[0]
    assert items == [{"kind": "directadmin", "ref": "/backups/user.admin.alice.tar.zst"}]  # de-duplicated
    assert source.remote.password == "s3cret-pw"  # handed to the worker in memory...
    assert "s3cret-pw" not in repr(maintenance._backup_jobs)  # ...and not into the job record


def test_a_pick_outside_the_listed_folder_is_refused(runner):
    with pytest.raises(HTTPException) as exc:
        _run(source="remote", remote={"host": "h", "password": "p", "path": "/backups"},
             items=[{"kind": "directadmin", "ref": "/etc/user.admin.x.tar.zst"}])
    assert exc.value.status_code == 400
    assert runner == []


def test_a_second_restore_waits_for_the_first(runner, monkeypatch):
    monkeypatch.setattr(maintenance, "_backup_jobs", {"j": {"kind": "user_restore_batch", "status": "running"}})
    with pytest.raises(HTTPException) as exc:
        _run(source="remote", remote={"host": "h", "password": "p", "path": "/b"},
             items=[{"kind": "directadmin", "ref": "/b/user.admin.x.tar.zst"}])
    assert exc.value.status_code == 409


def test_restore_routes_are_admin_only():
    for route in (maintenance.list_restore_source, maintenance.run_restore,
                  maintenance.upload_restore_archives, maintenance.delete_restore_archive):
        assert "ensure_role(current_user.role, Role.admin)" in inspect.getsource(route), route.__name__


def test_the_job_restores_each_kind_with_its_own_importer_and_drops_downloads():
    source = inspect.getsource(maintenance._run_restore_job)
    assert "backup.restore_user_backup(local_path" in source
    assert "da_import.import_da_backup(local_path, db, overwrite=overwrite)" in source
    assert "Path(fetched).unlink(missing_ok=True)" in source


def test_deleting_from_the_restore_tab_only_touches_uploads():
    """The panel's own account backups are deleted under Backup user, where the
    copy on a destination is offered too."""
    source = inspect.getsource(maintenance.delete_restore_archive)
    assert "backup.delete_user_restore_backup(ref)" in source
    assert "backup.delete_user_backup(" not in source
