"""
Authentication endpoints for registration, login, refresh, and logout.
"""

import os
from sanic import Blueprint
from sanic.response import json
from sanic.request import Request
from pydantic import ValidationError
import logging

from models.user import UserLogin, UserRegister
import services.auth as auth_service
from utils.access_log import get_client_ip


auth_blueprint = Blueprint("auth", url_prefix="/auth", version=1)
logger = logging.getLogger(__name__)

# Cookie configuration — override via environment for each deployment tier.
_COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"

_SAME_SITE_ALLOWED = {"Lax", "Strict", "None"}
_raw_same_site = os.getenv("COOKIE_SAME_SITE", "Lax")
_COOKIE_SAME_SITE = _raw_same_site if _raw_same_site in _SAME_SITE_ALLOWED else "Lax"
if _COOKIE_SAME_SITE != _raw_same_site:
    logging.getLogger(__name__).warning(
        "Invalid COOKIE_SAME_SITE value %r; falling back to 'Lax'", _raw_same_site
    )
# SameSite=None requires Secure=True per the cookies spec.
if _COOKIE_SAME_SITE == "None":
    _COOKIE_SECURE = True

_REFRESH_COOKIE_NAME = "refresh_token"
_REFRESH_COOKIE_PATH = "/v1/auth"


def _get_client_metadata(request: Request) -> tuple[str, str]:
    return get_client_ip(request), request.headers.get("User-Agent", "")


def _set_refresh_cookie(response, token: str) -> None:
    """Attach an HttpOnly refresh-token cookie to the response."""
    response.add_cookie(
        _REFRESH_COOKIE_NAME,
        token,
        httponly=True,
        secure=_COOKIE_SECURE,
        samesite=_COOKIE_SAME_SITE,
        path=_REFRESH_COOKIE_PATH,
        max_age=auth_service.REFRESH_TOKEN_EXPIRATION_SECONDS,
    )
    logger.debug(
        "Refresh cookie set (path=%s, secure=%s)", _REFRESH_COOKIE_PATH, _COOKIE_SECURE
    )


def _clear_refresh_cookie(response) -> None:
    """Expire and clear the refresh-token cookie from the client."""
    response.delete_cookie(_REFRESH_COOKIE_NAME, path=_REFRESH_COOKIE_PATH)
    logger.debug("Refresh cookie cleared")


@auth_blueprint.post("/register")
async def register(request: Request):
    """
    Register a new user account.

    Method: POST
    Route: /auth/register

    Request body:
        {
            "username": "string (5-25 alphanumeric)",
            "password": "string (5-25 chars, alphanumeric + symbols)"
        }

    Returns:
        {
            "data": {
                "access_token": "string",
                "token_type": "Bearer",
                "expires_in": 900,
                "user": {
                    "id": int,
                    "username": "string",
                    "created_at": "string"
                }
            }
        }

    The refresh token is delivered via an HttpOnly cookie, not in the response body.
    """
    try:
        # Parse and validate request body
        body = request.json
        if body is None:
            return json({"error": "Invalid or missing JSON body"}, status=400)
        user_register = UserRegister(**body)

        # Register user
        client_ip, user_agent = _get_client_metadata(request)
        success, user_data, error_msg = await auth_service.async_register_user(
            user_register.username,
            user_register.password,
            created_ip=client_ip,
            created_user_agent=user_agent,
        )

        if not success:
            # Do not leak account enumeration details (e.g., existing usernames).
            if error_msg == auth_service.AUTH_ERROR_USERNAME_EXISTS:
                logger.warning("Registration request failed: %s", error_msg)
                return json({"error": "Unable to register account"}, status=400)

            logger.error(
                "Registration request failed due to internal error: %s", error_msg
            )
            return json({"error": "Unable to register account"}, status=500)

        # Move the refresh token to an HttpOnly cookie; strip it from the JSON body.
        refresh_token = user_data.pop("refresh_token", None)
        user_data.pop("refresh_expires_in", None)

        response = json({"data": user_data}, status=201)
        if refresh_token:
            _set_refresh_cookie(response, refresh_token)
        return response

    except ValidationError as e:
        errors = e.errors()
        error_msgs = [f"{err['loc'][0]}: {err['msg']}" for err in errors]
        return json({"error": ", ".join(error_msgs)}, status=400)
    except TypeError:
        return json({"error": "Invalid request body format"}, status=400)
    except Exception as e:
        logger.exception("Unhandled error in register endpoint")
        return json({"error": "Internal server error"}, status=500)


