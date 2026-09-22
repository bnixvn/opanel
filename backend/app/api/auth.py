import base64
import logging
import json
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime
from io import BytesIO
from threading import Lock
from typing import Deque, Dict, Optional

import pyotp
import qrcode
from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_current_user_optional
from app.core.config import settings
from app.core.database import get_db
from app.core.permissions import Role, ensure_role
from app.core.security import create_access_token, hash_password, needs_rehash, verify_password
from app.core.secrets import decrypt, encrypt
from app.core.step_up import require_current_password, require_sensitive_action_step_up, verify_totp
from app.services import passkeys
from app.models.entities import RevokedToken, User
from app.schemas.schemas import (
    LoginResponse,
    PasskeyDeleteRequest,
    PasskeyOut,
    PasskeyRegisterBegin,
    PasskeyRegisterComplete,
    PasskeyStatus,
    TwoFactorDisableRequest,
    TwoFactorEnableRequest,
    TwoFactorSetup,
    TwoFactorSetupRequest,
    TwoFactorStatus,
)
from app.services import storage_quota
from app.services.audit import log_action

# Hard caps for credentials submitted to /auth/login. bcrypt accepts at most
# 72 bytes anyway and we never want to spend CPU comparing absurd payloads.
_MAX_USERNAME_LEN = 64
_MAX_PASSWORD_LEN = 72

router = APIRouter(prefix="/auth", tags=["auth"])


# Cookie names. The session cookie is HttpOnly so JavaScript cannot read it,
# which mitigates token theft via XSS. The CSRF cookie is readable by JS so
# the SPA can echo it in the X-CSRF-Token header for mutating requests
# (double-submit cookie pattern).
SESSION_COOKIE = "opanel_session"
CSRF_COOKIE = "opanel_csrf"
CSRF_HEADER = "X-CSRF-Token"


# Login rate limiter for /auth/login. Production uses Redis so counters are
# shared across uvicorn workers; development can opt into the in-process backend.
_LOGIN_WINDOW_SECONDS = 60
_LOGIN_MAX_ATTEMPTS = 8
_LOGIN_LOCKOUT_SECONDS = 15 * 60
_LOGIN_LOCKOUT_THRESHOLD = 20
_login_attempts: Dict[str, Deque[float]] = defaultdict(deque)
_login_failures: Dict[str, Deque[float]] = defaultdict(deque)
_login_lockouts: Dict[str, float] = {}
_login_lock = Lock()
_redis_client = None

# Pre-computed dummy bcrypt hash for constant-time fail path.
_DUMMY_HASH = hash_password("not-a-real-password-opanel-dummy")


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _username_key(username: str) -> str:
    name = (username or "").strip().lower()
    return f"user:{name}" if name else "user:_unknown"


def _is_secure_request(request: Request) -> bool:
    """Decide whether to set the Secure cookie flag.

    True when the inbound request was HTTPS. The panel can also be served
    directly over http://IP:2222 during first install, so production mode alone
    must not force Secure cookies.
    """
    forwarded_proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    if forwarded_proto == "https":
        return True
    if request.url.scheme == "https":
        return True
    return False


def _set_session_cookies(response: Response, request: Request, token: str) -> str:
    csrf_token = secrets.token_urlsafe(32)
    secure = _is_secure_request(request)
    max_age = settings.access_token_expire_minutes * 60
    # HttpOnly session cookie: never visible to JS.
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    # CSRF cookie: readable by JS so the SPA can mirror it in a header.
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return csrf_token


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def _rate_limit_backend() -> str:
    return (settings.rate_limit_backend or "memory").lower()


def _redis() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def _rate_limit_key(kind: str, key: str) -> str:
    return f"opanel:login:{kind}:{key}"


def _rate_limit_unavailable(exc: Exception) -> HTTPException:
    # Kept for backward compatibility but no longer raised during login.
    # If Redis is down the rate limiter falls back to the in-memory backend
    # so that a Redis outage never blocks authentication.
    return HTTPException(status_code=503, detail="Login rate limiter is unavailable")


def _log_redis_fallback(exc: Exception) -> None:
    """Log a warning when Redis is unavailable and we fall back to memory."""
    logging.getLogger("opanel.auth").warning(
        "Redis unavailable (%s), falling back to in-memory rate limiter", exc,
    )


def _raise_locked(retry_after: int) -> None:
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many login attempts. Try again later.",
        headers={"Retry-After": str(max(1, retry_after))},
    )


