"""
Guild endpoints.
"""

import services.postgres as postgres_client

from sanic import Blueprint
from sanic.request import Request
from sanic.response import json

from constants.guilds import GUILD_NAME_MAX_LENGTH


guild_blueprint = Blueprint("guild", url_prefix="/guilds", version=1)


# ===== Client-facing endpoints =====
@guild_blueprint.get("/by-name/<guild_name:str>")
async def get_guilds_by_name(request: Request, guild_name: str):
    """
    Method: GET

    Route: /guilds/by-name/<guild_name>

    Description: Get guilds by name.
    """

    if not guild_name or len(guild_name) > GUILD_NAME_MAX_LENGTH:
        return json({"message": "Invalid guild name."}, status=400)
    if not all(c.isalnum() or c.isspace() or c == "-" for c in guild_name):
        return json(
            {"message": "Guild name must be alphanumeric, spaces, or hyphens."},
            status=400,
        )

    try:
        return json({"data": postgres_client.get_guilds_by_name(guild_name)})
    except Exception as e:
        return json({"message": str(e)}, status=500)
