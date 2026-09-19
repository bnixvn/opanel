"""The S3-compatible backup destination.

boto3 is stubbed rather than mocked at the HTTP layer: what matters here is the
client configuration opanel asks for, the keys it builds, and how it reports a
refusal, none of which need a real endpoint.
"""
from pathlib import Path

import pytest

from app.services import backup


class _FakeClient:
    """Records calls, and can be told to raise the ClientError shapes botocore
    produces for the failures admins actually hit."""

    def __init__(self, fail_with=None):
        self.calls = []
        self.fail_with = fail_with
        self.objects: dict[str, dict] = {}

    def _maybe_fail(self):
        if self.fail_with:
            raise self.fail_with

    def head_bucket(self, **kwargs):
        self.calls.append(("head_bucket", kwargs))
        self._maybe_fail()

    def put_object(self, **kwargs):
        self.calls.append(("put_object", kwargs))
        self._maybe_fail()

    def delete_object(self, **kwargs):
        self.calls.append(("delete_object", kwargs))
        self._maybe_fail()

    def delete_objects(self, **kwargs):
        self.calls.append(("delete_objects", kwargs))
        self._maybe_fail()

    def upload_file(self, filename, bucket, key):
        self.calls.append(("upload_file", {"filename": filename, "Bucket": bucket, "Key": key}))
        self._maybe_fail()

    def get_paginator(self, name):
        entries = [
            {"Key": key, "Size": row["size"], "LastModified": row["modified"]}
            for key, row in self.objects.items()
        ]
        client = self

        class _Paginator:
            def paginate(self, **kwargs):
                client.calls.append(("list_objects_v2", kwargs))
                prefix = kwargs.get("Prefix", "")
                return [{"Contents": [e for e in entries if e["Key"].startswith(prefix)]}]

        return _Paginator()


@pytest.fixture
def fake_s3(monkeypatch):
    holder = {}

    def factory(**kwargs):
        holder["config"] = kwargs
        return holder["client"]

    holder["client"] = _FakeClient()
    monkeypatch.setattr(backup, "_s3_client", factory)
    return holder


CREDS = dict(
    endpoint="https://s3.wasabisys.com",
    region="ap-northeast-1",
    bucket="opanel-backups",
    access_key="AK",
    secret_key="SK",
)


# --------------------------------------------------------------------------
# Key building
# --------------------------------------------------------------------------

@pytest.mark.parametrize("given,expected", [
    ("", ""),
    ("opanel", "opanel"),
    ("/opanel/backups/", "opanel/backups"),
    ("///", ""),
    ("a/b/c", "a/b/c"),
])
def test_prefix_is_normalised(given, expected):
    """S3 has no directories. A leading slash makes an object whose name starts
    with a slash, which every console then shows as an empty folder."""
    assert backup.s3_prefix_for(given) == expected


@pytest.mark.parametrize("bad", ["../etc", "a/../b", "back ups", "b$d", "x\ny"])
def test_a_prefix_cannot_escape_or_carry_junk(bad):
    with pytest.raises(ValueError):
        backup.s3_prefix_for(bad)


def test_key_joins_prefix_and_filename():
    assert backup._s3_key("opanel/backups", "site.tar.gz") == "opanel/backups/site.tar.gz"
    assert backup._s3_key("", "site.tar.gz") == "site.tar.gz"
    assert backup._s3_key("/opanel/", "site.tar.gz") == "opanel/site.tar.gz"


# --------------------------------------------------------------------------
# Upload
# --------------------------------------------------------------------------

def test_upload_sends_the_file_under_the_prefix(fake_s3, tmp_path):
    archive = tmp_path / "user-2026.tar.gz"
    archive.write_bytes(b"x" * 2048)

    result = backup.upload_to_s3(str(archive), prefix="opanel/backups", **CREDS)

    name, kwargs = fake_s3["client"].calls[0]
    assert name == "upload_file"
    assert kwargs["Bucket"] == "opanel-backups"
    assert kwargs["Key"] == "opanel/backups/user-2026.tar.gz"
    assert result["remote_file"] == "s3://opanel-backups/opanel/backups/user-2026.tar.gz"
    assert result["size"] == 2048


