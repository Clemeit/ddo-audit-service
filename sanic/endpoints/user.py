"""
User endpoints for profile, settings, and account management.
"""

import services.redis as redis_client
import services.postgres as postgres_client
import services.auth as auth_service

from sanic import Blueprint
from sanic.response import json
from sanic.request import Request
from sanic_ext import openapi
from pydantic import ValidationError

import secrets
import string
import json as json_lib
from models.user import ChangePassword
from utils.auth_cookies import set_refresh_cookie
from utils.access_log import get_client_ip

user_blueprint = Blueprint("user", url_prefix="/user", version=1)


# ===== One-time settings endpoints (backward compatibility) =====
@user_blueprint.post("/settings")
@openapi.summary("Store one-time settings and receive a retrieval ID")
@openapi.response(
    200,
    {
        "application/json": {
            "description": "6-character uppercase ID for retrieving settings"
        }
    },
)
@openapi.response(413, description="Request body too large (max 25 KB)")
async def post_user_settings(request):
    """
    Method: POST

    Route: /user/settings

    Description: Store user settings into redis. Return a unique 6-character alphabetic ID
    that can be used to retrieve the settings later.
    """
    try:
        settings = request.json
        if not settings:
            return json({"message": "Invalid request body"}, status=400)

        # Ensure size less than 25 KB
        if len(request.body) > 25 * 1024:
            return json({"message": "Request body too large"}, status=413)

        attempts = 0
        max_attempts = 10
        while True:
            user_id = "".join(secrets.choice(string.ascii_uppercase) for _ in range(6))
            if (
                not redis_client.one_time_user_settings_exists(user_id)
                or attempts >= max_attempts
            ):
                break
            attempts += 1

        if attempts >= max_attempts:
            return json({"message": "Could not generate unique user ID"}, status=500)

        redis_client.store_one_time_user_settings(user_id, settings)
    except Exception:
        return json({"message": "Internal server error"}, status=500)

    return json({"data": {"user_id": user_id}})


@user_blueprint.get("/settings/<user_id:str>")
@openapi.summary("Retrieve one-time settings by ID")
@openapi.response(200, {"application/json": {"description": "Stored settings object"}})
@openapi.response(404, description="Settings not found or already retrieved")
async def get_user_settings_one_time(request, user_id: str):
    """
    Method: GET

    Route: /user/settings/<user_id>

    Description: Retrieve user settings from redis using the unique user ID.
    """
    try:
        settings = redis_client.get_one_time_user_settings(user_id)
        if not settings:
            return json(
                {"message": "Settings not found or already retrieved"}, status=404
            )
    except Exception:
        return json({"message": "Internal server error"}, status=500)

    return json({"data": settings})


# ===== Authenticated user endpoints =====
@user_blueprint.get("/profile")
@openapi.summary("Get authenticated user profile")
@openapi.secured("BearerAuth")
@openapi.response(200, {"application/json": {"description": "User profile data"}})
@openapi.response(401, description="Unauthorized")
@openapi.response(404, description="User not found")
async def get_user_profile(request: Request):
    """
    Get authenticated user's profile.

    Method: GET
    Route: /user/profile

    Requires: Valid JWT token in Authorization header

    Returns:
        {
            "data": {
                "id": int,
                "username": "string",
                "created_at": "string"
            }
        }
    """
    try:
        # User ID should be set by JWT middleware
        user_id = getattr(request.ctx, "user_id", None)
        if not user_id:
            return json({"error": "Unauthorized"}, status=401)

        user = await auth_service.async_get_user_by_id(user_id)
        if not user:
            return json({"error": "User not found"}, status=404)

        return json({"data": user}, status=200)
    except Exception as e:
        return json({"error": "Internal server error"}, status=500)


