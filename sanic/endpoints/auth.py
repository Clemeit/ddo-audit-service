"""
Authentication endpoints for registration, login, refresh, and logout.
"""

from sanic import Blueprint
from sanic.response import json
from sanic.request import Request
from pydantic import ValidationError
import logging

from models.user import RefreshTokenRequest, UserLogin, UserRegister
import services.auth as auth_service
from utils.access_log import get_client_ip


auth_blueprint = Blueprint("auth", url_prefix="/auth", version=1)
logger = logging.getLogger(__name__)


def _get_client_metadata(request: Request) -> tuple[str, str]:
    return get_client_ip(request), request.headers.get("User-Agent", "")


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
                "refresh_token": "string",
                "token_type": "Bearer",
                "expires_in": 900,
                "refresh_expires_in": 2592000,
                "user": {
                    "id": int,
                    "username": "string",
                    "created_at": "string"
                }
            }
        }
    """
    try:
        # Parse and validate request body
        body = request.json
        if body is None:
            return json({"error": "Invalid or missing JSON body"}, status=400)
        user_register = UserRegister(**body)

        # Register user
        client_ip, user_agent = _get_client_metadata(request)
        success, user_data, error_msg = auth_service.register_user(
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

        return json(
            {"data": user_data},
            status=201,
        )

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
    Authenticate a user and return an access/refresh token pair.

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
                "refresh_token": "string",
                "token_type": "Bearer",
                "expires_in": 900,
                "refresh_expires_in": 2592000,
                "user": {
                    "id": int,
                    "username": "string",
                    "created_at": "string"
                }
            }
        }
    """
    try:
        # Parse and validate request body
        body = request.json
        if body is None:
            return json({"error": "Invalid or missing JSON body"}, status=400)
        user_login = UserLogin(**body)

        # Authenticate user
        client_ip, user_agent = _get_client_metadata(request)
        success, user_data, error_msg = auth_service.login_user(
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

        return json(
            {"data": user_data},
            status=200,
        )

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
    """Rotate a refresh token and return a fresh access/refresh token pair."""
    try:
        body = request.json
        if body is None:
            return json({"error": "Invalid or missing JSON body"}, status=400)
        refresh_request = RefreshTokenRequest(**body)

        success, token_data, error_msg = auth_service.refresh_session(
            refresh_request.refresh_token
        )
        if not success:
            if error_msg == auth_service.AUTH_ERROR_INVALID_REFRESH_TOKEN:
                return json({"error": "Invalid refresh token"}, status=401)

            logger.error("Refresh request failed due to internal error: %s", error_msg)
            return json({"error": "Unable to refresh session"}, status=500)

        return json({"data": token_data}, status=200)

    except ValidationError as e:
        errors = e.errors()
        error_msgs = [f"{err['loc'][0]}: {err['msg']}" for err in errors]
        return json({"error": ", ".join(error_msgs)}, status=400)
    except TypeError:
        return json({"error": "Invalid request body format"}, status=400)
    except Exception:
        logger.exception("Unhandled error in refresh endpoint")
        return json({"error": "Internal server error"}, status=500)


@auth_blueprint.post("/logout")
async def logout(request: Request):
    """Revoke the current authenticated session."""
    try:
        session_id = getattr(request.ctx, "session_id", None)
        if not session_id:
            return json({"error": "Unauthorized"}, status=401)

        if not auth_service.logout_session(session_id):
            return json({"error": "Failed to log out"}, status=500)

        return json({"data": {"message": "Logged out successfully"}}, status=200)
    except Exception:
        logger.exception("Unhandled error in logout endpoint")
        return json({"error": "Internal server error"}, status=500)
