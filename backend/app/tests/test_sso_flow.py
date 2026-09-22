"""The SSO handshake, exercised rather than grepped.

The previous guard was wired to POST /sso and GET /sso/{token} and checked
Sec-Fetch-Site. It blocked nothing a browser could do: the only URL the product
mints is /sso#<token> (provisioning.create_sso_login), and GET /sso served a
same-origin script that auto-submitted the token. A form posted by a document
at https://panel/sso to https://panel/sso carries Sec-Fetch-Site: same-origin
no matter how that document was reached, so a redirect from any origin still
dropped a victim into an account chosen by whoever sent the link.

What stops that is user activation: a redirect cannot click a button. Naming
the account on the button is what lets the visitor notice it is not theirs.
"""
from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main
from app.core.database import Base
from app.core.security import hash_password
from app.models.entities import PanelSsoToken, User
from app.services import provisioning


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _user(db, username="tenant", totp=False):
    user = User(
        username=username, email=f"{username}@example.test",
        hashed_password=hash_password("x" * 14), role="end_user",
        is_active=True, totp_enabled=totp,
    )
    db.add(user)
    db.flush()
    return user


def _token(db, user):
    raw = secrets.token_urlsafe(32)
    db.add(PanelSsoToken(
        user_id=user.id,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        expires_at=datetime.utcnow() + timedelta(seconds=300),
    ))
    db.commit()
    return raw


class _Request:
    """Enough of a Request for the header and cookie checks."""

    def __init__(self, headers=None, cookies=None):
        self.headers = headers or {}
        self.cookies = cookies or {}


# --------------------------------------------------------------------------
# peek vs consume
# --------------------------------------------------------------------------
def test_peek_names_the_account_without_consuming(db):
    user = _user(db)
    raw = _token(db, user)

    assert provisioning.peek_sso_token(db, raw) == user.id
    # Still usable: peeking must not burn the single-use token, or the
    # confirmation page would spend it before the visitor clicked.
    assert provisioning.peek_sso_token(db, raw) == user.id
    assert provisioning.consume_sso_token(db, raw) == user.id


def test_consume_is_single_use(db):
    user = _user(db)
    raw = _token(db, user)
    assert provisioning.consume_sso_token(db, raw) == user.id
    assert provisioning.consume_sso_token(db, raw) is None
    assert provisioning.peek_sso_token(db, raw) is None


def test_peek_refuses_an_expired_token(db):
    user = _user(db)
    raw = secrets.token_urlsafe(32)
    db.add(PanelSsoToken(
        user_id=user.id,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        expires_at=datetime.utcnow() - timedelta(seconds=1),
    ))
    db.commit()
    assert provisioning.peek_sso_token(db, raw) is None


# --------------------------------------------------------------------------
# the page no longer signs anyone in by itself
# --------------------------------------------------------------------------
def _script_code() -> str:
    """The script with // comments stripped.

    Asserting against the raw text matches the comments explaining the fix,
    which is how an earlier version of these tests passed for the wrong reason.
    """
    script = main.sso_intermediary_script().body.decode("utf-8")
    return "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("//")
    )


def test_the_sign_in_page_requires_a_click():
    code = _script_code()
    assert "addEventListener" in code, "the visitor has to act"
    # Exactly one submit, and it is reached only from the click handler. The
    # auto-submit is what made the whole flow reachable by redirect.
    assert code.count("submit()") == 1
    assert code.index('addEventListener("click"') < code.index("submit()"), (
        "the form must not submit before the handler is attached"
    )
    handler = code[code.index('addEventListener("click"'):]
    assert "submit()" in handler.split("});")[0], (
        "submit must sit inside the click handler body"
    )


def test_the_page_names_the_account_and_warns_about_replacement():
    code = _script_code()
    assert "Continue as " in code
    assert "/sso/preview" in code
    # The username reaches the DOM as text, never as markup.
    assert "innerHTML" not in code
    assert "textContent" in code
    html = main._sso_page_html("nonce-value")
    assert "replaces any OPanel session already open" in html


def test_the_page_carries_a_nonce_matching_its_cookie():
    class _Req:
        url = type("U", (), {"scheme": "https", "hostname": "panel.test"})()
        headers = {}
    page = main.sso_intermediary(_Req())
    html = page.body.decode("utf-8")
    cookie = next(
        (v for k, v in page.raw_headers if k.lower() == b"set-cookie"), b""
    ).decode("utf-8")
    assert main.SSO_NONCE_COOKIE in cookie
    nonce = re.search(rf"{main.SSO_NONCE_COOKIE}=([^;]+)", cookie).group(1)
    assert f'name="nonce" value="{nonce}"' in html
    assert "HttpOnly" in cookie and "strict" in cookie.lower()


# --------------------------------------------------------------------------
# the deprecated GET must not be a one-click login
# --------------------------------------------------------------------------
def test_the_legacy_get_route_no_longer_signs_anyone_in(db):
    user = _user(db)
    raw = _token(db, user)

    result = main.sso_login(raw)
    assert isinstance(result, RedirectResponse)
    assert result.status_code == 302
    assert result.headers["location"] == f"/sso#{raw}"
    # It must not set a session, and must not spend the token.
    assert not any(k.lower() == b"set-cookie" for k, _ in result.raw_headers)
    assert provisioning.peek_sso_token(db, raw) == user.id


def test_the_legacy_route_escapes_the_token_into_the_fragment():
    result = main.sso_login("a b/c#d")
    assert result.headers["location"] == "/sso#a%20b%2Fc%23d"


# --------------------------------------------------------------------------
# cross-origin posts, and the 2FA door
# --------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["cross-site", "none"])
def test_a_cross_origin_post_is_refused(value):
    with pytest.raises(HTTPException) as exc:
        main._reject_cross_site_sso(_Request({"sec-fetch-site": value}))
    assert exc.value.status_code == 403


@pytest.mark.parametrize("value", ["same-origin", "same-site"])
def test_the_pages_own_post_is_allowed(value):
    main._reject_cross_site_sso(_Request({"sec-fetch-site": value}))


def test_a_client_that_sends_no_fetch_metadata_is_allowed():
    """The WHMCS integration is not a browser."""
    main._reject_cross_site_sso(_Request({}))


def test_two_factor_accounts_are_sent_to_the_login_form(db):
    user = _user(db, "hastotp", totp=True)
    raw = _token(db, user)

    result = main._sso_user_or_error(db, raw)
    assert isinstance(result, RedirectResponse), (
        "a raw JSON 403 left a WHMCS customer staring at an error blob in a new "
        "tab with nowhere to go"
    )
    assert result.headers["location"] == "/?sso=2fa"


def test_an_invalid_token_redirects_rather_than_erroring(db):
    result = main._sso_user_or_error(db, "not-a-real-token")
    assert isinstance(result, RedirectResponse)
    assert result.headers["location"] == "/?sso=invalid"


def test_a_good_token_returns_the_user(db):
    user = _user(db)
    raw = _token(db, user)
    assert main._sso_user_or_error(db, raw) is user
