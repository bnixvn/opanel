"""A website's databases go with it when the website changes hands.

Before this, reassigning a website moved the files, the Linux user, the vhost
and the WAF rules, and left DatabaseAccount untouched. The new owner then saw
a site whose database the panel would not show them -- list_databases filters
on owner_id -- while the old owner could still open phpMyAdmin on it, change
its password, or drop it. Since backups follow database ownership, the data
was also filed under the wrong account: restoring the new owner gave them the
files with no database.

Nothing moves in MariaDB. The database, its MySQL user and its grants are
untouched, so the site keeps running on the credentials already in its config.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.api import databases as databases_api
from app.api import websites as websites_api


def test_reassigning_a_website_moves_its_databases():
    source = inspect.getsource(websites_api.update_website)

    assert "DatabaseAccount.website_id == website.id" in source
    assert "item.owner_id = payload.owner_id" in source


def test_only_the_databases_of_that_website_move():
    """An account's other databases are its own, not the site's, and a site
    the panel never linked a database to cannot have one inferred for it."""
    source = inspect.getsource(websites_api.update_website)

    assert "DatabaseAccount.website_id == website.id" in source
    # Never a blanket move of everything the old owner happened to own.
    assert "DatabaseAccount.owner_id == website.owner_id" not in source


def test_the_move_is_recorded():
    source = inspect.getsource(websites_api.update_website)

    assert '"move_database_owner"' in source


def test_nothing_is_touched_when_the_owner_does_not_change():
    """The whole block sits inside `if payload.owner_id != website.owner_id`,
    so saving any other setting leaves the databases alone."""
    source = inspect.getsource(websites_api.update_website)
    guard = source.index("if payload.owner_id != website.owner_id:")
    move = source.index("DatabaseAccount.website_id == website.id")
    assign = source.index("website.owner_id = payload.owner_id")

    assert guard < move < assign


def test_a_database_already_owned_by_the_target_is_not_rewritten():
    source = inspect.getsource(websites_api.update_website)

    assert "DatabaseAccount.owner_id != payload.owner_id" in source


def test_moving_a_database_alone_needs_admin():
    source = inspect.getsource(databases_api.change_database_owner)

    assert "ensure_role(current_user.role, Role.admin)" in source


def test_a_database_attached_to_a_website_cannot_be_moved_on_its_own():
    """That is exactly how the two end up owned by different accounts."""
    source = inspect.getsource(databases_api.change_database_owner)

    assert "if item.website_id:" in source
    assert "website.owner_id != owner.id" in source
    assert "Reassign that website" in source


def test_an_unattached_database_can_be_handed_over():
    source = inspect.getsource(databases_api.change_database_owner)
    guard = source.index("if item.website_id:")
    assign = source.index("item.owner_id = owner.id")

    assert guard < assign


def test_the_target_account_has_to_exist_and_be_active():
    source = inspect.getsource(databases_api.change_database_owner)

    assert "User.is_active.is_(True)" in source
    assert "Owner not found or inactive" in source


def test_mariadb_is_never_touched_by_the_handover():
    """No grant is rewritten and no password is rotated: the site is still
    running against these credentials."""
    source = inspect.getsource(databases_api.change_database_owner)

    assert "mariadb." not in source


def test_the_handover_is_recorded():
    source = inspect.getsource(databases_api.change_database_owner)

    assert '"change_database_owner"' in source


def test_the_owner_payload_rejects_a_missing_or_bogus_account():
    import pytest
    from pydantic import ValidationError

    from app.schemas.schemas import DatabaseOwnerUpdate

    assert DatabaseOwnerUpdate(owner_id=3).owner_id == 3
    for bad in (0, -1):
        with pytest.raises(ValidationError):
            DatabaseOwnerUpdate(owner_id=bad)