def _redis_enforce_rate_limit(key: str) -> None:
    now = time.time()
    attempts_key = _rate_limit_key("attempts", key)
    lockout_key = _rate_limit_key("lockout", key)
    try:
        client = _redis()
        retry_after = client.ttl(lockout_key)
        if retry_after and retry_after > 0:
            _raise_locked(int(retry_after))
        pipe = client.pipeline()
        pipe.zremrangebyscore(attempts_key, 0, now - _LOGIN_WINDOW_SECONDS)
        pipe.zcard(attempts_key)
        _, attempts_count = pipe.execute()
    except RedisError as exc:
        raise _rate_limit_unavailable(exc) from exc
    if attempts_count >= _LOGIN_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Slow down.",
            headers={"Retry-After": str(_LOGIN_WINDOW_SECONDS)},
        )


def _memory_enforce_rate_limit(key: str) -> None:
    now = time.monotonic()
    with _login_lock:
        locked_until = _login_lockouts.get(key)
        if locked_until and locked_until > now:
            retry_after = int(locked_until - now)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts. Try again later.",
                headers={"Retry-After": str(retry_after)},
            )
        if locked_until and locked_until <= now:
            _login_lockouts.pop(key, None)
        attempts = _login_attempts[key]
        cutoff = now - _LOGIN_WINDOW_SECONDS
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts. Slow down.",
                headers={"Retry-After": str(_LOGIN_WINDOW_SECONDS)},
            )


def _enforce_rate_limit(key: str) -> None:
    if _rate_limit_backend() == "redis":
        try:
            _redis_enforce_rate_limit(key)
            return
        except HTTPException as exc:
            if exc.status_code == 503:
                _log_redis_fallback(exc.detail)
            else:
                raise
    _memory_enforce_rate_limit(key)


def _redis_record_failure(key: str, *, apply_lockout: bool) -> None:
    """Record a login failure for ``key``.

    ``apply_lockout=True`` is what an IP key gets: both the short-window rate
    limit (``attempts_key``) and the long-window hard lockout (``lockout_key``)
    apply, so one source cannot exceed ``_LOGIN_MAX_ATTEMPTS`` per minute and
    repeated failure locks that source out.

    ``apply_lockout=False`` is what a username key gets: the short window is
    recorded, the long lockout is not. The window is only consulted after a
    password has already failed (see login()), so it throttles spraying against
    one account without ever refusing that account's real owner.
    """
    now = time.time()
    member = f"{now}:{secrets.token_hex(8)}"
    attempts_key = _rate_limit_key("attempts", key)
    failures_key = _rate_limit_key("failures", key)
    lockout_key = _rate_limit_key("lockout", key)
    try:
        client = _redis()
        pipe = client.pipeline()
        pipe.zadd(attempts_key, {member: now})
        pipe.expire(attempts_key, _LOGIN_WINDOW_SECONDS)
        if apply_lockout:
            pipe.zremrangebyscore(failures_key, 0, now - _LOGIN_LOCKOUT_SECONDS)
            pipe.zadd(failures_key, {member: now})
            pipe.expire(failures_key, _LOGIN_LOCKOUT_SECONDS)
            pipe.zcard(failures_key)
            *_, failure_count = pipe.execute()
            if failure_count >= _LOGIN_LOCKOUT_THRESHOLD:
                client.set(lockout_key, "1", ex=_LOGIN_LOCKOUT_SECONDS)
        else:
            pipe.execute()
    except RedisError as exc:
        raise _rate_limit_unavailable(exc) from exc


def _memory_record_failure(key: str, *, apply_lockout: bool) -> None:
    now = time.monotonic()
    with _login_lock:
        # The short window is fed for both kinds of key. Where it is *enforced*
        # is what differs: login() consults the account-name key only after a
        # password has already failed, so a correct password is never refused.
        attempts = _login_attempts[key]
        attempts.append(now)
        cutoff = now - _LOGIN_WINDOW_SECONDS
        while attempts and attempts[0] < cutoff:
            attempts.popleft()

        # The long lockout stays source-only. Feeding it from an account name
        # would let wrong passwords from many addresses lock that account out
        # for 15 minutes, which is the whole reason the exemption exists.
        if not apply_lockout:
            return
        failures = _login_failures[key]
        failures.append(now)
        failure_cutoff = now - _LOGIN_LOCKOUT_SECONDS
        while failures and failures[0] < failure_cutoff:
            failures.popleft()
        if len(failures) >= _LOGIN_LOCKOUT_THRESHOLD:
            _login_lockouts[key] = now + _LOGIN_LOCKOUT_SECONDS


