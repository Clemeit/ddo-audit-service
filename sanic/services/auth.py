"""
Authentication service for user management.

Handles password hashing, access token generation and validation,
refresh-session lifecycle management, and user CRUD operations.
"""

import asyncio
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
AUTH_ERROR_USER_NOT_FOUND = "user_not_found"
AUTH_ERROR_PASSWORD_REQUIRED = "password_required"
AUTH_ERROR_INTERNAL = "internal_error"


# JWT Configuration
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRATION_SECONDS = int(
    os.getenv("ACCESS_TOKEN_EXPIRATION_SECONDS", "900")
)
REFRESH_TOKEN_EXPIRATION_SECONDS = int(
    os.getenv("REFRESH_TOKEN_EXPIRATION_SECONDS", "2592000")
)
# Hard ceiling on a session's age, however often it is refreshed (OWASP absolute timeout).
SESSION_ABSOLUTE_LIFETIME_SECONDS = int(
    os.getenv("SESSION_ABSOLUTE_LIFETIME_SECONDS", str(90 * 24 * 3600))
)
# How long a just-rotated-out refresh token is still honoured (concurrent tabs, retried
# requests whose response was lost). Seen after this window, it counts as token reuse.
REFRESH_REUSE_GRACE_SECONDS = int(os.getenv("REFRESH_REUSE_GRACE_SECONDS", "60"))
TOKEN_TYPE = "Bearer"

# Why a refresh was refused; returned to clients so they can explain the logout.
REFRESH_FAILURE_INVALID = "invalid"
REFRESH_FAILURE_EXPIRED = "session_expired"
REFRESH_FAILURE_REVOKED = "session_revoked"
REVOKE_REASON_REFRESH_REUSE = "refresh_token_reuse"

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


