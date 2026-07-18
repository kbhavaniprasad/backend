"""
core/security.py — Password hashing and JWT token utilities.

All cryptographic operations are centralised here so that the algorithm,
secret keys, and expiry logic live in exactly one place.

Token structure
---------------
Access tokens
    Payload: sub (user_id), tenant_id, role, type="access", exp, iat
Refresh tokens
    Payload: sub (user_id), type="refresh", jti (random UUID), exp, iat
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import Settings

# --------------------------------------------------------------------------- #
# Password hashing                                                             #
# --------------------------------------------------------------------------- #

PASSWORD_CONTEXT = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",  # Automatically re-hash old schemes on verify
)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain-text password against a stored bcrypt hash.

    Parameters
    ----------
    plain_password:
        The raw password supplied by the user at login time.
    hashed_password:
        The bcrypt hash stored in the database.

    Returns
    -------
    bool
        ``True`` if the password matches; ``False`` otherwise.
    """
    return PASSWORD_CONTEXT.verify(plain_password, hashed_password)


def hash_password(password: str) -> str:
    """
    Return the bcrypt hash of *password*.

    Parameters
    ----------
    password:
        Raw plain-text password to hash.

    Returns
    -------
    str
        bcrypt hash suitable for storing in the database.
    """
    return PASSWORD_CONTEXT.hash(password)


# --------------------------------------------------------------------------- #
# JWT helpers                                                                  #
# --------------------------------------------------------------------------- #

_ALGORITHM = "HS256"


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def create_access_token(data: dict[str, Any], settings: Settings) -> str:
    """
    Create a signed JWT access token.

    Parameters
    ----------
    data:
        Arbitrary claims to embed (e.g. ``{"sub": str(user_id), "role": ...}``).
        ``type``, ``iat``, and ``exp`` are added automatically.
    settings:
        Application settings providing the secret key and expiry duration.

    Returns
    -------
    str
        Signed JWT string.
    """
    now = _utcnow()
    payload = {
        **data,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def create_refresh_token(data: dict[str, Any], settings: Settings) -> str:
    """
    Create a signed JWT refresh token.

    A unique ``jti`` (JWT ID) is generated per token so that individual tokens
    can be revoked by deleting the corresponding ``RefreshToken`` DB record.

    Parameters
    ----------
    data:
        Base claims (typically just ``{"sub": str(user_id)}``).
    settings:
        Application settings providing the refresh secret and expiry duration.

    Returns
    -------
    str
        Signed JWT string.
    """
    now = _utcnow()
    payload = {
        **data,
        "type": "refresh",
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + timedelta(days=settings.jwt_refresh_expire_days),
    }
    return jwt.encode(payload, settings.jwt_refresh_secret, algorithm=_ALGORITHM)


def decode_token(token: str, settings: Settings, *, is_refresh: bool = False) -> dict[str, Any] | None:
    """
    Decode and verify a JWT token.

    Parameters
    ----------
    token:
        Raw JWT string to decode.
    settings:
        Application settings providing the correct secret key.
    is_refresh:
        When ``True`` the refresh secret is used; otherwise the access secret.

    Returns
    -------
    dict | None
        Decoded payload dict if valid; ``None`` if the token is expired,
        tampered with, or otherwise invalid.
    """
    secret = settings.jwt_refresh_secret if is_refresh else settings.jwt_secret
    try:
        return jwt.decode(token, secret, algorithms=[_ALGORITHM])
    except JWTError:
        return None