def _record_failure(key: str, *, apply_lockout: bool = True) -> None:
    if _rate_limit_backend() == "redis":
        try:
            _redis_record_failure(key, apply_lockout=apply_lockout)
            return
        except HTTPException as exc:
            if exc.status_code == 503:
                _log_redis_fallback(exc.detail)
            else:
                raise
    _memory_record_failure(key, apply_lockout=apply_lockout)


def _redis_record_success(key: str) -> None:
    try:
        _redis().delete(
            _rate_limit_key("attempts", key),
            _rate_limit_key("failures", key),
            _rate_limit_key("lockout", key),
        )
    except RedisError as exc:
        raise _rate_limit_unavailable(exc) from exc


def _record_success(key: str) -> None:
    if _rate_limit_backend() == "redis":
        try:
            _redis_record_success(key)
            return
        except HTTPException as exc:
            if exc.status_code == 503:
                _log_redis_fallback(exc.detail)
            else:
                raise
    with _login_lock:
        _login_attempts.pop(key, None)
        _login_failures.pop(key, None)
        _login_lockouts.pop(key, None)


def _issue_login_session(response: Response, request: Request, user: User) -> str:
    token_extra = {"role": user.role, "tv": user.token_version or 0}
    token = create_access_token(user.username, token_extra)
    _set_session_cookies(response, request, token)
    return token


def _jwt_expiry_from_payload(payload: dict) -> datetime:
    raw_exp = payload.get("exp")
    if isinstance(raw_exp, (int, float)):
        return datetime.utcfromtimestamp(raw_exp)
    return datetime.utcnow()


def _revoke_request_token(db: Session, request: Request, user: User) -> None:
    payload = getattr(request.state, "jwt_payload", {}) or {}
    jti = payload.get("jti")
    if not jti:
        return
    now = datetime.utcnow()
    db.query(RevokedToken).filter(RevokedToken.expires_at <= now).delete()
    if db.query(RevokedToken.id).filter(RevokedToken.jti == jti).first():
        return
    db.add(RevokedToken(jti=jti, user_id=user.id, expires_at=_jwt_expiry_from_payload(payload), revoked_at=now))


def _get_totp_secret(user: User) -> str:
    return decrypt(user.totp_secret or "")


def _verify_totp(user: User, code: str) -> bool:
    return verify_totp(user, code)


def _qr_data_url(uri: str) -> str:
    image = qrcode.make(uri)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