def test_upload_uses_upload_file_so_large_archives_go_multipart(fake_s3, tmp_path):
    """A single PUT caps at 5 GB and these archives pass that. upload_file goes
    through boto3's transfer manager, which splits automatically."""
    archive = tmp_path / "big.tar.gz"
    archive.write_bytes(b"x")

    backup.upload_to_s3(str(archive), **CREDS)

    assert [call[0] for call in fake_s3["client"].calls] == ["upload_file"]


def test_a_missing_archive_is_not_an_s3_problem(fake_s3, tmp_path):
    with pytest.raises(FileNotFoundError):
        backup.upload_to_s3(str(tmp_path / "gone.tar.gz"), **CREDS)


# --------------------------------------------------------------------------
# Connection test
# --------------------------------------------------------------------------

def test_the_test_writes_and_removes_an_object(fake_s3):
    """HeadBucket alone passes for a read-only key, which then fails at 2am on
    the first scheduled run."""
    backup.test_s3_target(prefix="opanel", **CREDS)

    names = [call[0] for call in fake_s3["client"].calls]
    assert names == ["head_bucket", "put_object", "delete_object"]

    put = dict(fake_s3["client"].calls[1][1])
    delete = dict(fake_s3["client"].calls[2][1])
    assert put["Key"].startswith("opanel/.opanel-write-test-")
    assert delete["Key"] == put["Key"]


# --------------------------------------------------------------------------
# Failures, phrased for an admin
# --------------------------------------------------------------------------

def _client_error(code, message="nope"):
    from botocore.exceptions import ClientError
    return ClientError({"Error": {"Code": code, "Message": message}}, "HeadBucket")


@pytest.mark.parametrize("code,expected", [
    ("NoSuchBucket", "does not exist"),
    ("AccessDenied", "Access denied"),
    ("InvalidAccessKeyId", "Access key not recognised"),
    ("SignatureDoesNotMatch", "Secret key does not match"),
    ("PermanentRedirect", "Wrong region"),
    ("AuthorizationHeaderMalformed", "Wrong region"),
])
def test_known_refusals_say_what_to_change(monkeypatch, code, expected):
    client = _FakeClient(fail_with=_client_error(code))
    monkeypatch.setattr(backup, "_s3_client", lambda **kwargs: client)

    with pytest.raises(backup.S3Error) as exc:
        backup.test_s3_target(**CREDS)

    assert expected in str(exc.value)


def test_an_unreachable_endpoint_says_so(monkeypatch):
    from botocore.exceptions import EndpointConnectionError

    client = _FakeClient(fail_with=EndpointConnectionError(endpoint_url="https://wrong.example"))
    monkeypatch.setattr(backup, "_s3_client", lambda **kwargs: client)

    with pytest.raises(backup.S3Error, match="Could not reach the S3 endpoint"):
        backup.test_s3_target(**CREDS)


def test_an_unknown_code_is_passed_through_not_swallowed(monkeypatch):
    client = _FakeClient(fail_with=_client_error("SlowDown", "Reduce your request rate"))
    monkeypatch.setattr(backup, "_s3_client", lambda **kwargs: client)

    with pytest.raises(backup.S3Error) as exc:
        backup.test_s3_target(**CREDS)

    assert "SlowDown" in str(exc.value)
    assert "Reduce your request rate" in str(exc.value)


# --------------------------------------------------------------------------
# Retention at the far end
# --------------------------------------------------------------------------

def _seed(client, names):
    for index, name in enumerate(names):
        client.objects[name] = {"size": 10, "modified": index}


def test_prune_keeps_the_newest_and_deletes_the_rest(fake_s3):
    _seed(fake_s3["client"], [f"opanel/alice-{n}.tar.gz" for n in range(10)])

    removed = backup.prune_s3_backups(prefix="opanel", keep=3, **CREDS)

    assert removed == 7
    deleted = [call for call in fake_s3["client"].calls if call[0] == "delete_objects"][0][1]
    kept = {f"opanel/alice-{n}.tar.gz" for n in (9, 8, 7)}
    gone = {row["Key"] for row in deleted["Delete"]["Objects"]}
    assert kept.isdisjoint(gone)
    assert len(gone) == 7


