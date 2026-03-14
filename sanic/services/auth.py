"""
Authentication service for user management.

Handles password hashing, JWT token generation and validation, and user CRUD operations.
"""

import os
import bcrypt
import jwt
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
import services.postgres as postgres_client
from models.user import UserProfile


# JWT Configuration
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "")
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_DAYS = 7  # 7 days

# Validate JWT secret key on import
if not JWT_SECRET_KEY:
    logger = logging.getLogger(__name__)
    logger.warning(
        "JWT_SECRET_KEY is not set! "
        "Set JWT_SECRET_KEY environment variable to a secure random string in production. "
        "Using insecure fallback for development only."
    )
    JWT_SECRET_KEY = "insecure-development-key-change-immediately"


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


def generate_jwt_token(user_id: int, username: str) -> str:
    """
    Generate a JWT token for a user.

    Args:
        user_id: The user's ID
        username: The user's username

    Returns:
        The signed JWT token string
    """
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(days=JWT_EXPIRATION_DAYS)

    payload = {
        "user_id": user_id,
        "username": username,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }

    token = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return token


def verify_jwt_token(token: str) -> Optional[dict]:
    """
    Verify a JWT token and return the payload.

    Args:
        token: The JWT token to verify

    Returns:
        The decoded token payload if valid, None if invalid or expired
    """
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None
    except Exception:
        return None


def register_user(username: str, password: str) -> Tuple[bool, Optional[dict], str]:
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

        # Hash password
        password_hash = hash_password(password)

        # Create user in database
        user = postgres_client.create_user(username, password_hash)

        if not user:
            return False, None, "Failed to create user"

        # Create settings for user
        postgres_client.create_user_settings(user["id"])

        # Generate JWT token
        token = generate_jwt_token(user["id"], user["username"])

        return (
            True,
            {
                "user": {
                    "id": user["id"],
                    "username": user["username"],
                    "created_at": user["created_at"],
                },
                "token": token,
            },
            "",
        )

    except Exception as e:
        return False, None, f"Registration failed: {str(e)}"


def login_user(username: str, password: str) -> Tuple[bool, Optional[dict], str]:
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

        # Generate JWT token
        token = generate_jwt_token(user["id"], user["username"])

        return (
            True,
            {
                "user": {
                    "id": user["id"],
                    "username": user["username"],
                    "created_at": user["created_at"],
                },
                "token": token,
            },
            "",
        )

    except Exception as e:
        return False, None, f"Login failed: {str(e)}"


def change_password(
    user_id: int, old_password: str, new_password: str, username: str
) -> Tuple[bool, str]:
    """
    Change a user's password.

    Args:
        user_id: The user's ID
        old_password: The current password
        new_password: The new password
        username: The user's username (for validation)

    Returns:
        Tuple of (success: bool, error_message: str)
    """
    try:
        # Get user from database
        user = postgres_client.get_user_by_id(user_id)

        if not user:
            return False, "User not found"

        # Verify old password
        if not verify_password(old_password, user["password_hash"]):
            return False, "Current password is incorrect"

        # Check that new password is different from username
        if new_password == username:
            return False, "Password cannot be the same as username"

        # Check that new password is different from old password
        if verify_password(new_password, user["password_hash"]):
            return False, "New password must be different from current password"

        # Hash new password
        new_password_hash = hash_password(new_password)

        # Update password in database
        success = postgres_client.update_user_password(user_id, new_password_hash)

        if not success:
            return False, "Failed to update password"

        return True, ""

    except Exception as e:
        return False, f"Password change failed: {str(e)}"


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

        return {
            "id": user["id"],
            "username": user["username"],
            "created_at": user["created_at"],
        }
    except Exception:
        return None
