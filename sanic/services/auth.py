"""
Authentication service for user management.

Handles password hashing, access token generation and validation,
refresh-session lifecycle management, and user CRUD operations.
"""

import hashlib
import os
import secrets
import uuid
import bcrypt
import jwt
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
import services.postgres as postgres_client
import services.redis as redis_client


logger = logging.getLogger(__name__)


AUTH_ERROR_INVALID_CREDENTIALS = "invalid_credentials"
AUTH_ERROR_INVALID_REFRESH_TOKEN = "invalid_refresh_token"
AUTH_ERROR_USERNAME_EXISTS = "username_exists"
AUTH_ERROR_INTERNAL = "internal_error"


# JWT Configuration
JWT_SECRET_KEY = "123"  # os.getenv("JWT_SECRET_KEY", "")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRATION_SECONDS = int(
    os.getenv("ACCESS_TOKEN_EXPIRATION_SECONDS", "900")
)
REFRESH_TOKEN_EXPIRATION_SECONDS = int(
    os.getenv("REFRESH_TOKEN_EXPIRATION_SECONDS", "2592000")
)
TOKEN_TYPE = "Bearer"

# Validate JWT secret key on import
if not JWT_SECRET_KEY:
    logger.error(
        "JWT_SECRET_KEY is not set! Refusing to start without a secure JWT secret key."
    )
    raise RuntimeError(
        "JWT_SECRET_KEY environment variable is required for authentication service."
    )


def serialize_datetime(value):
    """
    Convert datetime objects to an ISO 8601 string for JSON serialization.
    If the value is not a datetime instance, it is returned unchanged.
    """
    if isinstance(value, datetime):
        # Ensure we have a timezone-aware datetime; default to UTC if naive
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return value


def serialize_user(user: dict) -> dict:
    """Build the public user payload returned to clients."""
    return {
        "id": user["id"],
        "username": user["username"],
        "created_at": serialize_datetime(user["created_at"]),
    }


def hash_password(password: str) -> str:
    """
    Hash a password using bcrypt.

    Args:
        password: The plain text password to hash

    Returns:
        The hashed password string
    """
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """
    Verify a plain text password against a bcrypt hash.

    Args:
        password: The plain text password to verify
        password_hash: The bcrypt hash to verify against

    Returns:
        True if password matches, False otherwise
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


def hash_refresh_token(refresh_token: str) -> str:
    """Hash a refresh token before persisting it."""
    return hashlib.sha256(refresh_token.encode("utf-8")).hexdigest()


def generate_refresh_token() -> str:
    """Generate a high-entropy refresh token for mixed clients."""
    return secrets.token_urlsafe(48)


def get_refresh_token_expiry() -> datetime:
    """Return the expiry timestamp for a refresh token session."""
    return datetime.now(timezone.utc) + timedelta(
        seconds=REFRESH_TOKEN_EXPIRATION_SECONDS
    )


def generate_jwt_token(
    user_id: int, username: str, session_id: str, auth_version: int
) -> str:
    """
    Generate a short-lived access token for a user session.

    Args:
        user_id: The user's ID
        username: The user's username
        session_id: The persistent auth session id backing the token
        auth_version: The current auth version for the user

    Returns:
        The signed JWT token string
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ACCESS_TOKEN_EXPIRATION_SECONDS)

    payload = {
        "type": "access",
        "user_id": user_id,
        "username": username,
        "session_id": session_id,
        "auth_version": int(auth_version),
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "jti": uuid.uuid4().hex,
    }

    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return token


