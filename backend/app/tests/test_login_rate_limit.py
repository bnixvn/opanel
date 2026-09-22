"""Spraying one account must be throttled; its owner must never be locked out.

Three shapes were tried. The original enforced the account-name counter BEFORE
verifying the password, so eight wrong guesses a minute held a named account at
429 for its real owner, from any number of addresses -- the account-DoS the
lockout exemption already existed to prevent, arriving through the other
counter. Removing the counter cured that but left distributed guessing against
one account completely unmetered, and the lockout had already been exempt, so
the wrong half was given up.

The position was the bug. These tests pin both halves of the fix through the
real login() function rather than by reading its source.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import auth as auth_api
from app.core.database import Base
from app.core.security import hash_password
from app.models.entities import User

PASSWORD = "CorrectHorseBattery1"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(
        username="victim", email="victim@example.test",
        hashed_password=hash_password(PASSWORD), role="end_user", is_active=True,
    ))
    session.commit()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def memory_backend(monkeypatch):
    monkeypatch.setattr(auth_api, "_rate_limit_backend", lambda: "memory")
    auth_api._login_attempts.clear()
    auth_api._login_failures.clear()
    auth_api._login_lockouts.clear()
    yield
    auth_api._login_attempts.clear()
    auth_api._login_failures.clear()
    auth_api._login_lockouts.clear()


class _Form:
    def __init__(self, username, password):
        self.username = username
        self.password = password


def _request(ip):
    class _Client:
        host = ip

    class _Req:
        client = _Client()
        headers = {}
        cookies = {}
        url = type("U", (), {"scheme": "https", "hostname": "panel.test"})()

    return _Req()


def _attempt(db, password, ip="203.0.113.9"):
    """One login attempt. Returns the HTTPException status, or None on success."""
    try:
        auth_api.login(
            request=_request(ip), response=Response(),
            form=_Form("victim", password), otp="", db=db,
        )
        return None
    except HTTPException as exc:
        return exc.status_code


# --------------------------------------------------------------------------
# the owner must never be locked out
# --------------------------------------------------------------------------
def test_the_owner_gets_in_however_many_wrong_guesses_preceded_them(db):
    """Each guess arrives from a different address, so no IP limit applies."""
    for n in range(auth_api._LOGIN_MAX_ATTEMPTS * 3):
        _attempt(db, "wrong-guess", ip=f"198.51.100.{n % 254 + 1}")

    assert _attempt(db, PASSWORD, ip="198.51.100.200") is None, (
        "a correct password must never be refused because someone else has "
        "been guessing at this account -- that is the account-DoS the whole "
        "exemption exists to prevent"
    )


def test_a_correct_password_is_checked_before_the_account_counter(db):
    """The ordering is the fix; assert it through behaviour, not source."""
    for n in range(auth_api._LOGIN_MAX_ATTEMPTS * 2):
        _attempt(db, "wrong-guess", ip=f"198.51.100.{n % 254 + 1}")
    user_key = auth_api._username_key("victim")
    # The counter is genuinely over its limit...
    with pytest.raises(HTTPException) as exc:
        auth_api._enforce_rate_limit(user_key)
    assert exc.value.status_code == 429
    # ...and the owner still gets in.
    assert _attempt(db, PASSWORD, ip="198.51.100.201") is None


# --------------------------------------------------------------------------
# spraying must still be throttled
# --------------------------------------------------------------------------
def test_guessing_at_one_account_is_throttled_across_addresses(db):
    statuses = [
        _attempt(db, "wrong-guess", ip=f"198.51.100.{n % 254 + 1}")
        for n in range(auth_api._LOGIN_MAX_ATTEMPTS * 2)
    ]
    assert 401 in statuses, "the first guesses are ordinary failures"
    assert 429 in statuses, (
        "once the account is over its window, further guesses must be "
        "throttled -- removing the counter left this completely unmetered"
    )


def test_the_account_counter_is_recorded_again():
    """My previous fix made this key feed nothing, which is what left the
    spraying path unmetered."""
    key = auth_api._username_key("someone")
    auth_api._record_failure(key, apply_lockout=False)
    assert auth_api._login_attempts[key], "the short-window counter must be fed"
    assert not auth_api._login_failures[key], (
        "the long lockout stays source-only, or spraying could lock an account"
    )


# --------------------------------------------------------------------------
# per-source limiting is unchanged
# --------------------------------------------------------------------------
def test_one_source_is_still_limited_on_its_own(db):
    statuses = [_attempt(db, "wrong-guess", ip="203.0.113.77")
                for _ in range(auth_api._LOGIN_MAX_ATTEMPTS + 3)]
    assert 429 in statuses


def test_a_source_that_keeps_failing_is_locked_out(db):
    for _ in range(auth_api._LOGIN_LOCKOUT_THRESHOLD + 2):
        auth_api._record_failure("ip:203.0.113.88", apply_lockout=True)
    with pytest.raises(HTTPException) as exc:
        auth_api._enforce_rate_limit("ip:203.0.113.88")
    assert exc.value.status_code == 429