def test_prune_is_scoped_to_one_account(fake_s3):
    """Two users sharing a bucket must not delete each other's archives."""
    _seed(fake_s3["client"], [f"opanel/alice-{n}.tar.gz" for n in range(5)]
          + [f"opanel/bob-{n}.tar.gz" for n in range(5)])

    backup.prune_s3_backups(prefix="opanel", keep=2, name_prefix="alice", **CREDS)

    deleted = [call for call in fake_s3["client"].calls if call[0] == "delete_objects"][0][1]
    gone = {row["Key"] for row in deleted["Delete"]["Objects"]}
    assert all("alice" in key for key in gone)
    assert len(gone) == 3


def test_prune_does_nothing_when_there_is_nothing_to_drop(fake_s3):
    _seed(fake_s3["client"], ["opanel/alice-1.tar.gz"])

    assert backup.prune_s3_backups(prefix="opanel", keep=7, **CREDS) == 0
    assert not [call for call in fake_s3["client"].calls if call[0] == "delete_objects"]


def test_prune_refuses_to_keep_nothing(fake_s3):
    _seed(fake_s3["client"], ["opanel/alice-1.tar.gz"])

    assert backup.prune_s3_backups(prefix="opanel", keep=0, **CREDS) == 0
    assert fake_s3["client"].calls == []


# --------------------------------------------------------------------------
# Client configuration
# --------------------------------------------------------------------------

def test_client_asks_for_v4_signing_and_the_chosen_addressing(monkeypatch):
    captured = {}

    class _Boto:
        @staticmethod
        def client(service, **kwargs):
            captured.update(kwargs)
            captured["service"] = service
            return _FakeClient()

    import sys
    monkeypatch.setitem(sys.modules, "boto3", _Boto)

    backup._s3_client(
        endpoint="minio.local:9000", region="us-east-1",
        access_key="AK", secret_key="SK", use_path_style=True,
    )

    assert captured["service"] == "s3"
    # Bare host gets a scheme, or botocore rejects it.
    assert captured["endpoint_url"] == "https://minio.local:9000"
    config = captured["config"]
    assert config.signature_version == "s3v4"
    assert config.s3["addressing_style"] == "path"


def test_no_endpoint_means_aws(monkeypatch):
    captured = {}

    class _Boto:
        @staticmethod
        def client(service, **kwargs):
            captured.update(kwargs)
            return _FakeClient()

    import sys
    monkeypatch.setitem(sys.modules, "boto3", _Boto)

    backup._s3_client(endpoint="", region="eu-west-1", access_key="AK",
                      secret_key="SK", use_path_style=False)

    assert captured["endpoint_url"] is None
    assert captured["config"].s3["addressing_style"] == "auto"


def test_empty_credentials_are_refused_before_any_call():
    with pytest.raises(ValueError, match="access key and secret key"):
        backup._s3_client(endpoint="", region="us-east-1", access_key="",
                          secret_key="SK", use_path_style=False)


# --------------------------------------------------------------------------
# API dispatch: one upload path, two kinds
# --------------------------------------------------------------------------

class _Target:
    def __init__(self, **kw):
        self.id = kw.pop("id", 1)
        self.name = kw.pop("name", "dest")
        self.kind = kw.pop("kind", "sftp")
        self.is_active = kw.pop("is_active", True)
        self.remote_path = kw.pop("remote_path", "opanel")
        self.s3_endpoint = kw.pop("s3_endpoint", "")
        self.s3_region = kw.pop("s3_region", "us-east-1")
        self.s3_bucket = kw.pop("s3_bucket", "opanel-backups")
        self.s3_access_key = kw.pop("s3_access_key", "AK")
        self.s3_secret_key = kw.pop("s3_secret_key", "enc")
        self.s3_use_path_style = kw.pop("s3_use_path_style", False)
        self.host = kw.pop("host", "backup.example.test")
        self.port = 22
        self.username = "bk"
        self.password = "enc"
        self.private_key = None
        self.host_key_type = None
        self.host_key_fingerprint = None
        for key, value in kw.items():
            setattr(self, key, value)


