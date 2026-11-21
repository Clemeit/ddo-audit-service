"""
Authentication endpoints for registration, login, refresh, and logout.
"""

from sanic import Blueprint
from sanic.response import json
from sanic.request import Request
from sanic_ext import openapi
from pydantic import ValidationError
import logging

from models.user import UserLogin, UserRegister
import services.auth as auth_service
from middleware.rate_limit import (
    async_clear_login_failures,
    async_get_login_failure_retry_after,
    async_record_login_failure,
)
from utils.auth_cookies import (
    clear_refresh_cookie,
    get_refresh_cookie,
    is_trusted_origin,
    set_refresh_cookie,
)
from utils.access_log import get_client_ip
import os

# Phase-in switch: once every client sends the password, set to true so DELETE /auth/account
# without one is refused.
ACCOUNT_DELETE_REQUIRE_PASSWORD = (
    os.getenv("ACCOUNT_DELETE_REQUIRE_PASSWORD", "false").lower() == "true"
)


auth_blueprint = Blueprint("auth", url_prefix="/auth", version=1)
logger = logging.getLogger(__name__)


def _get_client_metadata(request: Request) -> tuple[str, str]:
    return get_client_ip(request), request.headers.get("User-Agent", "")


@auth_blueprint.post("/register")
@openapi.summary("Register a new user account")
@openapi.body({"application/json": UserRegister})
@openapi.response(
    201,
    {
        "application/json": {
            "description": "Access token, token type, expiry, and user info"
        }
    },
)
@openapi.response(400, description="Validation error")
@openapi.response(409, description="Username already taken")
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
            # Registration can't hide whether a name is taken (trying it reveals that
            # anyway); say so plainly and rely on the per-IP rate limit against scraping.
            if error_msg == auth_service.AUTH_ERROR_USERNAME_EXISTS:
                return json({"error": "Username is already taken"}, status=409)

            logger.error(
                "Registration request failed due to internal error: %s", error_msg
            )
            return json({"error": "Unable to register account"}, status=500)

        # Deliver the refresh token via HttpOnly cookie only; strip it from the JSON body.
        refresh_token = user_data.pop("refresh_token", None)
        refresh_expires_in = user_data.pop("refresh_expires_in", None)

        response = json({"data": user_data}, status=201)
        if refresh_token:
            set_refresh_cookie(response, refresh_token, refresh_expires_in)
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
@openapi.summary("Authenticate and receive an access token")
@openapi.body({"application/json": UserLogin})
@openapi.response(
    200,
    {
        "application/json": {
            "description": "Access token, token type, expiry, and user info"
        }
    },
)
@openapi.response(401, description="Invalid username or password")
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

        # Too many recent failures for this username (from any IP): refuse before
        # spending a bcrypt check. Same answer whether or not the account exists.
        retry_after = await async_get_login_failure_retry_after(user_login.username)
        if retry_after is not None:
            return json(
                {
                    "error": "Too many failed login attempts. Try again later.",
                    "retry_after": retry_after,
                },
                status=429,
                headers={"Retry-After": str(retry_after)},
            )

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
                await async_record_login_failure(user_login.username)
                # Only expose a generic auth error to avoid information leakage.
                return json({"error": "Invalid username or password"}, status=401)

            logger.error("Login request failed due to internal error: %s", error_msg)
            return json({"error": "Unable to complete login"}, status=500)

        await async_clear_login_failures(user_login.username)

        # Deliver the refresh token via HttpOnly cookie only; strip it from the JSON body.
        refresh_token = user_data.pop("refresh_token", None)
        refresh_expires_in = user_data.pop("refresh_expires_in", None)

        response = json({"data": user_data}, status=200)
        if refresh_token:
            set_refresh_cookie(response, refresh_token, refresh_expires_in)
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


def _forbidden_origin_response():
    return json({"error": "Origin not allowed"}, status=403)


def _refresh_rejected_response(reason: str):
    """401 that tells the client why its session ended, and drops the dead cookie."""
    response = json({"error": "Invalid refresh token", "code": reason}, status=401)
    clear_refresh_cookie(response)
    return response