@router.post("/login", response_model=LoginResponse)
def login(
    request: Request,
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
    otp: str = Form(default=""),
    passkey: str = Form(default=""),
    db: Session = Depends(get_db),
):
    # Reject oversize credentials early Ã¢â‚¬â€ protects bcrypt and avoids using a
    # huge string as a rate-limit/lockout key.
    if len(form.username) > _MAX_USERNAME_LEN or len(form.password) > _MAX_PASSWORD_LEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    ip_key = _client_key(request)
    user_key = _username_key(form.username)
    # IP key gets the full lockout: one source, slowed down.
    _enforce_rate_limit(ip_key)
    # The account-name key is deliberately NOT checked here. Enforcing it
    # before the password is what let an attacker hold someone else's account
    # at 429 -- the owner was refused even with the right password. It is
    # checked below, only on the path where the password has already failed.

    user = db.query(User).filter(User.username == form.username).first()
    if user and user.is_active:
        password_ok = verify_password(form.password, user.hashed_password)
    else:
        verify_password(form.password, _DUMMY_HASH)
        password_ok = False

    if not user or not user.is_active or not password_ok:
        _record_failure(ip_key, apply_lockout=True)
        _record_failure(user_key, apply_lockout=False)
        # Only now. A correct password never reaches this branch, so the real
        # owner cannot be locked out, while an attacker guessing against one
        # account is throttled however many addresses they spread across.
        _enforce_rate_limit(user_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    # Second factor. An account may hold a passkey, an authenticator app, or
    # both; when it holds both the passkey is offered first and the code is the
    # way through when the passkey cannot be used -- a borrowed machine, a
    # browser without WebAuthn, a key left at home.
    has_passkey = passkeys.has_passkeys(db, user)

    def _second_factor_failed(detail: str):
        _record_failure(ip_key, apply_lockout=True)
        _record_failure(user_key, apply_lockout=False)
        _enforce_rate_limit(user_key)
        return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)

    if passkey:
        if not has_passkey:
            raise _second_factor_failed("This account has no passkey")
        try:
            assertion = json.loads(passkey)
        except (TypeError, ValueError):
            assertion = None
        if not isinstance(assertion, dict) or not passkeys.complete_login(db, user, assertion):
            raise _second_factor_failed("That passkey could not be verified")
    elif otp:
        # The fallback, and the only path for an account with no passkey.
        if not user.totp_enabled:
            raise _second_factor_failed("This account has no authenticator app")
        if not _verify_totp(user, otp):
            raise _second_factor_failed("Invalid authentication code")
    elif has_passkey:
        try:
            options = passkeys.begin_login(db, user)
        except passkeys.PasskeyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # requires_2fa rides along so the client can offer "use a code
        # instead" without a second round trip to find out whether it exists.
        return LoginResponse(
            requires_passkey=True,
            passkey_options=options,
            requires_2fa=bool(user.totp_enabled),
        )
    elif user.totp_enabled:
        return LoginResponse(requires_2fa=True)

    _record_success(ip_key)
    _record_success(user_key)
    if needs_rehash(user.hashed_password):
        try:
            user.hashed_password = hash_password(form.password)
            db.commit()
        except Exception:  # pragma: no cover
            db.rollback()
    token = _issue_login_session(response, request, user)

    # Bearer token still returned for backward compatibility with CLI tools or
    # mobile clients that cannot set cookies. Browser clients should ignore it
    # and rely on the HttpOnly cookie set above.
    return LoginResponse(access_token=token)


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Invalidate the session by clearing cookies AND bumping token_version.

    The current JWT's jti is stored server-side, and bumping token_version
    forces all other devices/tabs holding a JWT for this user to re-authenticate.
    """
    _revoke_request_token(db, request, current_user)
    current_user.token_version = (current_user.token_version or 0) + 1
    db.commit()
    _clear_session_cookies(response)
    return {"ok": True}


@router.get("/session")
def session_status(
    response: Response,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    if current_user is None:
        _clear_session_cookies(response)
        return {"authenticated": False, "user": None}
    user_data = {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "role": current_user.role,
        "is_active": current_user.is_active,
        "website_limit": current_user.website_limit,
        "storage_limit_mb": current_user.storage_limit_mb,
        "totp_enabled": current_user.totp_enabled,
    }
    user_data.update(storage_quota.storage_usage_summary(db, current_user))
    return {"authenticated": True, "user": user_data}


@router.post("/impersonate/{user_id}", response_model=LoginResponse)
def impersonate_user(
    user_id: int,
    request: Request,
    response: Response,
    otp: str = Form(default=""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Issue a session cookie for ``user_id`` while the caller stays admin.

    Impersonation is a high-trust operation: it bypasses normal auth for the
    target account. We therefore (1) require the admin to re-prove possession
    of their TOTP if 2FA is enabled, and (2) audit-log every successful
    impersonation with the actor and target identities.
    """
    ensure_role(current_user.role, Role.admin)

    # Rate-limit impersonation attempts to prevent enumeration of user IDs.
    _enforce_rate_limit(_client_key(request))
    _enforce_rate_limit(_username_key(current_user.username))

    target_user = db.query(User).filter(User.id == user_id).first()
    if target_user is None:
        raise HTTPException(status_code=404, detail="User not found")
    # Admins may impersonate suspended users for support/troubleshooting.
    # Normal login still rejects inactive users; only impersonation is allowed
    # to bypass the active check here.

    # Re-prompt TOTP for admins that have 2FA. Refusing without a code keeps
    # the feature usable from the SPA (which can pop a modal on 401) while
    # blocking session-stealing attackers who don't have the admin's phone.
    if current_user.totp_enabled:
        if not otp:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Two-factor authentication code required",
            )
        if not _verify_totp(current_user, otp):
            _record_failure(_client_key(request), apply_lockout=True)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication code",
            )

    # Audit log must be written before the session is issued so a DB failure
    # doesn't leave an impersonation succeeded with no audit trail.
    log_action(
        db,
        current_user.id,
        "auth.impersonate",
        target_user.username,
        detail=f"target_user_id={target_user.id} target_role={target_user.role}",
        request=request,
    )
    token = _issue_login_session(response, request, target_user)
    return LoginResponse(access_token=token)


@router.get("/csrf")
def get_csrf(
    request: Request,
    response: Response,
    current_user: User = Depends(get_current_user),
):
    """Return (and refresh) the CSRF cookie for the current session.

    The SPA calls this on bootstrap when the cookie is missing, e.g. after a
    page reload that pre-dates this code change.
    """
    secure = _is_secure_request(request)
    csrf_token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )
    return {"csrf_token": csrf_token}


@router.get("/2fa/status", response_model=TwoFactorStatus)
def two_factor_status(current_user: User = Depends(get_current_user)):
    return TwoFactorStatus(enabled=bool(current_user.totp_enabled))