class _DB:
    def __init__(self, target):
        self._target = target

    def query(self, _model):
        return self

    def filter(self, *_):
        return self

    def first(self):
        return self._target

    def commit(self):
        pass


def _patch_api(monkeypatch, target, **overrides):
    from app.api import maintenance

    monkeypatch.setattr(maintenance, "decrypt", lambda value: "SECRET")
    for name, value in overrides.items():
        monkeypatch.setattr(maintenance.backup, name, value)
    return maintenance, _DB(target)


def test_an_s3_target_takes_the_s3_path(monkeypatch, tmp_path):
    seen = {}

    def fake_upload(archive, **kwargs):
        seen.update(kwargs)
        seen["archive"] = archive
        return {"remote_file": "s3://opanel-backups/opanel/x.tar.gz"}

    maintenance, db = _patch_api(monkeypatch, _Target(kind="s3"), upload_to_s3=fake_upload)

    name, remote = maintenance.upload_archive_to_target(db, 1, str(tmp_path / "x.tar.gz"))

    assert remote == "s3://opanel-backups/opanel/x.tar.gz"
    assert seen["bucket"] == "opanel-backups"
    assert seen["prefix"] == "opanel"
    # The stored secret is decrypted before it reaches the transport.
    assert seen["secret_key"] == "SECRET"


def test_an_sftp_target_still_takes_the_sftp_path(monkeypatch, tmp_path):
    called = {}

    def fake_upload(archive, **kwargs):
        called["host"] = kwargs["host"]
        return {"remote_file": "/backups/opanel/x.tar.gz"}

    maintenance, db = _patch_api(monkeypatch, _Target(kind="sftp"), upload_to_sftp=fake_upload)

    _, remote = maintenance.upload_archive_to_target(db, 1, str(tmp_path / "x.tar.gz"))

    assert called["host"] == "backup.example.test"
    assert remote == "/backups/opanel/x.tar.gz"


def test_a_target_with_no_kind_is_treated_as_sftp(monkeypatch, tmp_path):
    """Rows written before 0027 read back with kind defaulted, but a row
    restored from an older dump could still carry an empty string."""
    called = {}

    def fake_sftp(archive, **kwargs):
        called["used"] = "sftp"
        return {"remote_file": "/x"}

    maintenance, db = _patch_api(monkeypatch, _Target(kind=""), upload_to_sftp=fake_sftp)

    maintenance.upload_archive_to_target(db, 1, str(tmp_path / "x.tar.gz"))

    assert called["used"] == "sftp"


def test_an_s3_refusal_surfaces_as_a_gateway_error(monkeypatch, tmp_path):
    from fastapi import HTTPException

    def boom(archive, **kwargs):
        raise backup.S3Error("Bucket 'opanel-backups' does not exist.")

    maintenance, db = _patch_api(monkeypatch, _Target(kind="s3"), upload_to_s3=boom)

    with pytest.raises(HTTPException) as exc:
        maintenance.upload_archive_to_target(db, 1, str(tmp_path / "x.tar.gz"))

    assert exc.value.status_code == 502
    assert "does not exist" in exc.value.detail


def test_the_secret_is_never_returned_to_the_browser():
    from app.schemas.schemas import BackupTargetOut

    assert "s3_secret_key" not in BackupTargetOut.model_fields
    assert "password" not in BackupTargetOut.model_fields
    assert "private_key" not in BackupTargetOut.model_fields


@pytest.mark.parametrize("prefix", ["../../etc", "a/../b", "back ups", "b$d"])
def test_a_bad_s3_prefix_is_refused_at_save_not_at_backup_time(prefix):
    """Caught live: the API accepted ../../etc and would only have failed when
    the scheduled backup ran, which is the worst moment to find out."""
    from app.schemas.schemas import BackupTargetCreate

    with pytest.raises(ValueError):
        BackupTargetCreate(
            name="bad-prefix", kind="s3", remote_path=prefix,
            s3_bucket="opanel-backups", s3_access_key="AK", s3_secret_key="SK",
        )


def test_an_sftp_target_still_takes_an_absolute_remote_path():
    """The S3 prefix rules must not leak onto the other kind."""
    from app.schemas.schemas import BackupTargetCreate

    target = BackupTargetCreate(
        name="offsite", kind="sftp", host="backup.example.test",
        username="bk", password="pw", remote_path="/backups/opanel",
    )

    assert target.remote_path == "/backups/opanel"


