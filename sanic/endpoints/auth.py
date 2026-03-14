"""
Authentication endpoints for user registration and login.
"""

from sanic import Blueprint
from sanic.response import json
from sanic.request import Request
from pydantic import ValidationError

from models.user import UserRegister, UserLogin
import services.auth as auth_service


auth_blueprint = Blueprint("auth", url_prefix="/auth", version=1)


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
                "expires_in": 604800,
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
        success, user_data, error_msg = auth_service.register_user(
            user_register.username, user_register.password
        )

        if not success:
            return json({"error": error_msg}, status=400)

        return json(
            {
                "data": {
                    "access_token": user_data["token"],
                    "token_type": "Bearer",
                    "expires_in": 604800,  # 7 days in seconds
                    "user": user_data["user"],
                }
            },
            status=201,
        )

    except ValidationError as e:
        errors = e.errors()
        error_msgs = [f"{err['loc'][0]}: {err['msg']}" for err in errors]
        return json({"error": ", ".join(error_msgs)}, status=400)
    except TypeError:
        return json({"error": "Invalid request body format"}, status=400)
    except Exception as e:
        return json({"error": "Internal server error"}, status=500)


@auth_blueprint.post("/login")
async def login(request: Request):
    """
    Authenticate a user and return a JWT token.

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
                "expires_in": 604800,
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
        success, user_data, error_msg = auth_service.login_user(
            user_login.username, user_login.password
        )

        if not success:
            return json({"error": error_msg}, status=401)

        return json(
            {
                "data": {
                    "access_token": user_data["token"],
                    "token_type": "Bearer",
                    "expires_in": 604800,  # 7 days in seconds
                    "user": user_data["user"],
                }
            },
            status=200,
        )

    except ValidationError as e:
        errors = e.errors()
        error_msgs = [f"{err['loc'][0]}: {err['msg']}" for err in errors]
        return json({"error": ", ".join(error_msgs)}, status=400)
    except TypeError:
        return json({"error": "Invalid request body format"}, status=400)
    except Exception as e:
        return json({"error": "Internal server error"}, status=500)
