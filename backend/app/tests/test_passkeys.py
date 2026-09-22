"""Passkeys, and how they sit beside an authenticator app.

An account may hold both. Sign-in prefers the passkey and falls back to a code
when the passkey cannot be used -- a borrowed machine, a browser without
WebAuthn, a key left at home. Two factors in parallel do mean an attacker may
attack whichever is weaker; in exchange nobody is locked out of their own panel
by a lost device, which is the commoner failure.
"""
from __future__ import annotations

import pathlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.core.security import hash_password
from app.models.entities import User, WebauthnCredential
from app.services import passkeys


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def user(db):
    row = User(
        username="tenant", email="t@example.test",
        hashed_password=hash_password("x" * 14), role="end_user", is_active=True,
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def panel_host(monkeypatch):
    monkeypatch.setattr(
        passkeys, "_panel_url", lambda: "https://panel.example.test:2222"
    )


def _credential(db, user, credential_id="Y3JlZDE"):
    row = WebauthnCredential(
        user_id=user.id, credential_id=credential_id,
        public_key="cGs", sign_count=0, name="Test key",
    )
    db.add(row)
    db.commit()
    return row


# --------------------------------------------------------------------------
# the two coexist
# --------------------------------------------------------------------------
def test_a_passkey_can_be_added_while_an_authenticator_app_is_on(db, user, panel_host):
    user.totp_enabled = True
    db.commit()
    options = passkeys.begin_registration(db, user)
    assert options["challenge"], "an authenticator app must not block a passkey"


def test_an_authenticator_app_can_be_set_up_while_a_passkey_exists(db, user, panel_host):
    _credential(db, user)
    # Nothing in the passkey layer refuses it any more, and auth.py no longer
    # consults this module before generating a TOTP secret.
    assert not hasattr(passkeys, "assert_totp_may_be_enabled")
    source = (
        pathlib.Path(passkeys.__file__).parent.parent / "api" / "auth.py"
    ).read_text(encoding="utf-8")
    setup = source[source.index("def setup_two_factor") : source.index("def enable_two_factor")]
    assert "PasskeyError" not in setup


def test_an_account_can_hold_both_at_once(db, user, panel_host):
    user.totp_enabled = True
    db.commit()
    _credential(db, user)
    assert passkeys.has_passkeys(db, user) is True
    assert user.totp_enabled is True
    # And a second passkey is still fine.
    options = passkeys.begin_registration(db, user)
    assert options["challenge"]


# --------------------------------------------------------------------------
# relying party
# --------------------------------------------------------------------------
def test_the_rp_id_is_the_bare_host_and_the_origin_keeps_the_port(panel_host):
    rp_id, origin = passkeys.relying_party()
    assert rp_id == "panel.example.test", "a credential is scoped to a domain, not a port"
    assert origin == "https://panel.example.test:2222", "the browser reports the port"


def test_passkeys_are_unavailable_without_a_panel_hostname(monkeypatch):
    monkeypatch.setattr(passkeys, "_panel_url", lambda: "")
    assert passkeys.is_available() is False
    with pytest.raises(passkeys.PasskeyError, match="panel URL"):
        passkeys.relying_party()


# --------------------------------------------------------------------------
# challenges
# --------------------------------------------------------------------------
def test_a_challenge_is_single_use(db, user, panel_host):
    passkeys.begin_registration(db, user)
    key = passkeys._registration_key(user)
    assert passkeys._take_challenge(key) is not None
    assert passkeys._take_challenge(key) is None, "a replay must find nothing"


def test_an_expired_challenge_is_refused(db, user, panel_host, monkeypatch):
    passkeys.begin_registration(db, user)
    key = passkeys._registration_key(user)
    # Capture the real clock before patching, or the replacement calls itself.
    real_monotonic = passkeys.time.monotonic
    later = real_monotonic() + passkeys.CHALLENGE_TTL_SECONDS + 1
    monkeypatch.setattr(passkeys.time, "monotonic", lambda: later)
    assert passkeys._take_challenge(key) is None


def test_completing_registration_without_a_challenge_is_refused(db, user, panel_host):
    with pytest.raises(passkeys.PasskeyError, match="expired"):
        passkeys.complete_registration(db, user, {"id": "x"})


def test_login_without_a_challenge_fails_closed(db, user, panel_host):
    _credential(db, user)
    assert passkeys.complete_login(db, user, {"rawId": "Y3JlZDE"}) is False


# --------------------------------------------------------------------------
# registration options
# --------------------------------------------------------------------------
def test_registration_excludes_keys_the_account_already_holds(db, user, panel_host):
    _credential(db, user)
    options = passkeys.begin_registration(db, user)
    assert options["excludeCredentials"], (
        "without this the browser silently registers a duplicate instead of "
        "saying the key is already enrolled"
    )


def test_the_user_handle_is_the_account_id_not_the_username(db, user, panel_host):
    options = passkeys.begin_registration(db, user)
    from webauthn.helpers import base64url_to_bytes

    assert base64url_to_bytes(options["user"]["id"]) == str(user.id).encode()


def test_an_account_cannot_hoard_passkeys(db, user, panel_host):
    for n in range(passkeys.MAX_CREDENTIALS_PER_USER):
        _credential(db, user, credential_id=f"cred{n}")
    with pytest.raises(passkeys.PasskeyError, match="at most"):
        passkeys.begin_registration(db, user)


# --------------------------------------------------------------------------
# login options
# --------------------------------------------------------------------------
def test_login_offers_only_this_accounts_keys(db, user, panel_host):
    other = User(
        username="other", email="o@example.test",
        hashed_password=hash_password("x" * 14), role="end_user", is_active=True,
    )
    db.add(other)
    db.commit()
    _credential(db, user, credential_id="mine")
    _credential(db, other, credential_id="theirs")

    options = passkeys.begin_login(db, user)
    offered = {c["id"] for c in options["allowCredentials"]}
    assert "mine" in offered
    assert "theirs" not in offered


def test_login_refuses_an_account_with_no_passkey(db, user, panel_host):
    with pytest.raises(passkeys.PasskeyError, match="no passkey"):
        passkeys.begin_login(db, user)


def test_a_credential_belonging_to_another_account_is_refused(db, user, panel_host):
    other = User(
        username="other2", email="o2@example.test",
        hashed_password=hash_password("x" * 14), role="end_user", is_active=True,
    )
    db.add(other)
    db.commit()
    _credential(db, other, credential_id="theirs")
    _credential(db, user, credential_id="mine")
    passkeys.begin_login(db, user)
    assert passkeys.complete_login(db, user, {"rawId": "theirs"}) is False


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------
def test_deleting_a_passkey_invalidates_existing_sessions(db, user):
    record = _credential(db, user)
    before = user.token_version or 0
    passkeys.delete_credential(db, user, record.id)
    assert (user.token_version or 0) > before, (
        "a session issued before the factor changed never proved it"
    )


def test_one_account_cannot_delete_anothers_passkey(db, user):
    other = User(
        username="other3", email="o3@example.test",
        hashed_password=hash_password("x" * 14), role="end_user", is_active=True,
    )
    db.add(other)
    db.commit()
    theirs = _credential(db, other, credential_id="theirs")
    assert passkeys.delete_credential(db, user, theirs.id) is False
    assert db.query(WebauthnCredential).filter_by(id=theirs.id).first() is not None


# --------------------------------------------------------------------------
# sign-in prefers the passkey, and falls back to a code
# --------------------------------------------------------------------------
PASSWORD = "CorrectHorseBattery1"


@pytest.fixture
def login_user(db):
    from app.core.security import hash_password as _hash

    row = User(
        username="loginer", email="l@example.test",
        hashed_password=_hash(PASSWORD), role="end_user", is_active=True,
    )
    db.add(row)
    db.commit()
    return row


@pytest.fixture
def memory_limits(monkeypatch):
    from app.api import auth as auth_api

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


def _request():
    class _Client:
        host = "203.0.113.5"

    class _Req:
        client = _Client()
        headers = {}
        cookies = {}
        url = type("U", (), {"scheme": "https", "hostname": "panel.example.test"})()

    return _Req()


def _login(db, user, **kwargs):
    from fastapi import Response

    from app.api import auth as auth_api

    return auth_api.login(
        request=_request(), response=Response(),
        form=_Form(user.username, PASSWORD),
        otp=kwargs.get("otp", ""), passkey=kwargs.get("passkey", ""), db=db,
    )


def test_an_account_with_a_passkey_is_offered_the_passkey_first(
    db, login_user, panel_host, memory_limits
):
    _credential(db, login_user, credential_id="k1")
    result = _login(db, login_user)
    assert result.requires_passkey is True
    assert result.passkey_options["challenge"]
    assert result.access_token is None


def test_the_offer_says_whether_a_code_is_also_available(
    db, login_user, panel_host, memory_limits
):
    _credential(db, login_user, credential_id="k1")
    assert _login(db, login_user).requires_2fa is False, (
        "no authenticator app, so the client must not offer a code"
    )
    login_user.totp_enabled = True
    db.commit()
    assert _login(db, login_user).requires_2fa is True, (
        "the client needs this to offer 'use a code instead' without another "
        "round trip"
    )


def test_a_code_gets_in_even_though_the_account_has_a_passkey(
    db, login_user, panel_host, memory_limits, monkeypatch
):
    """The fallback: the passkey could not be used on this device."""
    from app.api import auth as auth_api

    _credential(db, login_user, credential_id="k1")
    login_user.totp_enabled = True
    db.commit()
    monkeypatch.setattr(auth_api, "_verify_totp", lambda user, code: code == "123456")

    result = _login(db, login_user, otp="123456")
    assert result.access_token, "a valid code must sign the account in"


def test_a_wrong_code_still_fails(db, login_user, panel_host, memory_limits, monkeypatch):
    from fastapi import HTTPException

    from app.api import auth as auth_api

    _credential(db, login_user, credential_id="k1")
    login_user.totp_enabled = True
    db.commit()
    monkeypatch.setattr(auth_api, "_verify_totp", lambda user, code: False)

    with pytest.raises(HTTPException) as exc:
        _login(db, login_user, otp="000000")
    assert exc.value.status_code == 401


def test_a_code_is_refused_when_the_account_has_no_authenticator_app(
    db, login_user, panel_host, memory_limits
):
    from fastapi import HTTPException

    _credential(db, login_user, credential_id="k1")
    with pytest.raises(HTTPException) as exc:
        _login(db, login_user, otp="123456")
    assert exc.value.status_code == 401


def test_an_account_with_only_an_authenticator_app_is_unaffected(
    db, login_user, memory_limits
):
    login_user.totp_enabled = True
    db.commit()
    result = _login(db, login_user)
    assert result.requires_2fa is True
    assert result.requires_passkey is False


def test_an_account_with_neither_signs_straight_in(db, login_user, memory_limits):
    result = _login(db, login_user)
    assert result.access_token