# --------------------------------------------------------------------------
# Deleting from the bucket. Without this the panel could send a backup to S3
# and then never list it or remove it -- retention on a scheduled run was the
# only way anything ever left.
# --------------------------------------------------------------------------

def test_delete_removes_one_object(fake_s3):
    key = backup.delete_s3_object(key="opanel/alice-1.tar.gz", prefix="opanel", **CREDS)

    assert key == "opanel/alice-1.tar.gz"
    name, kwargs = fake_s3["client"].calls[0]
    assert name == "delete_object"
    assert kwargs["Key"] == "opanel/alice-1.tar.gz"
    assert kwargs["Bucket"] == "opanel-backups"


@pytest.mark.parametrize("key", [
    "somewhere-else/important.tar.gz",
    "opanel-other/x.tar.gz",
    "/etc/passwd",
    "opanel/../secrets/x",
    "",
])
def test_delete_refuses_anything_outside_the_prefix(fake_s3, key):
    """The key comes from the browser. A bucket usually holds more than opanel's
    backups, and a mistyped key must not be able to reach it."""
    with pytest.raises(ValueError):
        backup.delete_s3_object(key=key, prefix="opanel", **CREDS)

    assert fake_s3["client"].calls == []


def test_delete_with_no_prefix_still_rejects_traversal(fake_s3):
    with pytest.raises(ValueError):
        backup.delete_s3_object(key="a/../../b", prefix="", **CREDS)


def test_delete_accepts_a_leading_slash_from_the_browser(fake_s3):
    key = backup.delete_s3_object(key="/opanel/alice-1.tar.gz", prefix="opanel", **CREDS)

    assert key == "opanel/alice-1.tar.gz"


def test_the_panel_exposes_list_and_delete_for_a_destination():
    """The gap that started this: uploads worked and nothing could be removed."""
    from app.api import maintenance

    paths = {route.path for route in maintenance.router.routes}
    assert "/maintenance/backup-targets/{target_id}/objects" in paths

    methods = {
        method
        for route in maintenance.router.routes
        if route.path == "/maintenance/backup-targets/{target_id}/objects"
        for method in route.methods
    }
    assert {"GET", "DELETE"} <= methods


# --------------------------------------------------------------------------
# A backup that went to S3 exists twice. Deleting the local copy on its own
# left the offsite one behind, with nothing in the panel mentioning it --
# reported as "the panel deleted it but S3 still has it".
# --------------------------------------------------------------------------

def test_delete_endpoints_take_an_also_remote_flag():
    import inspect
    from app.api import maintenance

    for fn in (maintenance.delete_user_backup, maintenance.delete_backup):
        params = inspect.signature(fn).parameters
        assert "also_remote" in params, fn.__name__
        # On by default. Backups rotate daily against a fixed retention, so a
        # copy the panel cannot remove is one that accumulates until the bucket
        # is full -- the operator's reason, and the stronger one.
        assert params["also_remote"].default is True, fn.__name__


def test_the_panel_can_report_where_the_offsite_copies_are():
    """The confirm dialog names them before asking, so nobody deletes an
    offsite backup without seeing what it is."""
    from app.api import maintenance

    paths = {route.path for route in maintenance.router.routes}
    assert "/maintenance/backup-remote-copies" in paths


def test_remote_copies_are_matched_by_filename_under_each_prefix(monkeypatch):
    from app.api import maintenance

    class _Target:
        id, name, kind, is_active = 1, "wasabi", "s3", True
        remote_path = "opanel"
        s3_endpoint, s3_region, s3_bucket = "", "us-east-1", "opanel-backups"
        s3_access_key, s3_secret_key, s3_use_path_style = "AK", "enc", False

    class _Q:
        def __init__(self, rows): self._rows = rows
        def filter(self, *a): return self
        def all(self): return self._rows

    class _DB:
        def query(self, _m): return _Q([_Target()])

    monkeypatch.setattr(maintenance, "decrypt", lambda v: "SECRET")
    monkeypatch.setattr(maintenance.backup, "list_s3_backups", lambda **kw: [
        {"key": "opanel/alice-20260917.tar.gz", "size": 1, "modified": 1},
        {"key": "opanel/bob-20260917.tar.gz", "size": 1, "modified": 2},
    ])

    found = maintenance._remote_copies(_DB(), "alice-20260917.tar.gz")

    assert [item["key"] for item in found] == ["opanel/alice-20260917.tar.gz"]


