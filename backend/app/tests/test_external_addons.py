"""Addons from a repository of their own, and the backup destinations they
bring (operator, 2026-11-16: a Google Drive backup addon "nằm ở gitlab độc
lập. Cần dùng thì chạy lệnh cài vào"). A stand-in addon, written to a
folder as `opanel addon install` would leave it, goes the whole way: loaded,
a destination made, signed in, tested, a schedule's archive sent and pruned,
its files listed and deleted, listed and fetched back for a restore, and its
copy removed with a local backup."""
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.core.database as database
from app.api import maintenance
from app.core.database import Base
from app.models.entities import BackupTarget, User
from app.schemas.schemas import BackupTargetCreate, BackupTargetUpdate
from app.services import addons, backup_scheduler, destinations, external_addons, restore_sources

ROOT = Path(__file__).resolve().parents[3]

DRIVER = '''
class Driver:
    kind = "fake"
    label = "Fake Drive"
    auth = "oauth_device"
    fields = [{"name": "folder", "label": "Folder", "type": "text", "required": True},
              {"name": "token", "label": "Token", "type": "password", "secret": True}]

    def __init__(self, panel):
        self.Error = panel.DestinationError
        self.files = {}

    def validate(self, fields, config):
        if not fields.get("folder"):
            raise self.Error("Name the folder")
        return {**config, "folder": fields["folder"]}

    def summary(self, config):
        return "folder " + config.get("folder", "")

    def connected(self, config):
        return bool(config.get("refresh_token"))

    def connect_start(self, config):
        config["device_code"] = "dev-1"
        return {"verification_url": "https://www.google.com/device", "user_code": "ABCD-EFGH",
                "expires_in": 1800, "interval": 5}

    def connect_poll(self, config):
        config.pop("device_code", None)
        config["refresh_token"] = "secret-refresh-token"
        return "done"

    def test(self, config):
        return "wrote and removed a probe"

    def upload(self, config, local_file, remote_name, on_progress=None):
        with open(local_file, "rb") as handle:
            self.files[remote_name] = handle.read()
        return config["folder"] + "/" + remote_name

    def listing(self, config):
        return [{"key": "id%d/%s" % (i, name), "name": name, "size": len(data), "modified": "2026-11-%02dT00:00:00" % (i + 1)}
                for i, (name, data) in enumerate(sorted(self.files.items()))]

    def download(self, config, key, destination, on_progress=None):
        with open(destination, "wb") as handle:
            handle.write(self.files[key.split("/", 1)[1]])

    def delete(self, config, key):
        self.files.pop(key.split("/", 1)[1], None)


def register(panel):
    global driver
    driver = Driver(panel)
    panel.register_destination(driver)
'''


@pytest.fixture
def addon(tmp_path, monkeypatch):
    root = tmp_path / "addons"
    (root / "fake" / "fakemod").mkdir(parents=True)
    (root / "fake" / "addon.json").write_text(json.dumps(
        {"id": "fake", "name": "Fake", "version": "1.2.3", "panel_api": 1, "module": "fakemod", "kinds": ["fake"]}))
    (root / "fake" / "fakemod" / "__init__.py").write_text(DRIVER)
    monkeypatch.setattr(external_addons, "ROOT", root)
    monkeypatch.setitem(external_addons.KNOWN, "fake", {"name": "Fake", "project": "bnix/addon-fake",
                                                        "summary": "s", "details": ["d"]})
    monkeypatch.setattr(external_addons, "_trusted", lambda path: True)   # no root on a test machine
    monkeypatch.setattr(external_addons, "_loaded", {})
    monkeypatch.setattr(destinations, "_drivers", {})
    monkeypatch.setattr(destinations, "_addon_of", {})
    monkeypatch.setitem(addons.ADDONS, "fake", external_addons.catalogue_entries()["fake"])
    enabled = {"fake": True}
    monkeypatch.setattr(addons, "is_enabled", lambda slug: enabled.get(slug, False))
    return SimpleNamespace(root=root, enabled=enabled)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(maintenance, "log_action", lambda *a, **k: None)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", factory)    # restore_sources opens its own
    session = factory()
    session.add(User(id=1, username="admin", email="a@example.test", role="admin", hashed_password="x", is_active=True))
    session.commit()
    yield session
    session.close()


def _admin(db):
    return db.get(User, 1)


def _make(db, folder="Panel backups"):
    payload = BackupTargetCreate(name="drive", kind="fake", options={"folder": folder})
    return maintenance.create_backup_target(payload, request=None, db=db, current_user=_admin(db))


def test_an_installed_addon_is_loaded_and_offers_its_kind(addon):
    assert external_addons.load_installed(addons.is_enabled) == ["fake"]
    assert [k["kind"] for k in destinations.kinds()] == ["sftp", "s3", "fake"]
    addon.enabled["fake"] = False
    assert [k["kind"] for k in destinations.kinds()] == ["sftp", "s3"], "an addon stopped or removed offers nothing"
    with pytest.raises(destinations.DestinationError):
        destinations.driver_for("fake")


