"""
Guild endpoints.
"""

import services.postgres as postgres_client
import services.redis as redis_client
from urllib.parse import unquote

from sanic import Blueprint
from sanic.request import Request
from sanic.response import json

from constants.guilds import GUILD_NAME_MAX_LENGTH, GUILD_PAGE_LENGTH
import utils.guilds as guild_utils
from constants.server import SERVER_NAMES_LOWERCASE


guild_blueprint = Blueprint("guild", url_prefix="/guilds", version=1)


# ===== Client-facing endpoints =====
@guild_blueprint.get("/by-name/<guild_name:str>")
async def get_guilds_by_name_deprecated(request: Request, guild_name: str):
    """
    Method: GET

    Route: /guilds/by-name/<guild_name>

    Description: Get guilds by name.
    """
    try:
        guild_name = unquote(guild_name)
    except Exception as e:
        return json({"message": "Invalid guild name."}, status=400)

    if not guild_utils.validate_guild_name(guild_name):
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


@guild_blueprint.get("/<server_name:str>/<guild_name:str>")
async def get_guild_by_server_name_and_guild_name(
    request: Request, server_name: str, guild_name: str
):
    """
    Method: GET

    Route: /guilds/<server_name>/<guild_name>

    Description: Get guild information including name, server, character count, last
    update timestamp, and current online characters. If the authorization header is
    provided, any guild that the user is a member of will be hydrated with additional
    information.
    """
    # Validate server name
    if server_name.lower() not in SERVER_NAMES_LOWERCASE:
        return json({"message": "Invalid server name."}, status=400)

    # Validate guild name
    try:
        guild_name = unquote(guild_name)
    except Exception as e:
        return json({"message": "Invalid guild name."}, status=400)

    if not guild_utils.validate_guild_name(guild_name):
        return json(
            {"message": "Guild name must be alphanumeric, spaces, or hyphens."},
            status=400,
        )
    try:
        guild_data = postgres_client.get_guild_by_server_name_and_guild_name(
            server_name, guild_name
        )
        if not guild_data:
            return json({"data": None}, status=404)
        online_characters = redis_client.get_characters_by_guild_name_as_dict(
            guild_name, server_name, True
        )
        guild_data.update({"online_characters": online_characters})
        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return json({"data": guild_data})
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("page_size", 50))
        sort_by = request.args.get("sort_by", "last_save")
        if page < 1:
            raise ValueError
        if page_size < 1 or page_size > 200:
            raise ValueError
        if sort_by not in ("last_save", "total_level", "name"):
            raise ValueError
        # if auth header is provided, hydrate guilds that the user is a member of
        verified_character_id = postgres_client.get_character_id_by_access_token(
            auth_header
        )
        verified_character = (
            postgres_client.get_character_by_id(verified_character_id)
            if verified_character_id
            else None
        )
        if not verified_character:
            return json({"data": guild_data})
        # Ensure the verified character is actually in the requested guild
        if (
            verified_character.guild_name is None
            or verified_character.server_name is None
            or not verified_character.guild_name.strip()
            or not verified_character.server_name.strip()
            or verified_character.server_name.lower() != server_name.lower()
            or verified_character.guild_name.lower() != guild_name.lower()
        ):
            return json({"data": guild_data})

        # The verified character is in the requested guild, so we can
        # safely add member information
        member_ids = postgres_client.get_character_ids_by_server_and_guild(
            server_name, guild_name, page, page_size, sort_by
        )
        guild_data.update(
            {
                "is_member": True,
                "member_ids": member_ids,
            }
        )
        return json({"data": guild_data})
    except ValueError:
        return json({"message": "Invalid page number, page size, or sort."}, status=400)
    except Exception as e:
        return json({"message": str(e)}, status=500)