def test_an_unreachable_destination_does_not_block_the_local_delete(monkeypatch):
    """The local file is the thing being deleted. A destination that is down
    must not turn that into a failed request."""
    from app.api import maintenance

    class _Target:
        id, name, kind, is_active = 1, "down", "s3", True
        remote_path = "opanel"
        s3_endpoint, s3_region, s3_bucket = "", "us-east-1", "b"
        s3_access_key, s3_secret_key, s3_use_path_style = "AK", "enc", False

    class _Q:
        def filter(self, *a): return self
        def all(self): return [_Target()]

    class _DB:
        def query(self, _m): return _Q()

    monkeypatch.setattr(maintenance, "decrypt", lambda v: "SECRET")

    def boom(**kwargs):
        raise backup.S3Error("Could not reach the S3 endpoint.")

    monkeypatch.setattr(maintenance.backup, "list_s3_backups", boom)

    assert maintenance._remote_copies(_DB(), "alice.tar.gz") == []


# --------------------------------------------------------------------------
# Weekly rotation, DirectAdmin style: seven slots per account, each overwritten
# a week later. Bounded by construction -- the prune can fail and nothing
# accumulates, because next Monday's run writes over last Monday's object.
# --------------------------------------------------------------------------

def test_weekday_slot_covers_the_week():
    from datetime import datetime

    names = [backup.weekday_slot(datetime(2026, 9, 14 + n)) for n in range(7)]

    assert names == ["monday", "tuesday", "wednesday", "thursday",
                     "friday", "saturday", "sunday"]
    assert len(set(names)) == 7


def test_the_same_weekday_next_week_lands_on_the_same_slot():
    from datetime import datetime

    assert backup.weekday_slot(datetime(2026, 9, 14)) == backup.weekday_slot(datetime(2026, 9, 21))


def test_the_scheduler_names_the_archive_for_the_account_and_the_day():
    import inspect
    from app.services import backup_scheduler

    source = inspect.getsource(backup_scheduler.run_due_schedules)

    assert 'filename=f"{user.username}-{slot}.tar.gz"' in source
    assert "backup.weekday_slot(now)" in source


def test_a_manual_backup_rotates_on_its_own_slots():
    """A timestamp never repeats, so nothing overwrote it and nothing removed
    it -- a run of manual backups filled the destination and stayed there. It
    rotates on seven slots of its own, clear of the scheduled slot for the
    same day."""
    import inspect

    source = inspect.getsource(backup.create_user_backup)

    assert "manual_slot_filename(user.username)" in source
    assert "{stamp}" not in source
    assert "if filename:" in source


def test_a_manual_slot_never_collides_with_a_scheduled_one():
    from datetime import datetime

    scheduled = {f"acme-{slot}.tar.gz" for slot in backup.WEEKDAY_SLOTS}
    manual = {backup.manual_slot_filename("acme", datetime(2026, 9, 14 + n)) for n in range(7)}

    assert len(manual) == 7
    assert not (manual & scheduled)
    assert backup.rotation_filenames("acme") == scheduled | manual


def test_a_manual_slot_is_recognised_by_name():
    assert backup.is_manual_slot("acme-manual-monday.tar.gz")
    assert backup.is_manual_slot("bk122/acme/acme-manual-monday.tar.gz")
    assert not backup.is_manual_slot("acme-monday.tar.gz")
    assert not backup.is_manual_slot("user-acme-20260919171122.tar.gz")
    assert not backup.is_manual_slot("")


def test_a_rotation_filename_has_to_be_one_of_the_slots(tmp_path, monkeypatch):
    class _User:
        username = "acme"

    monkeypatch.setattr(backup, "_user_backup_dir", lambda name: tmp_path)

    for bad in ("../../etc/passwd.tar.gz", "acme-funday.tar.gz",
                "other-monday.tar.gz", "monday.tar.gz"):
        with pytest.raises(ValueError, match="Invalid rotation filename"):
            backup.create_user_backup(_User(), None, filename=bad)