def test_it_is_a_shell_only_addon_with_no_root_helper(addon):
    entry = addons.ADDONS["fake"]
    assert entry["cli_only"] and entry["kind"] == "external" and entry["version"] == "1.2.3"
    assert "fake" not in addons.helper_addon_ids(), "the root helper never hears of it"


def test_a_destination_of_the_kind_is_made_signed_in_tested_and_edited(addon, db):
    out = _make(db)
    assert (out.kind, out.kind_label, out.summary, out.connected) == ("fake", "Fake Drive", "folder Panel backups", False)
    assert out.options == {"folder": "Panel backups"}
    row = db.get(BackupTarget, out.id)
    assert row.config and "Panel" not in row.config, "kept encrypted"
    prompt = maintenance.connect_backup_target(out.id, request=None, db=db, current_user=_admin(db))
    assert prompt["user_code"] == "ABCD-EFGH" and prompt["interval"] == 5
    polled = maintenance.poll_backup_target_connect(out.id, db=db, current_user=_admin(db))
    assert polled["state"] == "done" and polled["destination"].connected
    assert "secret-refresh-token" not in json.dumps(polled["destination"].model_dump())
    tested = maintenance.test_backup_target(out.id, db=db, current_user=_admin(db))
    assert tested == {"ok": True, "message": "wrote and removed a probe"}
    edited = maintenance.update_backup_target(out.id, BackupTargetUpdate(name="drive", kind="fake", options={"folder": "Other"}),
                                              request=None, db=db, current_user=_admin(db))
    assert edited.summary == "folder Other" and edited.connected, "editing keeps the sign-in"


def test_a_schedule_sends_to_it_and_dated_archives_are_held_to_the_retention(addon, db, tmp_path):
    out = _make(db)
    schedule = SimpleNamespace(target_id=out.id, retention=2, name_style="date")
    for day in ("2026-11-01", "2026-11-02", "2026-11-03"):
        archive = tmp_path / f"bob-{day}.tar.gz"
        archive.write_bytes(day.encode())
        sent = backup_scheduler._upload_if_configured(db, schedule, str(archive), "bob")
        assert sent == f"drive:Panel backups/bob-{day}.tar.gz"
    (tmp_path / "bobby-2026-11-01.tar.gz").write_bytes(b"x")
    backup_scheduler._upload_if_configured(db, SimpleNamespace(target_id=out.id, retention=1, name_style="date"),
                                           str(tmp_path / "bobby-2026-11-01.tar.gz"), "bobby")
    names = [row["name"] for row in maintenance.list_backup_target_objects(out.id, db=db, current_user=_admin(db))["items"]]
    assert names == ["bob-2026-11-02.tar.gz", "bob-2026-11-03.tar.gz", "bobby-2026-11-01.tar.gz"], \
        "bob keeps two; bobby's prune never touches bob's and bob's never touched bobby's"


def test_its_files_are_listed_and_deleted_only_when_they_are_its_own(addon, db, tmp_path):
    out = _make(db)
    archive = tmp_path / "bob-monday.tar.gz"
    archive.write_bytes(b"archive")
    maintenance.upload_archive_to_target(db, out.id, str(archive))
    listed = maintenance.list_backup_target_objects(out.id, db=db, current_user=_admin(db))
    assert listed["bucket"] == "Fake Drive: folder Panel backups"
    key = listed["items"][0]["key"]
    with pytest.raises(HTTPException) as exc:
        maintenance.delete_backup_target_object(out.id, "someone-elses-id/x.tar.gz", request=None, db=db, current_user=_admin(db))
    assert exc.value.status_code == 404
    assert maintenance.delete_backup_target_object(out.id, key, request=None, db=db, current_user=_admin(db)) == {"deleted": key}
    assert maintenance.list_backup_target_objects(out.id, db=db, current_user=_admin(db))["items"] == []


def test_a_restore_lists_checks_and_fetches_from_it(addon, db, tmp_path, monkeypatch):
    out = _make(db)
    archive = tmp_path / "bob-monday.tar.gz"
    archive.write_bytes(b"archive")
    maintenance.upload_archive_to_target(db, out.id, str(archive))
    source, target = maintenance._restore_source(SimpleNamespace(source="target", target_id=out.id, remote=None), db)
    assert target.id == out.id and source.location == "addon"
    rows = restore_sources._list_archives(source)
    assert [(row["filename"], row["account"], row["kind"]) for row in rows] == [("bob-monday.tar.gz", "bob", "opanel")]
    ref = restore_sources.check_ref(source, "opanel", rows[0]["ref"])
    with pytest.raises(ValueError):
        restore_sources.check_ref(source, "opanel", "other-id/bob-monday.tar.gz")
    monkeypatch.setattr(restore_sources.backup, "_user_restore_dir", lambda: tmp_path / "restore")
    monkeypatch.setattr(restore_sources, "_ensure_room", lambda *a: None)
    path = restore_sources.fetch(source, "opanel", ref, rows[0]["size"])
    assert Path(path).read_bytes() == b"archive" and Path(path).parent == (tmp_path / "restore").resolve()