# bcrypt at cost 12 takes ~250ms of CPU. Run it in a worker thread so a login doesn't
# freeze every other request (and SSE stream) on this event loop.
async def async_hash_password(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


async def async_verify_password(password: str, password_hash: str) -> bool:
    return await asyncio.to_thread(verify_password, password, password_hash)


_dummy_password_hash: Optional[str] = None


async def _async_burn_password_check(password: str) -> None:
    """Spend the same bcrypt time as a real check, so response timing doesn't reveal
    whether a username exists."""
    global _dummy_password_hash
    if _dummy_password_hash is None:
        _dummy_password_hash = await async_hash_password(secrets.token_urlsafe(16))
    await async_verify_password(password, _dummy_password_hash)


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

        password_hash = await async_hash_password(password)
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
            await _async_burn_password_check(password)
            return False, None, AUTH_ERROR_INVALID_CREDENTIALS

        if not await async_verify_password(password, user["password_hash"]):
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


def _refresh_failure(reason: str) -> Tuple[bool, dict, str]:
    return False, {"reason": reason}, AUTH_ERROR_INVALID_REFRESH_TOKEN


def _session_failure_reason(session: Optional[dict]) -> Optional[str]:
    """Why a persisted session can't be refreshed, or None if it is active."""
    if not session:
        return REFRESH_FAILURE_INVALID
    if session.get("revoked_at"):
        return REFRESH_FAILURE_REVOKED
    if not is_auth_session_active(session):
        return REFRESH_FAILURE_EXPIRED
    return None


def get_capped_refresh_expiry(session: dict) -> datetime:
    """Sliding refresh expiry, never past the session's absolute lifetime."""
    sliding_expiry = get_refresh_token_expiry()
    created_at = _normalize_datetime(session.get("created_at"))
    if created_at is None:
        return sliding_expiry
    absolute_expiry = created_at + timedelta(seconds=SESSION_ABSOLUTE_LIFETIME_SECONDS)
    return min(sliding_expiry, absolute_expiry)


async def _async_load_refresh_identity(session: dict):
    """Resolve (user, auth_version) for a session being refreshed.

    Returns a failure tuple instead when the user is gone (internal error) or the
    session predates a password change (revoked).
    """
    user = await postgres_client.async_get_user_by_id(int(session["user_id"]))
    if not user:
        logger.error("Refresh failed: session references a missing user")
        return None, (False, None, AUTH_ERROR_INTERNAL)

    current_auth_version = await _async_get_user_auth_version(int(user["id"]))
    if current_auth_version is None:
        logger.error("Refresh failed: could not resolve current auth version")
        return None, (False, None, AUTH_ERROR_INTERNAL)

    if int(current_auth_version) != int(session["auth_version"]):
        await redis_client.async_clear_cached_auth_session(str(session["session_id"]))
        return None, _refresh_failure(REFRESH_FAILURE_REVOKED)

    return (user, int(current_auth_version)), None


async def _async_refresh_with_previous_token(session: dict):
    """Handle a refresh that presents the token rotated out most recently.

    Inside the grace window this is a concurrent tab or a retry whose response was lost:
    answer with an access token and leave the browser's (newer) cookie alone. Outside it,
    a rotated-out token showing up again means it may have been copied, so the whole
    session is revoked (RFC 9700 section 4.14.2).
    """
    session_id = str(session["session_id"])
    failure = _session_failure_reason(session)
    if failure:
        await redis_client.async_clear_cached_auth_session(session_id)
        return _refresh_failure(failure)

    rotated_at = _normalize_datetime(session.get("refresh_rotated_at"))
    now = datetime.now(timezone.utc)
    if rotated_at is None or (now - rotated_at).total_seconds() > (
        REFRESH_REUSE_GRACE_SECONDS
    ):
        logger.warning(
            "Refresh token reuse detected; revoking session %s for user %s",
            session_id,
            session.get("user_id"),
        )
        await postgres_client.async_revoke_auth_session(
            session_id, REVOKE_REASON_REFRESH_REUSE
        )
        await redis_client.async_clear_cached_auth_session(session_id)
        return _refresh_failure(REFRESH_FAILURE_REVOKED)

    identity, identity_failure = await _async_load_refresh_identity(session)
    if identity_failure:
        return identity_failure
    user, auth_version = identity

    # No refresh_token in the result: the endpoint must not touch the cookie.
    return (
        True,
        _build_token_response(
            user_id=int(user["id"]),
            username=user["username"],
            session_id=session_id,
            auth_version=auth_version,
        ),
        "",
    )


async def async_refresh_session(
    refresh_token: str,
) -> Tuple[bool, Optional[dict], str]:
    """Rotate a refresh token and return a fresh token pair (async).

    Returns (success, data, error). On success ``data`` holds the access token and, when
    the refresh token was rotated, ``refresh_token`` + ``refresh_expires_in`` for the
    cookie. On AUTH_ERROR_INVALID_REFRESH_TOKEN, ``data["reason"]`` is one of the
    REFRESH_FAILURE_* codes. Database errors yield AUTH_ERROR_INTERNAL (never "invalid"),
    so an outage can't log anyone out.
    """
    try:
        refresh_token_hash = hash_refresh_token(refresh_token)
        session = await postgres_client.async_get_auth_session_by_refresh_token_hash(
            refresh_token_hash
        )
        if not session:
            return _refresh_failure(REFRESH_FAILURE_INVALID)

        session_id = str(session["session_id"])
        if session.get("matched", "current") == "previous":
            return await _async_refresh_with_previous_token(session)

        failure = _session_failure_reason(session)
        if failure:
            await redis_client.async_clear_cached_auth_session(session_id)
            return _refresh_failure(failure)

        identity, identity_failure = await _async_load_refresh_identity(session)
        if identity_failure:
            return identity_failure
        user, current_auth_version = identity

        new_expires_at = get_capped_refresh_expiry(session)
        if new_expires_at <= datetime.now(timezone.utc):
            # Past the absolute lifetime; the user has to log in again.
            await redis_client.async_clear_cached_auth_session(session_id)
            return _refresh_failure(REFRESH_FAILURE_EXPIRED)

        new_refresh_token = generate_refresh_token()
        rotated_session = await postgres_client.async_rotate_auth_session_refresh_token(
            session_id=session_id,
            current_refresh_token_hash=refresh_token_hash,
            refresh_token_hash=hash_refresh_token(new_refresh_token),
            expires_at=new_expires_at,
        )
        if not rotated_session:
            # Lost the compare-and-set: another request rotated this token a moment ago
            # (or the session was revoked meanwhile). Judge it as a previous-token holder.
            latest = await postgres_client.async_get_auth_session(
                session_id, raise_on_error=True
            )
            if latest and latest.get("previous_refresh_token_hash") == (
                refresh_token_hash
            ):
                return await _async_refresh_with_previous_token(latest)
            await redis_client.async_clear_cached_auth_session(session_id)
            return _refresh_failure(
                _session_failure_reason(latest) or REFRESH_FAILURE_INVALID
            )

        await redis_client.async_cache_user_auth_version(
            int(user["id"]), current_auth_version
        )
        await redis_client.async_cache_auth_session(session_id, rotated_session)

        token_data = _build_token_response(
            user_id=int(user["id"]),
            username=user["username"],
            session_id=session_id,
            auth_version=current_auth_version,
            refresh_token=new_refresh_token,
        )
        remaining = int(
            (new_expires_at - datetime.now(timezone.utc)).total_seconds()
        )
        token_data["refresh_expires_in"] = max(1, remaining)
        return True, token_data, ""

    except Exception:
        logger.exception("Async refresh session failed")
        return False, None, AUTH_ERROR_INTERNAL


async def async_logout_session(session_id: str) -> bool:
    """Revoke a single auth session (async)."""
    success = await postgres_client.async_revoke_auth_session(session_id, "logout")
    await redis_client.async_clear_cached_auth_session(session_id)
    return success


AUTH_SESSION_PURGE_INTERVAL_SECONDS = int(
    os.getenv("AUTH_SESSION_PURGE_INTERVAL_SECONDS", "3600")
)
# Dead sessions are kept this long first, for support/debugging of recent logouts.
AUTH_SESSION_PURGE_RETENTION_SECONDS = int(
    os.getenv("AUTH_SESSION_PURGE_RETENTION_SECONDS", str(7 * 24 * 3600))
)
_AUTH_SESSION_PURGE_LOCK_KEY = "lock:auth_session_purge"


async def async_purge_dead_auth_sessions_once() -> Optional[int]:
    """Purge long-expired/revoked sessions if no other worker did so this interval.

    Returns rows deleted, or None when skipped (lock held elsewhere) or failed.
    """
    try:
        client = await redis_client.get_async_redis_client()
        acquired = await client.set(
            _AUTH_SESSION_PURGE_LOCK_KEY,
            "1",
            nx=True,
            ex=max(60, AUTH_SESSION_PURGE_INTERVAL_SECONDS - 60),
        )
        if not acquired:
            return None
    except Exception:
        logger.warning("Auth session purge skipped: Redis lock unavailable")
        return None

    deleted = await postgres_client.async_purge_dead_auth_sessions(
        AUTH_SESSION_PURGE_RETENTION_SECONDS
    )
    if deleted:
        logger.info("Purged %s dead auth sessions", deleted)
    return deleted


async def async_run_auth_session_purge_loop() -> None:
    """Background task: purge dead sessions once per interval, forever."""
    while True:
        try:
            await async_purge_dead_auth_sessions_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Auth session purge failed")
        await asyncio.sleep(AUTH_SESSION_PURGE_INTERVAL_SECONDS)


async def async_logout_by_refresh_token(refresh_token: str) -> Optional[bool]:
    """Revoke the session holding this refresh token (current or just-rotated-out).

    Lets logout work from the HttpOnly cookie alone, even when the access token has
    already expired. Returns True when a session was revoked, False when the token
    matches no session (already logged out), None on a database error.
    """
    try:
        session = await postgres_client.async_get_auth_session_by_refresh_token_hash(
            hash_refresh_token(refresh_token)
        )
    except Exception:
        logger.exception("Logout lookup by refresh token failed")
        return None
    if not session:
        return False
    if not await async_logout_session(str(session["session_id"])):
        return None
    return True


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

        if not await async_verify_password(old_password, user["password_hash"]):
            return False, None, "Current password is incorrect"

        if new_password == username:
            return False, None, "Password cannot be the same as username"

        if await async_verify_password(new_password, user["password_hash"]):
            return False, None, "New password must be different from current password"

        new_password_hash = await async_hash_password(new_password)

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


async def async_delete_user_account(
    user_id: int,
    password: Optional[str] = None,
    require_password: bool = False,
) -> Tuple[bool, str]:
    """Delete a user account and all auth/settings rows tied to it (async).

    When ``password`` is given it must match the account's current password
    (re-authentication for a destructive action). ``require_password`` refuses the
    deletion outright when no password is supplied.
    """
    try:
        if password is None and require_password:
            return False, AUTH_ERROR_PASSWORD_REQUIRED
        if password is not None:
            user = await postgres_client.async_get_user_by_id(user_id)
            if not user:
                return False, AUTH_ERROR_USER_NOT_FOUND
            if not await async_verify_password(password, user["password_hash"]):
                return False, AUTH_ERROR_INVALID_CREDENTIALS

        deletion_result = await postgres_client.async_delete_user_account(user_id)
        if deletion_result is None:
            return False, AUTH_ERROR_INTERNAL
        if not deletion_result.get("deleted"):
            return False, AUTH_ERROR_USER_NOT_FOUND

        deleted_session_ids = deletion_result.get("session_ids", [])
        if not isinstance(deleted_session_ids, list):
            deleted_session_ids = []

        await redis_client.async_clear_cached_auth_sessions(deleted_session_ids)
        await redis_client.async_clear_cached_user_auth_version(user_id)

        return True, ""
    except Exception:
        logger.exception("Async account deletion failed")
        return False, AUTH_ERROR_INTERNAL
