"""An archive is untrusted input, not a source of authority.

``RESTORABLE_BACKUP_KINDS`` deliberately accepts ``bpanel_user`` -- a foreign
panel's export -- and ``_fetch_remote_archive`` pulls archives out of S3, so the
bytes a restore consumes are third-party data. The operator authorises "restore
this account"; they are not shown, and do not approve, the role the manifest
asks for or the WAF directives it carries.

The counter-example lives in the same repository: ``da_import._process_archive``
hardcodes ``role="end_user"`` and generates the password server-side. Two
importers of the same class disagreeing is what made the restore path a defect
rather than a design choice.
"""
from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.core.permissions import Role
from app.core.security import hash_password
from app.models.entities import DatabaseAccount, User, Website
from app.services import backup


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def backup_root(tmp_path, monkeypatch):
    """user_backup_path only accepts paths under settings.backup_root."""
    monkeypatch.setattr(backup.settings, "backup_root", str(tmp_path))
    return tmp_path


def _archive(tmp_path: Path, manifest: dict, name: str = "acct-monday.tar.gz") -> Path:
    """Build a minimal full-user archive with manifest.json as the first member."""
    path = tmp_path / name
    payload = json.dumps(manifest).encode("utf-8")
    with tarfile.open(path, "w:gz") as tar:
        info = tarfile.TarInfo(backup.BACKUP_MANIFEST)
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))
    return path


def _manifest(**overrides) -> dict:
    manifest = {
        "kind": "opanel_user",
        "user": {"username": "migrated1", "email": "m@example.test"},
        "websites": [],
        "databases": [],
    }
    manifest.update(overrides)
    return manifest


# --------------------------------------------------------------------------
# role and credential
# --------------------------------------------------------------------------
def test_archive_cannot_choose_the_restored_account_role(db, tmp_path, monkeypatch):
    monkeypatch.setattr(backup.site_users, "ensure_panel_user", lambda *a, **k: "op_x")
    archive = _archive(
        tmp_path,
        _manifest(
            user={
                "username": "migrated1",
                "email": "m@example.test",
                "role": "admin",
                "hashed_password": hash_password("attacker-chosen"),
            }
        ),
    )
    backup.restore_user_backup(str(archive), db)

    created = db.query(User).filter(User.username == "migrated1").one()
    assert created.role == Role.end_user.value, (
        "an archive naming role 'admin' must not create a panel administrator"
    )


def test_archive_cannot_choose_the_restored_account_password(db, tmp_path, monkeypatch):
    monkeypatch.setattr(backup.site_users, "ensure_panel_user", lambda *a, **k: "op_x")
    chosen = hash_password("attacker-chosen")
    archive = _archive(
        tmp_path,
        _manifest(
            user={"username": "migrated1", "email": "m@example.test", "hashed_password": chosen}
        ),
    )
    backup.restore_user_backup(str(archive), db)

    created = db.query(User).filter(User.username == "migrated1").one()
    assert created.hashed_password != chosen, (
        "the stored credential must not be the one the archive supplied"
    )


def test_legacy_super_admin_alias_is_also_refused(db, tmp_path, monkeypatch):
    """normalize_role mapped 'super_admin' to Role.admin too."""
    monkeypatch.setattr(backup.site_users, "ensure_panel_user", lambda *a, **k: "op_x")
    archive = _archive(
        tmp_path,
        _manifest(user={"username": "migrated1", "email": "m@e.test", "role": "super_admin"}),
    )
    backup.restore_user_backup(str(archive), db)
    assert db.query(User).filter(User.username == "migrated1").one().role == Role.end_user.value


# --------------------------------------------------------------------------
# domain ownership
# --------------------------------------------------------------------------
def test_restore_refuses_a_domain_owned_by_another_account(db, tmp_path, monkeypatch):
    monkeypatch.setattr(backup.site_users, "ensure_panel_user", lambda *a, **k: "op_x")
    victim = User(
        username="victim", email="v@example.test", hashed_password=hash_password("x"),
        role=Role.end_user.value,
    )
    db.add(victim)
    db.flush()
    db.add(
        Website(
            domain="victim.test", owner_id=victim.id,
            root_path="/home/op_victim/victim.test", document_root="public_html",
            linux_user="op_victim",
        )
    )
    db.commit()

    archive = _archive(tmp_path, _manifest(websites=[{"domain": "victim.test"}]))
    with pytest.raises(ValueError, match="already belongs to another account"):
        backup.restore_user_backup(str(archive), db)

    kept = db.query(Website).filter(Website.domain == "victim.test").one()
    assert kept.owner_id == victim.id, "the victim must still own the site"


