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

    backup.prune_s3_backups(prefix="opanel", keep=2, name_contains="alice", **CREDS)

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
