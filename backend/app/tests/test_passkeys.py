"""Passkeys, and the rule that an account uses one second factor.

Two factors in parallel make the account only as strong as the weaker one and
let an attacker choose which to face, so registering a passkey is refused while
an authenticator app is on, and enabling an authenticator app is refused while
a passkey exists. Both directions are enforced, with a message naming what to
remove first.
"""
from __future__ import annotations

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
# one factor per account, both directions
# --------------------------------------------------------------------------
def test_a_passkey_cannot_be_added_while_an_authenticator_app_is_on(db, user, panel_host):
    user.totp_enabled = True
    db.commit()
    with pytest.raises(passkeys.PasskeyError, match="authenticator app"):
        passkeys.begin_registration(db, user)


def test_an_authenticator_app_cannot_be_enabled_while_a_passkey_exists(db, user):
    _credential(db, user)
    with pytest.raises(passkeys.PasskeyError, match="passkey"):
        passkeys.assert_totp_may_be_enabled(db, user)


def test_the_refusal_names_what_to_remove_first(db, user, panel_host):
    user.totp_enabled = True
    db.commit()
    with pytest.raises(passkeys.PasskeyError) as exc:
        passkeys.begin_registration(db, user)
    assert "Turn off" in str(exc.value)

    user.totp_enabled = False
    db.commit()
    _credential(db, user)
    with pytest.raises(passkeys.PasskeyError) as exc:
        passkeys.assert_totp_may_be_enabled(db, user)
    assert "Remove the passkey" in str(exc.value)


def test_neither_blocks_the_other_on_a_fresh_account(db, user, panel_host):
    passkeys.assert_totp_may_be_enabled(db, user)  # must not raise
    options = passkeys.begin_registration(db, user)
    assert options["challenge"]


def test_removing_the_last_passkey_frees_the_authenticator_app(db, user):
    record = _credential(db, user)
    with pytest.raises(passkeys.PasskeyError):
        passkeys.assert_totp_may_be_enabled(db, user)
    assert passkeys.delete_credential(db, user, record.id)
    passkeys.assert_totp_may_be_enabled(db, user)  # must not raise


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