@user_blueprint.put("/profile/password")
@openapi.summary("Change authenticated user's password")
@openapi.secured("BearerAuth")
@openapi.body({"application/json": ChangePassword})
@openapi.response(
    200,
    {
        "application/json": {
            "description": "New access token; rotated refresh token delivered via cookie"
        }
    },
)
@openapi.response(400, description="Validation error or incorrect old password")
@openapi.response(401, description="Unauthorized")
async def change_user_password(request: Request):
    """
    Change authenticated user's password.

    Method: PUT
    Route: /user/profile/password

    Requires: Valid JWT token in Authorization header

    Request body:
        {
            "old_password": "string",
            "new_password": "string (5-25 chars, alphanumeric + symbols)"
        }

    Returns:
        {
            "data": {
                "message": "Password changed successfully",
                "access_token": "string",
                "token_type": "Bearer",
                "expires_in": 900
            }
        }

    The refresh token is delivered via an HttpOnly cookie, not in the response body.
    """
    try:
        # User ID should be set by JWT middleware
        user_id = getattr(request.ctx, "user_id", None)
        username = getattr(request.ctx, "username", None)
        if not user_id or not username:
            return json({"error": "Unauthorized"}, status=401)

        # Parse and validate request body
        change_password_req = ChangePassword(**(request.json or {}))

        # Change password
        success, response_data, error_msg = await auth_service.async_change_password(
            user_id,
            change_password_req.old_password,
            change_password_req.new_password,
            username,
            created_ip=get_client_ip(request),
            created_user_agent=request.headers.get("User-Agent", ""),
        )

        if not success:
            return json({"error": error_msg}, status=400)

        refresh_token = response_data.pop("refresh_token", None)
        response_data.pop("refresh_expires_in", None)

        response = json({"data": response_data}, status=200)
        if refresh_token:
            set_refresh_cookie(response, refresh_token)
        return response

    except ValidationError as e:
        errors = e.errors()
        error_msgs = [f"{err['loc'][0]}: {err['msg']}" for err in errors]
        return json({"error": ", ".join(error_msgs)}, status=400)
    except Exception as e:
        return json({"error": "Internal server error"}, status=500)


@user_blueprint.get("/settings/persistent")
@openapi.summary("Get authenticated user's persistent settings")
@openapi.secured("BearerAuth")
@openapi.response(
    200, {"application/json": {"description": "Persistent settings object"}}
)
@openapi.response(401, description="Unauthorized")
@openapi.response(404, description="Settings not found")
async def get_persistent_settings(request: Request):
    """
    Get authenticated user's persistent settings from database.

    Method: GET
    Route: /user/settings/persistent

    Requires: Valid JWT token in Authorization header

    Returns:
        {
            "data": {
                "settings": {}
            }
        }
    """
    try:
        # User ID should be set by JWT middleware
        user_id = getattr(request.ctx, "user_id", None)
        if not user_id:
            return json({"error": "Unauthorized"}, status=401)

        user_settings = await postgres_client.async_get_user_settings(user_id)
        if not user_settings:
            return json({"error": "Settings not found"}, status=404)

        return json(
            {"data": {"settings": user_settings.get("settings", {})}}, status=200
        )
    except Exception as e:
        return json({"error": "Internal server error"}, status=500)


