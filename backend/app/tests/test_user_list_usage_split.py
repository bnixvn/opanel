"""The accounts list must not wait on a disk measurement.

Storage used is the one figure on that page that costs real work -- a du over
every site an account owns -- and it held the whole list back. It now comes
from /users/usage, fetched after the rows are on screen.
"""
import inspect
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from app.api import users as users_api
from app.schemas.schemas import UserOut, UserUsageOut


def test_the_list_measures_nothing():
    source = inspect.getsource(users_api.list_users)

    assert "storage_usage_summary" not in source
    assert "user_storage_used_bytes" not in source


def test_the_list_still_returns_the_limit():
    """It is arithmetic on a column, not a measurement, so it costs nothing."""
    source = inspect.getsource(users_api.list_users)

    assert "storage_quota.user_storage_limit_bytes(user)" in source


def test_unmeasured_is_null_not_zero():
    """0 would read as "uses nothing", which is a different claim."""
    source = inspect.getsource(users_api.list_users)

    assert 'data["storage_used_bytes"] = None' in source
    assert 'data["storage_percent"] = None' in source

    assert UserOut.model_fields["storage_used_bytes"].default is None
    assert UserOut.model_fields["storage_percent"].default is None


def test_the_usage_endpoint_does_the_measuring():
    source = inspect.getsource(users_api.list_user_usage)

    assert "storage_usage_summary" in source
    assert '"id": user.id' in source


def test_usage_is_admin_only():
    assert "ensure_role(current_user.role, Role.admin)" in inspect.getsource(users_api.list_user_usage)


def test_usage_carries_an_id_to_merge_on():
    assert "id" in UserUsageOut.model_fields
    for name in ("storage_used_bytes", "storage_limit_bytes", "storage_percent"):
        assert name in UserUsageOut.model_fields


def test_the_literal_route_is_declared_before_the_parameterised_one():
    """/users/usage would otherwise be swallowed by /users/{user_id} and come
    back as "User not found"."""
    source = inspect.getsource(users_api)
    usage = source.index('@router.get("/usage"')
    by_id = source.index('@router.get("/{user_id}"') if '@router.get("/{user_id}"' in source else len(source)

    assert usage < by_id


def test_a_single_user_still_gets_its_figure():
    """/users/me and the create/update responses are one account each, so
    measuring there is cheap and the value must stay real."""
    assert "storage_usage_summary" in inspect.getsource(users_api._user_out)
    assert "_user_out(current_user, db)" in inspect.getsource(users_api.me)
