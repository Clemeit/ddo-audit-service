"""
User endpoints.
"""

import services.redis as redis_client

from sanic import Blueprint
from sanic.response import json

import secrets, string

user_blueprint = Blueprint("user", url_prefix="/user", version=1)


# ===== Client-facing endpoints =====
@user_blueprint.post("/settings")
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
            return json({"message": len(request.body)}, status=413)

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
    except Exception as e:
        return json({"message": "Internal server error"}, status=500)

    return json({"data": {"user_id": user_id}})


@user_blueprint.get("/settings/<user_id:str>")
async def get_user_settings(request, user_id: str):
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
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": settings})
