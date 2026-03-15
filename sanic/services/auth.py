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


# JWT Configuration
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
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


def _get_user_auth_version(user_id: int) -> Optional[int]:
    cached_version = redis_client.get_cached_user_auth_version(user_id)
    if cached_version is not None:
        return cached_version

    auth_version = postgres_client.get_user_auth_version(user_id)
    if auth_version is not None:
        redis_client.cache_user_auth_version(user_id, auth_version)
    return auth_version


def _get_auth_session(session_id: str) -> Optional[dict]:
    cached_session = redis_client.get_cached_auth_session(session_id)
    if cached_session is not None:
        return cached_session

    session = postgres_client.get_auth_session(session_id)
    if session and is_auth_session_active(session):
        redis_client.cache_auth_session(session_id, session)
    elif session is not None:
        redis_client.clear_cached_auth_session(session_id)
    return session


def validate_access_token(token: str) -> Optional[dict]:
    """Validate the access token against the current user and session state."""
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

    current_auth_version = _get_user_auth_version(user_id)
    if current_auth_version is None or int(current_auth_version) != auth_version:
        return None

    session = _get_auth_session(str(session_id))
    if not is_auth_session_active(session):
        redis_client.clear_cached_auth_session(str(session_id))
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


def _create_session(
    user_id: int,
    auth_version: int,
    created_ip: Optional[str],
    created_user_agent: Optional[str],
) -> Tuple[bool, Optional[dict], str]:
    session_id = uuid.uuid4().hex
    refresh_token = generate_refresh_token()
    refresh_token_hash = hash_refresh_token(refresh_token)
    expires_at = get_refresh_token_expiry()

    session = postgres_client.create_auth_session(
        session_id=session_id,
        user_id=user_id,
        refresh_token_hash=refresh_token_hash,
        auth_version=auth_version,
        expires_at=expires_at,
        created_ip=created_ip,
        created_user_agent=created_user_agent,
    )
    if not session:
        return False, None, "Failed to create session"

    redis_client.cache_user_auth_version(user_id, auth_version)
    redis_client.cache_auth_session(session_id, session)

    return (
        True,
        {
            "session_id": session_id,
            "refresh_token": refresh_token,
            "auth_version": auth_version,
        },
        "",
    )


def register_user(
    username: str,
    password: str,
    created_ip: Optional[str] = None,
    created_user_agent: Optional[str] = None,
) -> Tuple[bool, Optional[dict], str]:
    """
    Register a new user.

    Args:
        username: The username (must be 5-25 alphanumeric chars, unique)
        password: The password (must be 5-25 chars, alphanumeric + symbols)

    Returns:
        Tuple of (success: bool, user_data: dict or None, error_message: str)
    """
    try:
        # Check if username already exists
        existing_user = postgres_client.get_user_by_username(username)
        if existing_user:
            return False, None, "Username already exists"

        password_hash = hash_password(password)
        session_id = uuid.uuid4().hex
        refresh_token = generate_refresh_token()
        refresh_token_hash = hash_refresh_token(refresh_token)
        expires_at = get_refresh_token_expiry()

        registration_data = postgres_client.create_user_with_settings_and_auth_session(
            username=username,
            password_hash=password_hash,
            session_id=session_id,
            refresh_token_hash=refresh_token_hash,
            expires_at=expires_at,
            created_ip=created_ip,
            created_user_agent=created_user_agent,
        )
        if not registration_data:
            return False, None, "Registration failed"

        user = registration_data["user"]
        session = registration_data["session"]

        redis_client.cache_user_auth_version(int(user["id"]), int(user["auth_version"]))
        redis_client.cache_auth_session(str(session["session_id"]), session)

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
        # Handle check-then-insert race where another request created the same
        # username (case-insensitive) between existence check and insert.
        return False, None, "Username already exists"
    except Exception:
        logger.exception("Registration failed")
        return False, None, "Registration failed"