def test_the_refusal_happens_before_any_side_effect(db, tmp_path, monkeypatch):
    """The preflight is the point: nothing privileged may run first."""
    touched: list[str] = []
    monkeypatch.setattr(
        backup.site_users, "ensure_panel_user",
        lambda *a, **k: (touched.append("ensure_panel_user"), "op_x")[1],
    )
    monkeypatch.setattr(
        backup.site_users, "ensure_site_runtime",
        lambda *a, **k: touched.append("ensure_site_runtime"),
    )
    victim = User(
        username="victim2", email="v2@example.test",
        hashed_password=hash_password("x"), role=Role.end_user.value,
    )
    db.add(victim)
    db.flush()
    db.add(
        Website(
            domain="victim2.test", owner_id=victim.id,
            root_path="/home/op_victim2/victim2.test", document_root="public_html",
            linux_user="op_victim2",
        )
    )
    db.commit()

    archive = _archive(tmp_path, _manifest(websites=[{"domain": "victim2.test"}]))
    with pytest.raises(ValueError):
        backup.restore_user_backup(str(archive), db)
    assert touched == [], f"privileged work ran before the archive was rejected: {touched}"


# --------------------------------------------------------------------------
# WAF directives
# --------------------------------------------------------------------------
def test_archive_waf_custom_rules_are_not_applied(db, tmp_path, monkeypatch):
    monkeypatch.setattr(backup.site_users, "ensure_panel_user", lambda *a, **k: "op_x")
    monkeypatch.setattr(backup.site_users, "ensure_site_runtime", lambda *a, **k: None)
    monkeypatch.setattr(backup.site_users, "ensure_document_root", lambda *a, **k: None)
    monkeypatch.setattr(backup, "_safe_extract_prefix", lambda *a, **k: None)
    monkeypatch.setattr(backup.waf, "sync_website_rules", lambda *a, **k: type("R", (), {"returncode": 0, "stderr": "", "stdout": ""})())
    monkeypatch.setattr(backup.openlitespeed, "rewrite_vhost", lambda *a, **k: "")

    archive = _archive(
        tmp_path,
        _manifest(
            websites=[{
                "domain": "site.test",
                "waf_custom_rules": "SecRuleEngine Off\n",
            }]
        ),
    )
    backup.restore_user_backup(str(archive), db)

    site = db.query(Website).filter(Website.domain == "site.test").one()
    assert site.waf_custom_rules == "", (
        "raw ModSecurity directives from an archive must never reach the "
        "root-written per-site include; api/waf.py refuses them from non-admins"
    )


def test_source_no_longer_reads_waf_custom_rules_from_the_manifest():
    source = Path(backup.__file__).read_text(encoding="utf-8")
    restore = source[source.index("def restore_user_backup") :]
    assert 'site_info.get("waf_custom_rules")' not in restore


# --------------------------------------------------------------------------
# db_user ownership
# --------------------------------------------------------------------------
def test_archive_cannot_repassword_another_accounts_db_user(db, tmp_path, monkeypatch):
    monkeypatch.setattr(backup.site_users, "ensure_panel_user", lambda *a, **k: "op_x")
    victim = User(
        username="victim3", email="v3@example.test",
        hashed_password=hash_password("x"), role=Role.end_user.value,
    )
    db.add(victim)
    db.flush()
    db.add(
        DatabaseAccount(
            owner_id=victim.id, website_id=None,
            db_name="victim3_db", db_user="victim3_dbuser", db_password="enc",
        )
    )
    db.commit()

    archive = _archive(
        tmp_path,
        _manifest(
            databases=[{
                "db_name": "attacker_fresh_db",
                "db_user": "victim3_dbuser",
                "db_password": "chosen-by-archive",
            }]
        ),
    )
    with pytest.raises(ValueError, match="Database user already belongs to another account"):
        backup.restore_user_backup(str(archive), db)


def test_every_allow_existing_call_is_guarded():
    """allow_existing turns create into ALTER USER ... IDENTIFIED BY."""
    for module in ("backup", "da_import"):
        source = (Path(backup.__file__).parent / f"{module}.py").read_text(encoding="utf-8")
        lines = source.splitlines()
        for index, line in enumerate(lines):
            if "allow_existing=True" not in line:
                continue
            window = "\n".join(lines[max(0, index - 6) : index])
            assert "assert_db_user_available" in window, (
                f"{module}.py:{index + 1} calls create_database_credentials with "
                "allow_existing=True without first checking db_user ownership"
            )
