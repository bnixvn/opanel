"""Passkey (WebAuthn) registration and login.

An account uses passkeys or an authenticator app, never both. Two second
factors in parallel means the account is only as strong as the weaker one, and
an attacker picks which to attack; keeping one also gives the operator a single
answer to "what does signing in to this account require". The rule is enforced
on the way in from both directions, with a message naming what to remove first.

Challenges live in memory with a short TTL rather than in the database. They
are single-use, expire in two minutes, and a lost one costs the user one retry
-- a table would outlive the ceremony it belongs to and need its own pruning.
The panel serves HTTP from a single uvicorn worker, so one process holds them
all; the schedulers are separate processes but never run a ceremony.
"""
from __future__ import annotations

import json
import secrets
import threading
import time
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, options_to_json
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.core.config import settings
from app.models.entities import User, WebauthnCredential

CHALLENGE_TTL_SECONDS = 120
MAX_CREDENTIALS_PER_USER = 10


class PasskeyError(ValueError):
    """Anything the caller should surface to the user as a 400."""


# ---------------------------------------------------------------------------
# Relying party identity
# ---------------------------------------------------------------------------

def _panel_url() -> str:
    from app.services import panel_settings

    try:
        configured = panel_settings.current_settings().get("panel_url") or ""
    except Exception:
        configured = ""
    return configured or settings.panel_url or ""


def relying_party() -> tuple[str, str]:
    """(rp_id, origin) for this panel.

    rp_id is the bare hostname with no port -- WebAuthn scopes credentials to a
    domain, not to a port -- while the origin the browser reports does include
    it, so the two are derived separately from the same URL.
    """
    parsed = urlparse(_panel_url())
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise PasskeyError(
            "Set the panel URL in Panel settings before using passkeys: a "
            "passkey is bound to the hostname it was created on."
        )
    scheme = parsed.scheme or "https"
    origin = f"{scheme}://{host}"
    if parsed.port:
        origin = f"{origin}:{parsed.port}"
    return host, origin


def is_available() -> bool:
    """False when the panel has no hostname to bind credentials to."""
    try:
        relying_party()
        return True
    except PasskeyError:
        return False


# ---------------------------------------------------------------------------
# Challenge store
# ---------------------------------------------------------------------------
_challenges: dict[str, tuple[bytes, float]] = {}
_challenge_lock = threading.Lock()


def _put_challenge(key: str, challenge: bytes) -> None:
    now = time.monotonic()
    with _challenge_lock:
        # Opportunistic prune: this dict is only ever as large as the number of
        # sign-ins in flight, but nothing else would ever remove an abandoned one.
        for stale in [k for k, (_, at) in _challenges.items() if now - at > CHALLENGE_TTL_SECONDS]:
            _challenges.pop(stale, None)
        _challenges[key] = (challenge, now)


def _take_challenge(key: str) -> Optional[bytes]:
    """Single use: taking it removes it, so a replay finds nothing."""
    with _challenge_lock:
        entry = _challenges.pop(key, None)
    if entry is None:
        return None
    challenge, at = entry
    if time.monotonic() - at > CHALLENGE_TTL_SECONDS:
        return None
    return challenge


def _registration_key(user: User) -> str:
    return f"reg:{user.id}"


def _login_key(username: str) -> str:
    return f"login:{(username or '').strip().lower()}"


# ---------------------------------------------------------------------------
# The one-factor rule
# ---------------------------------------------------------------------------

def credentials_for(db, user: User) -> list[WebauthnCredential]:
    return (
        db.query(WebauthnCredential)
        .filter(WebauthnCredential.user_id == user.id)
        .order_by(WebauthnCredential.id.asc())
        .all()
    )


def has_passkeys(db, user: User) -> bool:
    return (
        db.query(WebauthnCredential)
        .filter(WebauthnCredential.user_id == user.id)
        .first()
        is not None
    )


def assert_totp_may_be_enabled(db, user: User) -> None:
    """Called before an authenticator app is set up."""
    if has_passkeys(db, user):
        raise PasskeyError(
            "This account already uses a passkey. Remove the passkey first if "
            "you want to use an authenticator app instead."
        )