def login_user(
    username: str,
    password: str,
    created_ip: Optional[str] = None,
    created_user_agent: Optional[str] = None,
) -> Tuple[bool, Optional[dict], str]:
    """
    Authenticate a user and return a JWT token.

    Args:
        username: The username
        password: The password

    Returns:
        Tuple of (success: bool, user_data: dict or None, error_message: str)
    """
    try:
        # Get user from database
        user = postgres_client.get_user_by_username(username)

        if not user:
            return False, None, "Invalid username or password"

        # Verify password
        if not verify_password(password, user["password_hash"]):
            return False, None, "Invalid username or password"

        session_created, session_data, session_error = _create_session(
            user_id=user["id"],
            auth_version=int(user["auth_version"]),
            created_ip=created_ip,
            created_user_agent=created_user_agent,
        )
        if not session_created:
            return False, None, session_error

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
        logger.exception("Login failed")
        return False, None, "Authentication failed"


def refresh_session(refresh_token: str) -> Tuple[bool, Optional[dict], str]:
    """Rotate a refresh token and return a fresh token pair."""
    try:
        refresh_token_hash = hash_refresh_token(refresh_token)
        session = postgres_client.get_auth_session_by_refresh_token_hash(
            refresh_token_hash
        )
        if not is_auth_session_active(session):
            if session:
                redis_client.clear_cached_auth_session(str(session["session_id"]))
            return False, None, "Invalid refresh token"

        user = postgres_client.get_user_by_id(int(session["user_id"]))
        if not user:
            return False, None, "Invalid refresh token"

        current_auth_version = _get_user_auth_version(int(user["id"]))
        if current_auth_version is None:
            return False, None, "Invalid refresh token"

        if int(current_auth_version) != int(session["auth_version"]):
            redis_client.clear_cached_auth_session(str(session["session_id"]))
            return False, None, "Invalid refresh token"

        new_refresh_token = generate_refresh_token()
        rotated_session = postgres_client.rotate_auth_session_refresh_token(
            session_id=str(session["session_id"]),
            current_refresh_token_hash=refresh_token_hash,
            refresh_token_hash=hash_refresh_token(new_refresh_token),
            expires_at=get_refresh_token_expiry(),
        )
        if not rotated_session:
            redis_client.clear_cached_auth_session(str(session["session_id"]))
            return False, None, "Invalid refresh token"

        redis_client.cache_user_auth_version(int(user["id"]), int(current_auth_version))
        redis_client.cache_auth_session(str(session["session_id"]), rotated_session)

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
        logger.exception("Refresh session failed")
        return False, None, "Invalid refresh token"


def logout_session(session_id: str) -> bool:
    """Revoke a single auth session."""
    success = postgres_client.revoke_auth_session(session_id, "logout")
    redis_client.clear_cached_auth_session(session_id)
    return success


def change_password(
    user_id: int,
    old_password: str,
    new_password: str,
    username: str,
    created_ip: Optional[str] = None,
    created_user_agent: Optional[str] = None,
) -> Tuple[bool, Optional[dict], str]:
    """
    Change a user's password.

    Args:
        user_id: The user's ID
        old_password: The current password
        new_password: The new password
        username: The user's username (for validation)

    Returns:
        Tuple of (success: bool, response_data: dict or None, error_message: str)
    """
    try:
        # Get user from database
        user = postgres_client.get_user_by_id(user_id)

        if not user:
            return False, None, "User not found"

        # Verify old password
        if not verify_password(old_password, user["password_hash"]):
            return False, None, "Current password is incorrect"

        # Check that new password is different from username
        if new_password == username:
            return False, None, "Password cannot be the same as username"

        # Check that new password is different from old password
        if verify_password(new_password, user["password_hash"]):
            return False, None, "New password must be different from current password"

        # Hash new password
        new_password_hash = hash_password(new_password)

        new_session_id = uuid.uuid4().hex
        new_refresh_token = generate_refresh_token()
        expires_at = get_refresh_token_expiry()

        session = postgres_client.change_password_and_create_session(
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
        redis_client.clear_cached_auth_sessions(revoked_session_ids)
        redis_client.cache_user_auth_version(user_id, int(session["auth_version"]))
        redis_client.cache_auth_session(new_session_id, session)

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
        logger.exception("Password change failed")
        return False, None, "Password change failed"


def get_user_by_id(user_id: int) -> Optional[dict]:
    """
    Get a user by their ID.

    Args:
        user_id: The user's ID

    Returns:
        The user data if found, None otherwise
    """
    try:
        user = postgres_client.get_user_by_id(user_id)
        if not user:
            return None

        return serialize_user(user)
    except Exception:
        return None