@auth_blueprint.post("/refresh")
@openapi.summary("Refresh access token using HttpOnly refresh cookie")
@openapi.response(
    200, {"application/json": {"description": "New access token and token type"}}
)
@openapi.response(
    401,
    description=(
        "Missing or invalid refresh token. Body has `code`: invalid, "
        "session_expired, or session_revoked"
    ),
)
@openapi.response(403, description="Request Origin is not an allowed frontend")
async def refresh(request: Request):
    """
    Issue a new access token by reading the refresh token from an HttpOnly cookie.

    The request body is ignored. When the refresh token is rotated, the new one is
    returned via an HttpOnly cookie, not in the response body. A request presenting a
    token that was rotated out moments ago (another tab, a retried request) gets an
    access token and no cookie change.

    Only a 401 means the session is over; clients should treat 403/429/5xx as transient.
    """
    try:
        if not is_trusted_origin(request):
            return _forbidden_origin_response()

        raw_token = get_refresh_cookie(request)
        if not raw_token:
            return _refresh_rejected_response(auth_service.REFRESH_FAILURE_INVALID)

        success, token_data, error_msg = await auth_service.async_refresh_session(
            raw_token
        )
        if not success:
            if error_msg == auth_service.AUTH_ERROR_INVALID_REFRESH_TOKEN:
                reason = (token_data or {}).get(
                    "reason", auth_service.REFRESH_FAILURE_INVALID
                )
                return _refresh_rejected_response(reason)

            logger.error("Refresh request failed due to internal error: %s", error_msg)
            return json({"error": "Unable to refresh session"}, status=500)

        # Deliver the rotated refresh token via HttpOnly cookie only; strip it from the JSON body.
        new_refresh_token = token_data.pop("refresh_token", None)
        refresh_expires_in = token_data.pop("refresh_expires_in", None)

        response = json({"data": token_data}, status=200)
        if new_refresh_token:
            set_refresh_cookie(response, new_refresh_token, refresh_expires_in)
        return response

    except Exception:
        logger.exception("Unhandled error in refresh endpoint")
        return json({"error": "Internal server error"}, status=500)


@auth_blueprint.post("/logout")
@openapi.summary("Logout and revoke current session")
@openapi.response(200, {"application/json": {"description": "Logged out successfully"}})
@openapi.response(403, description="Request Origin is not an allowed frontend")
async def logout(request: Request):
    """Revoke this browser's session and clear the refresh cookie.

    The session is found from the refresh cookie, falling back to a still-valid Bearer
    access token, so logging out works even after the access token has expired. Always
    answers 200 and clears the cookie (idempotent): once this returns, this browser can
    no longer refresh, whatever state the server-side session was in.
    """
    try:
        if not is_trusted_origin(request):
            return _forbidden_origin_response()

        revoked = False
        raw_token = get_refresh_cookie(request)
        if raw_token:
            result = await auth_service.async_logout_by_refresh_token(raw_token)
            if result is None:
                logger.error("Logout could not revoke the cookie's session")
            revoked = bool(result)

        if not revoked:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                payload = await auth_service.async_validate_access_token(
                    auth_header[7:]
                )
                session_id = (payload or {}).get("session_id")
                if session_id and not await auth_service.async_logout_session(
                    str(session_id)
                ):
                    logger.error("Logout could not revoke the bearer token's session")

        response = json({"data": {"message": "Logged out successfully"}}, status=200)
        clear_refresh_cookie(response)
        return response
    except Exception:
        logger.exception("Unhandled error in logout endpoint")
        response = json({"error": "Internal server error"}, status=500)
        clear_refresh_cookie(response)
        return response


def _read_delete_account_password(request: Request):
    """Return (password, error_response). A missing/empty body yields (None, None)."""
    if not request.body:
        return None, None
    try:
        body = request.json
    except Exception:
        return None, json({"error": "Invalid or missing JSON body"}, status=400)
    if body is None:
        return None, None
    if not isinstance(body, dict):
        return None, json({"error": "Invalid request body format"}, status=400)
    password = body.get("password")
    if password is not None and not isinstance(password, str):
        return None, json({"error": "password: must be a string"}, status=400)
    return password, None


@auth_blueprint.delete("/account")
@openapi.summary("Delete authenticated user account")
@openapi.secured("BearerAuth")
@openapi.response(
    200, {"application/json": {"description": "Account deleted successfully"}}
)
@openapi.response(400, description="Password missing or incorrect")
@openapi.response(401, description="Unauthorized")
@openapi.response(404, description="User not found")
async def delete_account(request: Request):
    """Delete the authenticated user's account and related records.

    Body: {"password": "<current password>"}. A wrong password is a 400, never a 401:
    the access token itself is fine, so clients must not treat it as a session problem.
    """
    try:
        user_id = getattr(request.ctx, "user_id", None)
        if not user_id:
            return json({"error": "Unauthorized"}, status=401)

        password, error_response = _read_delete_account_password(request)
        if error_response is not None:
            return error_response

        success, error_msg = await auth_service.async_delete_user_account(
            int(user_id),
            password=password,
            require_password=ACCOUNT_DELETE_REQUIRE_PASSWORD,
        )
        if not success:
            if error_msg == auth_service.AUTH_ERROR_USER_NOT_FOUND:
                return json({"error": "User not found"}, status=404)
            if error_msg == auth_service.AUTH_ERROR_PASSWORD_REQUIRED:
                return json(
                    {"error": "Enter your password to delete your account"},
                    status=400,
                )
            if error_msg == auth_service.AUTH_ERROR_INVALID_CREDENTIALS:
                return json({"error": "Password is incorrect"}, status=400)

            return json({"error": "Failed to delete account"}, status=500)

        response = json(
            {"data": {"message": "Account deleted successfully"}}, status=200
        )
        clear_refresh_cookie(response)
        return response
    except Exception:
        logger.exception("Unhandled error in delete account endpoint")
        return json({"error": "Internal server error"}, status=500)