@router.post("/2fa/setup", response_model=TwoFactorSetup)
def setup_two_factor(
    payload: TwoFactorSetupRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_sensitive_action_step_up(current_user, payload.current_password, payload.code)
    if current_user.totp_enabled:
        raise HTTPException(status_code=400, detail="Two-factor authentication is already enabled")
    # A passkey is no longer a reason to refuse: an account may hold both, and
    # an authenticator code is what gets its owner in when the passkey cannot
    # be used.
    secret = pyotp.random_base32()
    current_user.totp_secret = encrypt(secret)
    db.commit()
    account_name = current_user.email or current_user.username
    uri = pyotp.TOTP(secret).provisioning_uri(name=account_name, issuer_name=settings.totp_issuer)
    return TwoFactorSetup(secret=secret, provisioning_uri=uri, qr_data_url=_qr_data_url(uri))


@router.post("/2fa/enable", response_model=TwoFactorStatus)
def enable_two_factor(
    payload: TwoFactorEnableRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not current_user.totp_secret:
        raise HTTPException(status_code=400, detail="Set up two-factor authentication first")
    if not _verify_totp(current_user, payload.code):
        raise HTTPException(status_code=400, detail="Invalid authentication code")
    if not current_user.totp_enabled:
        current_user.totp_enabled = True
        current_user.token_version = (current_user.token_version or 0) + 1
        db.commit()
        db.refresh(current_user)
    _issue_login_session(response, request, current_user)
    return TwoFactorStatus(enabled=True)


@router.post("/2fa/disable", response_model=TwoFactorStatus)
def disable_two_factor(
    payload: TwoFactorDisableRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    require_sensitive_action_step_up(current_user, payload.current_password, payload.code)
    current_user.totp_enabled = False
    current_user.totp_secret = None
    current_user.token_version = (current_user.token_version or 0) + 1
    db.commit()
    db.refresh(current_user)
    _issue_login_session(response, request, current_user)
    return TwoFactorStatus(enabled=False)


# ---------------------------------------------------------------------------
# Passkeys (WebAuthn)
# ---------------------------------------------------------------------------

def _passkey_status(db: Session, user: User) -> PasskeyStatus:
    available = passkeys.is_available()
    creds = passkeys.credentials_for(db, user) if available else []
    return PasskeyStatus(
        available=available,
        passkeys=[PasskeyOut.model_validate(c) for c in creds],
        totp_enabled=bool(user.totp_enabled),
        # Independent. The two run side by side, and sign-in prefers the
        # passkey with the authenticator code as the fallback.
        can_add_passkey=available,
        can_enable_totp=not user.totp_enabled,
        unavailable_reason=(
            ""
            if available
            else "Set a panel hostname in Panel settings first: a passkey is "
                 "bound to the hostname it was created on."
        ),
    )


@router.get("/passkeys", response_model=PasskeyStatus)
def list_passkeys(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return _passkey_status(db, current_user)


@router.post("/passkeys/register/begin")
def passkey_register_begin(
    payload: PasskeyRegisterBegin,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Options for creating a passkey. Costs a password (and a code if TOTP is
    still on), because adding a factor is as sensitive as removing one."""
    require_sensitive_action_step_up(current_user, payload.current_password, payload.code)
    try:
        return passkeys.begin_registration(db, current_user)
    except passkeys.PasskeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/passkeys/register/complete", response_model=PasskeyStatus)
def passkey_register_complete(
    payload: PasskeyRegisterComplete,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        passkeys.complete_registration(db, current_user, payload.credential, payload.name)
    except passkeys.PasskeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.refresh(current_user)
    # Registration bumps token_version, so this session needs reissuing or the
    # user is logged out by their own success.
    _issue_login_session(response, request, current_user)
    return _passkey_status(db, current_user)


@router.post("/passkeys/{credential_id}/delete", response_model=PasskeyStatus)
def passkey_delete(
    credential_id: int,
    payload: PasskeyDeleteRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Removing a factor is a sensitive action, so it costs a password.

    No TOTP code is asked for here: an account with a passkey has no TOTP, and
    requiring the passkey itself would strand anyone whose key was lost --
    which is the main reason to be on this endpoint.
    """
    require_current_password(current_user, payload.current_password)
    if not passkeys.delete_credential(db, current_user, credential_id):
        raise HTTPException(status_code=404, detail="Passkey not found")
    db.refresh(current_user)
    _issue_login_session(response, request, current_user)
    return _passkey_status(db, current_user)

