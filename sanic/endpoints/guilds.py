"""
Guild endpoints.
"""

import services.postgres as postgres_client
from urllib.parse import unquote

from sanic import Blueprint
from sanic.request import Request
from sanic.response import json

from constants.guilds import GUILD_NAME_MAX_LENGTH, GUILD_PAGE_LENGTH
import utils.guilds as guild_utils


guild_blueprint = Blueprint("guild", url_prefix="/guilds", version=1)


# ===== Client-facing endpoints =====
@guild_blueprint.get("/by-name/<guild_name:str>")
async def get_guilds_by_name(request: Request, guild_name: str):
    """
    Method: GET

    Route: /guilds/by-name/<guild_name>

    Description: Get guilds by name.
    """
    try:
        guild_name = unquote(guild_name)
    except Exception as e:
        return json({"message": "Invalid guild name."}, status=400)

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


@guild_blueprint.get("")
async def get_all_guilds(request: Request):
    """
    Method: GET

    Route: /guilds

    Description: Get all guild names. Paginated.
    """
    try:
        page = int(request.args.get("page", 1))
        name_filter = request.args.get("name", "").strip()
        server_filter = request.args.get("server", "").strip()

        if page < 1:
            raise ValueError
        offset = (page - 1) * GUILD_PAGE_LENGTH
        guild_data = guild_utils.get_all_guilds()
        # filter the results
        if name_filter:
            guild_data = [
                g for g in guild_data if name_filter.lower() in g["guild_name"].lower()
            ]
        if server_filter:
            guild_data = [
                g
                for g in guild_data
                if server_filter.lower() in g["server_name"].lower()
            ]
        paged_data = guild_data[offset : offset + GUILD_PAGE_LENGTH]
        return json(
            {
                "data": paged_data,
                "page": page,
                "page_length": GUILD_PAGE_LENGTH,
                "filtered_length": len(paged_data),
                "total": len(guild_data),
            }
        )
    except ValueError:
        return json({"message": "Invalid page number."}, status=400)
    except Exception as e:
        return json({"message": str(e)}, status=500)