def test_deleting_a_local_backup_finds_and_removes_its_copy_there(addon, db, tmp_path):
    out = _make(db)
    archive = tmp_path / "bob-monday.tar.gz"
    archive.write_bytes(b"archive")
    maintenance.upload_archive_to_target(db, out.id, str(archive))
    found = maintenance.find_remote_copies("/var/backups/opanel/users/bob/bob-monday.tar.gz", db=db, current_user=_admin(db))
    assert found["items"] == [{"target": "drive", "bucket": "Fake Drive", "key": "bob-monday.tar.gz"}]
    removed = maintenance._delete_remote_copies(db, "bob/bob-monday.tar.gz")
    assert removed == ["drive:bob-monday.tar.gz"]


def test_the_addon_is_not_removed_while_its_destinations_are_left(addon, db, monkeypatch):
    _make(db)
    monkeypatch.setattr(addons, "status", lambda addon_id: {"busy": False, "installed": True})
    with pytest.raises(ValueError, match="Delete the Fake destinations first: drive"):
        addons.uninstall("fake", from_shell=True)


def test_a_file_the_panel_user_could_write_is_never_loaded(addon, monkeypatch):
    monkeypatch.setattr(external_addons, "_trusted", lambda path: path.name != "__init__.py")
    with pytest.raises(external_addons.AddonError, match="not root's alone"):
        external_addons.load("fake")
    assert "fake" not in destinations._drivers


@pytest.mark.parametrize("mode, uid, trusted", [(0o100644, 0, True), (0o100664, 0, False),
                                                (0o100646, 0, False), (0o100644, 1001, False),
                                                (0o120777, 0, False)])
def test_only_roots_files_writable_by_nobody_else_are_trusted(monkeypatch, tmp_path, mode, uid, trusted):
    monkeypatch.setattr(Path, "lstat", lambda self: SimpleNamespace(st_mode=mode, st_uid=uid))
    assert external_addons._trusted(tmp_path / "x") is trusted


def test_only_the_panels_own_list_names_an_addon_and_the_shell_knows_the_same():
    ctl = (ROOT / "installer" / "files" / "opanelctl").read_text(encoding="utf-8")
    body = ctl.split("external_addon_project() {")[1].split("\n}\n")[0]
    in_ctl = dict(re.findall(r'^\s+([a-z0-9-]+)\) echo "([^"]+)" ;;', body, re.M))
    assert in_ctl == {slug: entry["project"] for slug, entry in external_addons.KNOWN.items()}
    fetch = ctl.split("fetch_external_addon() {")[1].split("\n}\n")[0]
    assert "local py=(/usr/bin/python3 -I)" in fetch and ".venv" not in fetch, "root never runs the panel's Python"
    assert 'chown -R root:root "$dest.new"' in fetch and "-type l -delete" in fetch
    assert "curl -fsSL --max-time 30" in fetch, "GitLab's latest-release link is a redirect"


def test_the_page_offers_the_addon_kind_its_fields_and_the_sign_in_code():
    page = (ROOT / "frontend" / "src" / "pages" / "Backups.jsx").read_text(encoding="utf-8")
    assert "request('/maintenance/destination-kinds', { silent: true })" in page
    assert "destinationKinds.filter((kind) => !kind.builtin).map((kind) => <option key={kind.kind} value={kind.kind}>" in page
    assert "(addonKind.fields || []).map((field) =>" in page, "its form is the fields its driver declared"
    connect = page.split("async function connectTarget(target) {")[1].split("\n  }\n")[0]
    assert "/connect/poll" in connect and "connectFlow.current !== flow.startedAt" in connect
    assert "if (state === 'slow_down') wait += 5;" in connect, "Google's slow_down is honoured"
    assert "addonKind?.auth === 'oauth_device' && !data.connected) connectTarget(data)" in page, "connected straight after saving"
    assert "{connect.user_code}" in page and "connect.verification_url" in page


def test_the_kind_is_a_short_word_and_the_options_plain_values():
    with pytest.raises(ValueError):
        BackupTargetCreate(name="x1", kind="Drive!", options={})
    with pytest.raises(ValueError):
        BackupTargetCreate(name="x1", kind="fake", options={"a": {"nested": 1}})
    assert BackupTargetCreate(name="x1", kind="gdrive", options={"folder": "f"}).kind == "gdrive"