@auth_blueprint.post("/login")
async def login(request: Request):
    """
    Authenticate a user and return an access token.

    Method: POST
    Route: /auth/login

    Request body:
        {
            "username": "string",
            "password": "string"
        }

    Returns:
        {
            "data": {
                "access_token": "string",
                "token_type": "Bearer",
                "expires_in": 900,
                "user": {
                    "id": int,
                    "username": "string",
                    "created_at": "string"
                }
            }
        }

    The refresh token is delivered via an HttpOnly cookie, not in the response body.
    """
    try:
        # Parse and validate request body
        body = request.json
        if body is None:
            return json({"error": "Invalid or missing JSON body"}, status=400)
        user_login = UserLogin(**body)

        # Authenticate user
        client_ip, user_agent = _get_client_metadata(request)
        success, user_data, error_msg = await auth_service.async_login_user(
            user_login.username,
            user_login.password,
            created_ip=client_ip,
            created_user_agent=user_agent,
        )

        if not success:
            if error_msg == auth_service.AUTH_ERROR_INVALID_CREDENTIALS:
                # Only expose a generic auth error to avoid information leakage.
                return json({"error": "Invalid username or password"}, status=401)

            logger.error("Login request failed due to internal error: %s", error_msg)
            return json({"error": "Unable to complete login"}, status=500)

        # Move the refresh token to an HttpOnly cookie; strip it from the JSON body.
        refresh_token = user_data.pop("refresh_token", None)
        user_data.pop("refresh_expires_in", None)

        response = json({"data": user_data}, status=200)
        if refresh_token:
            _set_refresh_cookie(response, refresh_token)
        return response

    except ValidationError as e:
        errors = e.errors()
        error_msgs = [f"{err['loc'][0]}: {err['msg']}" for err in errors]
        return json({"error": ", ".join(error_msgs)}, status=400)
    except TypeError:
        return json({"error": "Invalid request body format"}, status=400)
    except Exception as e:
        logger.exception("Unhandled error in login endpoint")
        return json({"error": "Internal server error"}, status=500)


@auth_blueprint.post("/refresh")
async def refresh(request: Request):
    """
    Issue a new access token by reading the refresh token from an HttpOnly cookie.

    The request body is ignored. The rotated refresh token is returned via a new
    HttpOnly cookie, not in the response body.
    """
    try:
        raw_token = request.cookies.get(_REFRESH_COOKIE_NAME)
        if not raw_token:
            response = json({"error": "Invalid refresh token"}, status=401)
            _clear_refresh_cookie(response)
            return response

        success, token_data, error_msg = await auth_service.async_refresh_session(
            raw_token
        )
        if not success:
            if error_msg == auth_service.AUTH_ERROR_INVALID_REFRESH_TOKEN:
                response = json({"error": "Invalid refresh token"}, status=401)
                _clear_refresh_cookie(response)
                return response

            logger.error("Refresh request failed due to internal error: %s", error_msg)
            return json({"error": "Unable to refresh session"}, status=500)

        # Rotate the refresh token via cookie; strip it from the JSON body.
        new_refresh_token = token_data.pop("refresh_token", None)
        token_data.pop("refresh_expires_in", None)

        response = json({"data": token_data}, status=200)
        if new_refresh_token:
            _set_refresh_cookie(response, new_refresh_token)
        return response

    except Exception:
        logger.exception("Unhandled error in refresh endpoint")
        return json({"error": "Internal server error"}, status=500)


@auth_blueprint.post("/logout")
async def logout(request: Request):
    """Revoke the current authenticated session and clear the refresh cookie."""
    try:
        session_id = getattr(request.ctx, "session_id", None)
        if not session_id:
            return json({"error": "Unauthorized"}, status=401)

        if not await auth_service.async_logout_session(session_id):
            return json({"error": "Failed to log out"}, status=500)

        response = json({"data": {"message": "Logged out successfully"}}, status=200)
        _clear_refresh_cookie(response)
        return response
    except Exception:
        logger.exception("Unhandled error in logout endpoint")
        return json({"error": "Internal server error"}, status=500)


@auth_blueprint.delete("/account")
async def delete_account(request: Request):
    """Delete the authenticated user's account and related records."""
    try:
        user_id = getattr(request.ctx, "user_id", None)
        if not user_id:
            return json({"error": "Unauthorized"}, status=401)

        success, error_msg = await auth_service.async_delete_user_account(int(user_id))
        if not success:
            if error_msg == auth_service.AUTH_ERROR_USER_NOT_FOUND:
                return json({"error": "User not found"}, status=404)

            return json({"error": "Failed to delete account"}, status=500)

        response = json(
            {"data": {"message": "Account deleted successfully"}}, status=200
        )
        _clear_refresh_cookie(response)
        return response
    except Exception:
        logger.exception("Unhandled error in delete account endpoint")
        return json({"error": "Internal server error"}, status=500)