def _assert_passkey_may_be_added(db, user: User) -> None:
    if user.totp_enabled:
        raise PasskeyError(
            "This account already uses an authenticator app. Turn off "
            "two-factor authentication first if you want to use a passkey "
            "instead."
        )
    if len(credentials_for(db, user)) >= MAX_CREDENTIALS_PER_USER:
        raise PasskeyError(
            f"An account can hold at most {MAX_CREDENTIALS_PER_USER} passkeys."
        )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def begin_registration(db, user: User) -> dict:
    _assert_passkey_may_be_added(db, user)
    rp_id, _origin = relying_party()
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=settings.app_name or "OPanel",
        # Stable per account, and not the username: WebAuthn user handles are
        # stored by the authenticator, and a rename must not orphan the key.
        user_id=str(user.id).encode("utf-8"),
        user_name=user.username,
        user_display_name=user.username,
        # Refuse a key the account already holds, so the browser says "already
        # registered" instead of silently making a duplicate.
        exclude_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(cred.credential_id))
            for cred in credentials_for(db, user)
        ],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    _put_challenge(_registration_key(user), options.challenge)
    return json.loads(options_to_json(options))


def complete_registration(db, user: User, credential: dict, name: str = "") -> WebauthnCredential:
    _assert_passkey_may_be_added(db, user)
    challenge = _take_challenge(_registration_key(user))
    if challenge is None:
        raise PasskeyError("That passkey request expired. Start again.")
    rp_id, origin = relying_party()
    try:
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
        )
    except Exception as exc:  # the library raises several types
        raise PasskeyError(f"Could not register that passkey: {exc}") from exc

    credential_id = bytes_to_base64url(verified.credential_id)
    if (
        db.query(WebauthnCredential)
        .filter(WebauthnCredential.credential_id == credential_id)
        .first()
        is not None
    ):
        raise PasskeyError("That passkey is already registered.")

    label = (name or "").strip()[:64] or "Passkey"
    record = WebauthnCredential(
        user_id=user.id,
        credential_id=credential_id,
        public_key=bytes_to_base64url(verified.credential_public_key),
        sign_count=verified.sign_count or 0,
        name=label,
        transports=",".join(credential.get("transports") or [])[:128],
    )
    db.add(record)
    # Every existing session predates this factor, so none of them proved it.
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    db.refresh(record)
    return record


def delete_credential(db, user: User, credential_pk: int) -> bool:
    record = (
        db.query(WebauthnCredential)
        .filter(
            WebauthnCredential.id == credential_pk,
            WebauthnCredential.user_id == user.id,
        )
        .first()
    )
    if record is None:
        return False
    db.delete(record)
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    return True


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def begin_login(db, user: User) -> dict:
    """Options for an account that has already proved its password."""
    creds = credentials_for(db, user)
    if not creds:
        raise PasskeyError("This account has no passkey.")
    rp_id, _origin = relying_party()
    options = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=[
            PublicKeyCredentialDescriptor(id=base64url_to_bytes(cred.credential_id))
            for cred in creds
        ],
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    _put_challenge(_login_key(user.username), options.challenge)
    return json.loads(options_to_json(options))


def complete_login(db, user: User, credential: dict) -> bool:
    challenge = _take_challenge(_login_key(user.username))
    if challenge is None:
        return False
    raw_id = credential.get("rawId") or credential.get("id") or ""
    record = (
        db.query(WebauthnCredential)
        .filter(
            WebauthnCredential.credential_id == raw_id,
            WebauthnCredential.user_id == user.id,
        )
        .first()
    )
    if record is None:
        return False
    rp_id, origin = relying_party()
    try:
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=base64url_to_bytes(record.public_key),
            credential_current_sign_count=record.sign_count,
        )
    except Exception:
        return False

    # A counter that fails to advance is the signal an authenticator has been
    # cloned. Authenticators that do not implement one report 0 forever, which
    # is why this only applies once a nonzero count has been seen.
    if record.sign_count and verified.new_sign_count <= record.sign_count:
        return False
    record.sign_count = verified.new_sign_count
    record.last_used_at = datetime.utcnow()
    db.commit()
    return True
