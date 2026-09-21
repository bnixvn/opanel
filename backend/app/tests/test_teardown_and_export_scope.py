"""Deletion must reach everything the account owns, and export must not leak.

Two separate defects with one shape: a per-website lookup used where the
resource is owned per account.

terminate_account and users._delete_owned_website both selected the database to
drop with ``DatabaseAccount.website_id == website.id`` and ``.first()``. That
missed standalone databases (``POST /api/databases`` creates them with
``website_id`` NULL -- on a live box three of eight rows looked like that), any
database past the first on a website, and every WHMCS-provisioned WordPress
account, because ``create_account`` calls ``mariadb.create_database`` directly
and never inserts a ``DatabaseAccount`` row for the lookup to find.

The residue is adoptable, not inert: ``safe_db_identifier`` derives ``db_name``
from the domain and ``create_database`` issues ``CREATE DATABASE IF NOT
EXISTS``, so the next website for that domain silently inherits the previous
tenant's schema under its new owner.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from app.api import users as users_api
from app.services import backup, da_import, provisioning


def _code(text: str) -> str:
    """Drop comment lines so these assertions read code, not prose.

    Several of the fixes below carry comments quoting the old, wrong
    expression; without this the tests would match their own explanation.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def _source(obj) -> str:
    return _code(inspect.getsource(obj))


# --------------------------------------------------------------------------
# teardown scope
# --------------------------------------------------------------------------
def test_terminate_drops_databases_by_owner_not_by_website():
    source = _source(provisioning.terminate_account)
    assert "DatabaseAccount.owner_id == user.id" in source, (
        "terminate must drop every database the account owns"
    )
    assert "DatabaseAccount.website_id == website.id" not in source, (
        "the per-website lookup missed standalone databases and every "
        "WHMCS-provisioned account, which never gets a DatabaseAccount row"
    )


def test_terminate_does_not_stop_at_the_first_database():
    source = _source(provisioning.terminate_account)
    db_block = source[source.index("DatabaseAccount.owner_id") :]
    assert ".all()" in db_block.split("\n")[1] or ".all()" in db_block[:200]
    assert ".first()" not in db_block[:200]


def test_user_deletion_also_reaches_databases_with_no_website():
    source = _source(users_api.delete_user)
    assert "_delete_orphan_databases" in source, (
        "deleting a user must drop the databases that no website teardown reached"
    )
    scope = _source(users_api._delete_orphan_databases)
    assert "DatabaseAccount.owner_id == owner_id" in scope


def test_per_website_deletion_handles_more_than_one_database():
    source = _source(users_api._delete_owned_website)
    assert ".all()" in source and ".first()" not in source


# --------------------------------------------------------------------------
# export scope
# --------------------------------------------------------------------------
def test_backup_manifest_carries_no_password_hash_and_no_role():
    source = _source(backup.create_user_backup)
    manifest = source[source.index('"kind": "opanel_user"') : source.index('"websites": []')]
    assert "hashed_password" not in manifest, (
        "the archive leaves the box with no client-side encryption, and "
        "manifest.json is the first member, so a bcrypt hash in it is "
        "recoverable from a few kilobytes by anyone who can read the bucket"
    )
    assert '"role"' not in manifest, (
        "the restore no longer honours a role from the archive, so exporting "
        "one only invites it being trusted again"
    )


def test_restore_does_not_read_a_credential_or_role_from_the_manifest():
    source = _code(Path(backup.__file__).read_text(encoding="utf-8"))
    restore = source[source.index("def restore_user_backup") :]
    assert 'user_info.get("hashed_password")' not in restore
    assert 'user_info.get("role")' not in restore
    assert "Role.end_user.value" in restore


# --------------------------------------------------------------------------
# DA import overwrite scope
# --------------------------------------------------------------------------
def test_overwrite_no_longer_deletes_the_whole_account():
    source = _code(Path(da_import.__file__).read_text(encoding="utf-8"))
    # The old helper removed every Website and DatabaseAccount the user owned,
    # plus the User row, for an archive that named only some of them.
    assert "def _delete_existing_user" not in source
    assert "def _overwrite_scope" in source


def test_overwrite_reports_what_it_kept():
    source = _source(da_import._process_archive)
    assert "_overwrite_scope" in source
    assert "warnings" in source[source.index("_overwrite_scope") :][:400], (
        "sites the archive does not contain must be surfaced to the operator, "
        "not silently removed -- the clash message actively recommends "
        "re-running with overwrite enabled"
    )


def test_overwrite_scope_excludes_the_archives_own_domains():
    scope = _source(da_import._overwrite_scope)
    assert "not in named" in scope, (
        "domains the archive brings back are replaced by _delete_existing_domain "
        "and must not be reported as kept"
    )