def test_each_account_gets_its_own_folder_on_the_destination():
    from app.services import backup_scheduler

    class _T:
        remote_path = "opanel/backups"

    assert backup_scheduler._account_prefix(_T(), "acme") == "opanel/backups/acme"

    class _Bare:
        remote_path = ""

    assert backup_scheduler._account_prefix(_Bare(), "acme") == "acme"


def test_prune_scoping_is_anchored_not_a_substring(fake_s3):
    """A schedule for "acme" used to treat every user-acme2-*.tar.gz as its own
    and delete another account's backups."""
    for name in [f"user-acme-2026091{n}.tar.gz" for n in range(4)]:
        fake_s3["client"].objects[f"opanel/{name}"] = {"size": 1, "modified": name}
    for name in [f"user-acme2-2026091{n}.tar.gz" for n in range(4)]:
        fake_s3["client"].objects[f"opanel/{name}"] = {"size": 1, "modified": name}

    backup.prune_s3_backups(prefix="opanel", keep=1, name_prefix="user-acme-", **CREDS)

    deleted = [c for c in fake_s3["client"].calls if c[0] == "delete_objects"][0][1]
    gone = {row["Key"] for row in deleted["Delete"]["Objects"]}
    assert all("user-acme-" in key for key in gone)
    assert not any("acme2" in key for key in gone)


@pytest.mark.parametrize("local,expected", [
    ("/var/backups/opanel/users/acme/acme-monday.tar.gz", "acme/acme-monday.tar.gz"),
    ("/var/backups/opanel/users/acme2/acme2-monday.tar.gz", "acme2/acme2-monday.tar.gz"),
    ("/var/backups/opanel/site.com/site.com-20260917.tar.gz", "site.com-20260917.tar.gz"),
])
def test_a_remote_copy_is_identified_by_its_path(local, expected):
    """Deleting one account's Monday copy must not reach another account's."""
    from app.api.maintenance import _remote_relative_key

    assert _remote_relative_key(local) == expected


class _RotUser:
    id = 1
    username = "acme"
    email = "acme@example.test"
    hashed_password = ""
    role = "user"
    is_active = True
    website_limit = 1
    storage_limit_mb = 0


class _RotQuery:
    def filter(self, *a, **k):
        return self

    def order_by(self, *a, **k):
        return self

    def all(self):
        return []

    def first(self):
        return None


class _RotDB:
    def query(self, *a, **k):
        return _RotQuery()


def test_a_failed_run_leaves_last_weeks_copy_intact(tmp_path, monkeypatch):
    """Overwriting in place is only safe if a half-finished archive never lands
    on the slot: opening the destination directly truncates it up front, so a
    run killed by a full disk would destroy the good copy and replace it with a
    stub."""
    monkeypatch.setattr(backup, "_user_backup_dir", lambda name: tmp_path)
    slot = tmp_path / "acme-monday.tar.gz"
    slot.write_bytes(b"last week's good archive")

    def exploding_open(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(backup.tarfile, "open", exploding_open)

    with pytest.raises(OSError):
        backup.create_user_backup(_RotUser(), _RotDB(), filename="acme-monday.tar.gz")

    assert slot.read_bytes() == b"last week's good archive"


def test_a_good_run_replaces_the_slot(tmp_path, monkeypatch):
    import tarfile as _tarfile

    monkeypatch.setattr(backup, "_user_backup_dir", lambda name: tmp_path)
    slot = tmp_path / "acme-monday.tar.gz"
    slot.write_bytes(b"last week's good archive")

    written = backup.create_user_backup(_RotUser(), _RotDB(), filename="acme-monday.tar.gz")

    assert written == str(slot)
    assert _tarfile.is_tarfile(slot)
    with _tarfile.open(slot) as tar:
        assert backup.BACKUP_MANIFEST in tar.getnames()


def test_no_temporary_directory_is_left_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "_user_backup_dir", lambda name: tmp_path)

    backup.create_user_backup(_RotUser(), _RotDB(), filename="acme-monday.tar.gz")

    assert [p.name for p in tmp_path.iterdir()] == ["acme-monday.tar.gz"]


