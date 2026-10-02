import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import ctypes
import ctypes.util

try:
    import crypt as unix_crypt
except ImportError:  # pragma: no cover - crypt is Unix-only and removed in Python 3.13
    unix_crypt = None

from passlib.exc import UnknownHashError
from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings


# Plain bcrypt. To prevent the 72-byte silent truncation problem (see
# security audit #8) we:
#   1. Cap password length at 72 bytes in the Pydantic schemas (bcrypt 72-byte
#      limit), so users can never set a password whose meaningful prefix is
#      truncated.
#   2. Set ``truncate_error=True`` so passlib raises rather than silently
#      truncating if anything bypasses the schema.
#
# We tried bcrypt_sha256 (which pre-hashes with SHA-256 to bypass the limit)
# but the passlib<->bcrypt 4.x compatibility issue makes that path unreliable
# in production. Plain bcrypt with the input length cap is the simplest fix.
pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__truncate_error=True,
)
ALGORITHM = "HS256"
SHADOW_HASH_PREFIXES = ("$y$", "$gy$", "$7$", "$6$", "$5$")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def is_shadow_password_hash(hashed_password: str) -> bool:
    return bool(hashed_password) and hashed_password.startswith(SHADOW_HASH_PREFIXES)


# libxcrypt's crypt_rn: the library the crypt module wrapped. Python 3.13
# removed that module, and Ubuntu 26.04 ships Python 3.14, so without this a
# root-synced admin password ($y$ yescrypt from /etc/shadow) could not log in.
CRYPT_DATA_SIZE = 32768  # sizeof(struct crypt_data) in libxcrypt


def _load_crypt_rn():
    try:
        library = ctypes.CDLL(ctypes.util.find_library("crypt") or "libcrypt.so.1")
        crypt_rn = library.crypt_rn
    except (OSError, AttributeError):
        return None
    crypt_rn.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_int]
    crypt_rn.restype = ctypes.c_char_p
    return crypt_rn


_crypt_rn = _load_crypt_rn()


def _crypt(password: str, setting: str) -> Optional[str]:
    if unix_crypt is not None:
        return unix_crypt.crypt(password, setting)
    if _crypt_rn is None:
        return None
    data = ctypes.create_string_buffer(CRYPT_DATA_SIZE)
    result = _crypt_rn(password.encode("utf-8"), setting.encode("utf-8"), data, CRYPT_DATA_SIZE)
    return result.decode("utf-8") if result else None


def verify_shadow_password(password: str, hashed_password: str) -> bool:
    if not is_shadow_password_hash(hashed_password):
        return False
    try:
        candidate = _crypt(password, hashed_password)
    except (OSError, ValueError, TypeError, UnicodeError):
        return False
    return bool(candidate) and secrets.compare_digest(candidate, hashed_password)


def verify_password(password: str, hashed_password: str) -> bool:
    if is_shadow_password_hash(hashed_password):
        return verify_shadow_password(password, hashed_password)
    try:
        return pwd_context.verify(password, hashed_password)
    except (UnknownHashError, ValueError):
        # truncate_error=True raises ValueError when the password is too long.
        return False


def needs_rehash(hashed_password: str) -> bool:
    if is_shadow_password_hash(hashed_password):
        return False
    try:
        return pwd_context.needs_update(hashed_password)
    except UnknownHashError:
        return False


def create_access_token(subject: str, extra: Optional[Dict[str, Any]] = None) -> str:
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.access_token_expire_minutes)
    payload: Dict[str, Any] = {"sub": subject, "exp": expire, "iat": now, "jti": secrets.token_urlsafe(32)}
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)