def verify_jwt_token(token: str) -> Optional[dict]:
    """
    Verify an access token and return the payload.

    Args:
        token: The JWT token to verify

    Returns:
        The decoded token payload if valid, None if invalid or expired
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        if payload.get("type") != "access":
            return None
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
    except Exception:
        return None


def _normalize_datetime(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        try:
            parsed_value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed_value.tzinfo is None:
                parsed_value = parsed_value.replace(tzinfo=timezone.utc)
            return parsed_value
        except ValueError:
            return None
    return None


def is_auth_session_active(session: Optional[dict]) -> bool:
    """Return whether a persisted auth session is active."""
    if not session:
        return False
    if session.get("revoked_at"):
        return False

    expires_at = _normalize_datetime(session.get("expires_at"))
    if not expires_at:
        return False

    return expires_at > datetime.now(timezone.utc)


async def _async_get_user_auth_version(user_id: int) -> Optional[int]:
    cached_version = await redis_client.async_get_cached_user_auth_version(user_id)
    if cached_version is not None:
        return cached_version

    auth_version = await postgres_client.async_get_user_auth_version(user_id)
    if auth_version is not None:
        await redis_client.async_cache_user_auth_version(user_id, auth_version)
    return auth_version


async def _async_get_auth_session(session_id: str) -> Optional[dict]:
    cached_session = await redis_client.async_get_cached_auth_session(session_id)
    if cached_session is not None:
        return cached_session

    session = await postgres_client.async_get_auth_session(session_id)
    if session and is_auth_session_active(session):
        await redis_client.async_cache_auth_session(session_id, session)
    elif session is not None:
        await redis_client.async_clear_cached_auth_session(session_id)
    return session


async def async_validate_access_token(token: str) -> Optional[dict]:
    """Validate the access token using async Redis lookups (non-blocking)."""
    payload = verify_jwt_token(token)
    if not payload:
        return None

    try:
        user_id = int(payload.get("user_id"))
        auth_version = int(payload.get("auth_version"))
    except (TypeError, ValueError):
        return None

    session_id = payload.get("session_id")
    if not session_id:
        return None

    current_auth_version = await _async_get_user_auth_version(user_id)
    if current_auth_version is None or int(current_auth_version) != auth_version:
        return None

    session = await _async_get_auth_session(str(session_id))
    if not is_auth_session_active(session):
        await redis_client.async_clear_cached_auth_session(str(session_id))
        return None

    try:
        session_user_id = int(session.get("user_id"))
        session_auth_version = int(session.get("auth_version"))
    except (TypeError, ValueError):
        return None

    if session_user_id != user_id or session_auth_version != auth_version:
        return None

    return payload


def _build_token_response(
    user_id: int,
    username: str,
    session_id: str,
    auth_version: int,
    refresh_token: Optional[str] = None,
    user: Optional[dict] = None,
    message: Optional[str] = None,
) -> dict:
    response_data = {
        "access_token": generate_jwt_token(
            user_id=user_id,
            username=username,
            session_id=session_id,
            auth_version=auth_version,
        ),
        "token_type": TOKEN_TYPE,
        "expires_in": ACCESS_TOKEN_EXPIRATION_SECONDS,
    }

    if refresh_token is not None:
        response_data["refresh_token"] = refresh_token
        response_data["refresh_expires_in"] = REFRESH_TOKEN_EXPIRATION_SECONDS
    if user is not None:
        response_data["user"] = user
    if message is not None:
        response_data["message"] = message

    return response_data


async def _async_create_session(
    user_id: int,
    auth_version: int,
    created_ip: Optional[str],
    created_user_agent: Optional[str],
) -> Tuple[bool, Optional[dict], str]:
    session_id = uuid.uuid4().hex
    refresh_token = generate_refresh_token()
    refresh_token_hash = hash_refresh_token(refresh_token)
    expires_at = get_refresh_token_expiry()

    session = await postgres_client.async_create_auth_session(
        session_id=session_id,
        user_id=user_id,
        refresh_token_hash=refresh_token_hash,
        auth_version=auth_version,
        expires_at=expires_at,
        created_ip=created_ip,
        created_user_agent=created_user_agent,
    )
    if not session:
        return False, None, AUTH_ERROR_INTERNAL

    await redis_client.async_cache_user_auth_version(user_id, auth_version)
    await redis_client.async_cache_auth_session(session_id, session)

    return (
        True,
        {
            "session_id": session_id,
            "refresh_token": refresh_token,
            "auth_version": auth_version,
        },
        "",
    )


async def async_register_user(
    username: str,
    password: str,
    created_ip: Optional[str] = None,
    created_user_agent: Optional[str] = None,
) -> Tuple[bool, Optional[dict], str]:
    """Register a new user (async)."""
    try:
        existing_user = await postgres_client.async_get_user_by_username(username)
        if existing_user:
            return False, None, AUTH_ERROR_USERNAME_EXISTS

        password_hash = hash_password(password)
        session_id = uuid.uuid4().hex
        refresh_token = generate_refresh_token()
        refresh_token_hash = hash_refresh_token(refresh_token)
        expires_at = get_refresh_token_expiry()

        registration_data = (
            await postgres_client.async_create_user_with_settings_and_auth_session(
                username=username,
                password_hash=password_hash,
                session_id=session_id,
                refresh_token_hash=refresh_token_hash,
                expires_at=expires_at,
                created_ip=created_ip,
                created_user_agent=created_user_agent,
            )
        )
        if not registration_data:
            logger.error(
                "Registration failed: user/session transaction returned no data"
            )
            return False, None, AUTH_ERROR_INTERNAL

        user = registration_data["user"]
        session = registration_data["session"]

        await redis_client.async_cache_user_auth_version(
            int(user["id"]), int(user["auth_version"])
        )
        await redis_client.async_cache_auth_session(str(session["session_id"]), session)

        return (
            True,
            _build_token_response(
                user_id=user["id"],
                username=user["username"],
                session_id=str(session["session_id"]),
                auth_version=int(user["auth_version"]),
                refresh_token=refresh_token,
                user=serialize_user(user),
            ),
            "",
        )

    except postgres_client.UsernameAlreadyExistsError:
        return False, None, AUTH_ERROR_USERNAME_EXISTS
    except Exception:
        logger.exception("Async registration failed")
        return False, None, AUTH_ERROR_INTERNAL


async def async_login_user(
    username: str,
    password: str,
    created_ip: Optional[str] = None,
    created_user_agent: Optional[str] = None,
) -> Tuple[bool, Optional[dict], str]:
    """Authenticate a user and return a JWT token (async)."""
    try:
        user = await postgres_client.async_get_user_by_username(username)

        if not user:
            return False, None, AUTH_ERROR_INVALID_CREDENTIALS

        if not verify_password(password, user["password_hash"]):
            return False, None, AUTH_ERROR_INVALID_CREDENTIALS

        session_created, session_data, session_error = await _async_create_session(
            user_id=user["id"],
            auth_version=int(user["auth_version"]),
            created_ip=created_ip,
            created_user_agent=created_user_agent,
        )
        if not session_created:
            logger.error(
                "Login failed: unable to create auth session (%s)", session_error
            )
            return False, None, AUTH_ERROR_INTERNAL

        return (
            True,
            _build_token_response(
                user_id=user["id"],
                username=user["username"],
                session_id=session_data["session_id"],
                auth_version=session_data["auth_version"],
                refresh_token=session_data["refresh_token"],
                user=serialize_user(user),
            ),
            "",
        )

    except Exception:
        logger.exception("Async login failed")
        return False, None, AUTH_ERROR_INTERNAL


async def async_refresh_session(
    refresh_token: str,
) -> Tuple[bool, Optional[dict], str]:
    """Rotate a refresh token and return a fresh token pair (async)."""
    try:
        refresh_token_hash = hash_refresh_token(refresh_token)
        session = await postgres_client.async_get_auth_session_by_refresh_token_hash(
            refresh_token_hash
        )
        if not is_auth_session_active(session):
            if session:
                await redis_client.async_clear_cached_auth_session(
                    str(session["session_id"])
                )
            return False, None, AUTH_ERROR_INVALID_REFRESH_TOKEN

        user = await postgres_client.async_get_user_by_id(int(session["user_id"]))
        if not user:
            logger.error("Refresh failed: session references a missing user")
            return False, None, AUTH_ERROR_INTERNAL

        current_auth_version = await _async_get_user_auth_version(int(user["id"]))
        if current_auth_version is None:
            logger.error("Refresh failed: could not resolve current auth version")
            return False, None, AUTH_ERROR_INTERNAL

        if int(current_auth_version) != int(session["auth_version"]):
            await redis_client.async_clear_cached_auth_session(
                str(session["session_id"])
            )
            return False, None, AUTH_ERROR_INVALID_REFRESH_TOKEN

        new_refresh_token = generate_refresh_token()
        rotated_session = await postgres_client.async_rotate_auth_session_refresh_token(
            session_id=str(session["session_id"]),
            current_refresh_token_hash=refresh_token_hash,
            refresh_token_hash=hash_refresh_token(new_refresh_token),
            expires_at=get_refresh_token_expiry(),
        )
        if not rotated_session:
            await redis_client.async_clear_cached_auth_session(
                str(session["session_id"])
            )
            return False, None, AUTH_ERROR_INVALID_REFRESH_TOKEN

        await redis_client.async_cache_user_auth_version(
            int(user["id"]), int(current_auth_version)
        )
        await redis_client.async_cache_auth_session(
            str(session["session_id"]), rotated_session
        )

        return (
            True,
            _build_token_response(
                user_id=int(user["id"]),
                username=user["username"],
                session_id=str(session["session_id"]),
                auth_version=int(current_auth_version),
                refresh_token=new_refresh_token,
            ),
            "",
        )

    except Exception:
        logger.exception("Async refresh session failed")
        return False, None, AUTH_ERROR_INTERNAL


async def async_logout_session(session_id: str) -> bool:
    """Revoke a single auth session (async)."""
    success = await postgres_client.async_revoke_auth_session(session_id, "logout")
    await redis_client.async_clear_cached_auth_session(session_id)
    return success


async def async_change_password(
    user_id: int,
    old_password: str,
    new_password: str,
    username: str,
    created_ip: Optional[str] = None,
    created_user_agent: Optional[str] = None,
) -> Tuple[bool, Optional[dict], str]:
    """Change a user's password (async)."""
    try:
        user = await postgres_client.async_get_user_by_id(user_id)

        if not user:
            return False, None, "User not found"

        if not verify_password(old_password, user["password_hash"]):
            return False, None, "Current password is incorrect"

        if new_password == username:
            return False, None, "Password cannot be the same as username"

        if verify_password(new_password, user["password_hash"]):
            return False, None, "New password must be different from current password"

        new_password_hash = hash_password(new_password)

        new_session_id = uuid.uuid4().hex
        new_refresh_token = generate_refresh_token()
        expires_at = get_refresh_token_expiry()

        session = await postgres_client.async_change_password_and_create_session(
            user_id=user_id,
            password_hash=new_password_hash,
            session_id=new_session_id,
            refresh_token_hash=hash_refresh_token(new_refresh_token),
            expires_at=expires_at,
            created_ip=created_ip,
            created_user_agent=created_user_agent,
        )
        if not session:
            return False, None, "Failed to update password"

        revoked_session_ids = session.get("revoked_session_ids", [])
        await redis_client.async_clear_cached_auth_sessions(revoked_session_ids)
        await redis_client.async_cache_user_auth_version(
            user_id, int(session["auth_version"])
        )
        await redis_client.async_cache_auth_session(new_session_id, session)

        return (
            True,
            _build_token_response(
                user_id=user_id,
                username=username,
                session_id=new_session_id,
                auth_version=int(session["auth_version"]),
                refresh_token=new_refresh_token,
                message="Password changed successfully",
            ),
            "",
        )

    except Exception:
        logger.exception("Async password change failed")
        return False, None, "Password change failed"


async def async_get_user_by_id(user_id: int) -> Optional[dict]:
    """Get a user by their ID (async)."""
    try:
        user = await postgres_client.async_get_user_by_id(user_id)
        if not user:
            return None
        return serialize_user(user)
    except Exception:
        return None