def test_backups_are_listed_newest_first_by_age_not_by_name(tmp_path, monkeypatch):
    """Weekday names carry no ordering, so the list has to go by mtime."""
    import os as _os

    monkeypatch.setattr(backup, "_user_backup_dir", lambda name: tmp_path)
    monkeypatch.setattr(backup.settings, "command_dry_run", False)

    ages = {
        "acme-monday.tar.gz": 5,
        "acme-tuesday.tar.gz": 4,
        "acme-wednesday.tar.gz": 3,
        "acme-friday.tar.gz": 1,
    }
    for name, days in ages.items():
        path = tmp_path / name
        path.write_bytes(b"x")
        stamp = 1_760_000_000 - days * 86_400
        _os.utime(path, (stamp, stamp))

    listed = [p.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for p in backup.list_user_backups("acme")]

    assert listed == ["acme-friday.tar.gz", "acme-wednesday.tar.gz",
                      "acme-tuesday.tar.gz", "acme-monday.tar.gz"]


def test_the_prune_never_deletes_the_backup_just_taken(tmp_path, monkeypatch):
    """The regression this guards: on a box with the old timestamped archives,
    reverse-alphabetical order put every "user-acme-<stamp>" ahead of every
    rotation file, so the nightly prune deleted the archive it had just
    written and kept seven stale ones."""
    import os as _os

    monkeypatch.setattr(backup, "_user_backup_dir", lambda name: tmp_path)
    monkeypatch.setattr(backup.settings, "command_dry_run", False)

    for n in range(7):                       # last week's legacy archives
        path = tmp_path / f"user-acme-2026091{n}.tar.gz"
        path.write_bytes(b"x")
        stamp = 1_760_000_000 - (10 - n) * 86_400
        _os.utime(path, (stamp, stamp))

    fresh = tmp_path / "acme-monday.tar.gz"  # tonight's rotation slot
    fresh.write_bytes(b"x")
    _os.utime(fresh, (1_760_000_000, 1_760_000_000))

    backup.prune_user_backups("acme", 7)

    assert fresh.exists()
    assert len(list(tmp_path.glob("*.tar.gz"))) == 7


def test_retention_leaves_manual_slots_alone(tmp_path, monkeypatch):
    """A schedule's budget must not evict the copy somebody took by hand right
    before a risky change -- nor the other way round."""
    import os as _os

    monkeypatch.setattr(backup, "_user_backup_dir", lambda name: tmp_path)
    monkeypatch.setattr(backup.settings, "command_dry_run", False)

    names = [f"acme-{slot}.tar.gz" for slot in backup.WEEKDAY_SLOTS]
    names += [f"acme-manual-{slot}.tar.gz" for slot in ("monday", "tuesday")]
    for index, name in enumerate(names):
        path = tmp_path / name
        path.write_bytes(b"x")
        stamp = 1_760_000_000 - index * 3600
        _os.utime(path, (stamp, stamp))

    backup.prune_user_backups("acme", 3)

    left = sorted(p.name for p in tmp_path.glob("*.tar.gz"))
    assert "acme-manual-monday.tar.gz" in left
    assert "acme-manual-tuesday.tar.gz" in left
    assert len([n for n in left if "-manual-" not in n]) == 3


def test_the_s3_prune_leaves_manual_slots_alone(fake_s3):
    for slot in backup.WEEKDAY_SLOTS:
        fake_s3["client"].objects[f"opanel/acme/acme-{slot}.tar.gz"] = {"size": 1, "modified": slot}
    fake_s3["client"].objects["opanel/acme/acme-manual-monday.tar.gz"] = {"size": 1, "modified": "z"}

    backup.prune_s3_backups(prefix="opanel/acme", keep=2, **CREDS)

    deleted = [c for c in fake_s3["client"].calls if c[0] == "delete_objects"]
    gone = {row["Key"] for row in deleted[0][1]["Delete"]["Objects"]} if deleted else set()
    assert not any("-manual-" in key for key in gone)