@user_blueprint.put("/settings/persistent")
@openapi.summary("Replace authenticated user's persistent settings")
@openapi.secured("BearerAuth")
@openapi.body(
    {
        "application/json": {
            "description": "Settings object",
            "example": {"settings": {}},
        }
    }
)
@openapi.response(200, {"application/json": {"description": "Updated settings"}})
@openapi.response(400, description="Invalid request body")
@openapi.response(401, description="Unauthorized")
async def update_persistent_settings(request: Request):
    """
    Update authenticated user's persistent settings in database.

    Method: PUT
    Route: /user/settings/persistent

    Requires: Valid JWT token in Authorization header

    Request body:
        {
            "settings": {}
        }

    Returns:
        {
            "data": {
                "settings": {}
            }
        }
    """
    try:
        # User ID should be set by JWT middleware
        user_id = getattr(request.ctx, "user_id", None)
        if not user_id:
            return json({"error": "Unauthorized"}, status=401)

        body = request.json
        if not body or "settings" not in body:
            return json(
                {"error": "Invalid request body: 'settings' field required"}, status=400
            )

        settings = body.get("settings")
        if not isinstance(settings, dict):
            return json({"error": "Settings must be a JSON object"}, status=400)

        # Ensure size is reasonable (e.g., less than 1 MB)
        if len(json_lib.dumps(settings)) > 1024 * 1024:
            return json({"error": "Settings too large"}, status=413)

        # Update settings
        success = await postgres_client.async_update_user_settings(user_id, settings)
        if not success:
            return json({"error": "Failed to update settings"}, status=500)

        return json({"data": {"settings": settings}}, status=200)
    except Exception as e:
        return json({"error": "Internal server error"}, status=500)


@user_blueprint.patch("/settings/persistent")
@openapi.summary("Partially update authenticated user's persistent settings")
@openapi.secured("BearerAuth")
@openapi.body(
    {
        "application/json": {
            "description": "Partial settings object to merge",
            "example": {"settings": {}},
        }
    }
)
@openapi.response(200, {"application/json": {"description": "Merged settings"}})
@openapi.response(400, description="Invalid request body")
@openapi.response(401, description="Unauthorized")
async def patch_persistent_settings(request: Request):
    """
    Partially update authenticated user's persistent settings in database.

    Method: PATCH
    Route: /user/settings/persistent

    Requires: Valid JWT token in Authorization header

    Request body:
        {
            "settings": {}
        }

    Returns:
        {
            "data": {
                "settings": {}
            }
        }
    """
    try:
        # User ID should be set by JWT middleware
        user_id = getattr(request.ctx, "user_id", None)
        if not user_id:
            return json({"error": "Unauthorized"}, status=401)

        body = request.json
        if not body or "settings" not in body:
            return json(
                {"error": "Invalid request body: 'settings' field required"}, status=400
            )

        settings_patch = body.get("settings")
        if not isinstance(settings_patch, dict):
            return json({"error": "Settings must be a JSON object"}, status=400)

        # Ensure size is reasonable (e.g., less than 1 MB)
        if len(json_lib.dumps(settings_patch)) > 1024 * 1024:
            return json({"error": "Settings too large"}, status=413)

        # Patch settings and return the merged result.
        merged_settings = await postgres_client.async_patch_user_settings(
            user_id, settings_patch
        )
        if merged_settings is None:
            return json({"error": "Failed to patch settings"}, status=500)

        return json({"data": {"settings": merged_settings}}, status=200)
    except Exception as e:
        return json({"error": "Internal server error"}, status=500)


@user_blueprint.delete("/settings/persistent")
@openapi.summary("Delete authenticated user's persistent settings")
@openapi.secured("BearerAuth")
@openapi.response(200, {"application/json": {"description": "Deletion confirmed"}})
@openapi.response(401, description="Unauthorized")
@openapi.response(404, description="Settings not found")
async def delete_persistent_settings(request: Request):
    """
    Delete authenticated user's persistent settings row from database.

    Method: DELETE
    Route: /user/settings/persistent

    Requires: Valid JWT token in Authorization header

    Returns:
        {
            "data": {
                "deleted": true
            }
        }
    """
    try:
        # User ID should be set by JWT middleware
        user_id = getattr(request.ctx, "user_id", None)
        if not user_id:
            return json({"error": "Unauthorized"}, status=401)

        deleted = await postgres_client.async_delete_user_settings(user_id)
        if deleted is None:
            return json({"error": "Failed to delete settings"}, status=500)
        if not deleted:
            return json({"error": "Settings not found"}, status=404)

        return json({"data": {"deleted": True}}, status=200)
    except Exception as e:
        return json({"error": "Internal server error"}, status=500)
