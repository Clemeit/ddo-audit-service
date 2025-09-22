"""
Character endpoints.
"""

import services.postgres as postgres_client
import services.redis as redis_client
from services.betterstack import character_collections_heartbeat
from models.api import CharacterRequestApiModel, CharacterRequestType
from utils.validation import is_server_name_valid, is_character_name_valid
from business.characters import (
    handle_incoming_characters,
)

from sanic import Blueprint
from sanic.request import Request
from sanic.response import json
from urllib.parse import unquote

from utils.log import logMessage
from constants.guilds import GUILD_NAME_MAX_LENGTH
from constants.server import MAX_CHARACTER_LOOKUP_IDS


character_blueprint = Blueprint("character", url_prefix="/characters", version=1)


# ===== Client-facing endpoints =====
@character_blueprint.get("")
async def get_all_characters(request: Request):
    """
    Method: GET

    Route: /characters

    Description: Get all characters from all servers from the Redis cache.
    """
    try:
        return json({"data": redis_client.get_all_characters_as_dict()})
    except Exception as e:
        return json({"message": str(e)}, status=500)


@character_blueprint.get("/summary")
async def get_online_character_summary(request: Request):
    """
    Method: GET

    Route: /characters/summary

    Description: Get the number of online characters for each server from the Redis cache.
    """
    # TODO: test this method
    try:
        return json({"data": redis_client.get_all_character_counts()})
    except Exception as e:
        return json({"message": str(e)}, status=500)


@character_blueprint.get("/ids")
async def get_online_character_ids(request: Request):
    """
    Method: GET

    Route: /characters/ids

    Description: Gets a list of all online character IDs from the Redis cache.
    This is used to quickly check if a character is online.
    """
    try:
        return json({"data": redis_client.get_all_character_ids()})
    except Exception as e:
        return json({"message": str(e)}, status=500)


@character_blueprint.get("/by-guild-name/<guild_name:str>")
async def get_online_characters_by_guild_name(request: Request, guild_name: str):
    """
    Method: GET

    Route: /characters/by-guild-name/<guild_name:str>

    Description: Get all online characters in a specific guild from the Redis cache.
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
        return json(
            {"data": redis_client.get_characters_by_guild_name_as_dict(guild_name)}
        )
    except Exception as e:
        return json({"message": str(e)}, status=500)


@character_blueprint.get("/by-group-id/<group_id:int>")
async def get_online_characters_by_group_id(request: Request, group_id: int):
    """
    Method: GET

    Route: /characters/by-group-id/<group_id:int>

    Description: Get all online characters in a specific group from the Redis cache.
    """
    if group_id <= 0:
        return json({"message": "Invalid group ID"}, status=400)

    try:
        return json({"data": redis_client.get_characters_by_group_id_as_dict(group_id)})
    except Exception as e:
        return json({"message": str(e)}, status=500)


@character_blueprint.get("/<server_name:str>")
async def get_characters_by_server(request: Request, server_name: str):
    """
    Method: GET

    Route: /characters/<server_name:str>

    Description: Get all characters from a specific server from the Redis cache.
    """
    if not is_server_name_valid(server_name):
        return json({"message": "Invalid server name"}, status=400)

    try:
        return json(
            {"data": redis_client.get_characters_by_server_name_as_dict(server_name)}
        )
    except Exception as e:
        return json({"message": str(e)}, status=500)


@character_blueprint.get("/<character_id:int>")
async def get_character_by_id(request: Request, character_id: int):
    """
    Method: GET

    Route: /characters/<character_id:int>

    Description: Get a specific character from either the Redis cache or the database.
    """
    source = "cache"
    character = redis_client.get_character_by_id_as_dict(character_id)
    if character:
        character["is_online"] = True
    else:
        source = "database"
        character_from_db = postgres_client.get_character_by_id(character_id)
        if character_from_db:
            character_from_db.is_online = False
            character = character_from_db.model_dump()

    if not character:
        return json({"message": "Character not found"}, status=404)

    return json({"data": character, "source": source})


@character_blueprint.get("/ids/<character_ids:str>")
async def get_characters_by_ids(request: Request, character_ids: str):
    """
    Method: GET

    Route: /characters/ids/<character_ids:str>

    Description: Get a list of characters by their IDs from either the Redis cache or the database.
    """
    # validate that all character_ids are numbers
    if not all(character_id.isdigit() for character_id in character_ids.split(",")):
        return json({"message": "Invalid character IDs"}, status=400)

    try:
        character_ids_list = [int(id) for id in character_ids.split(",")]
        if len(character_ids_list) > MAX_CHARACTER_LOOKUP_IDS:
            return json(
                {"message": "Cannot request more than 100 character IDs at once"},
                status=400,
            )
        if not all(
            0 < character_id <= 1099511627775 for character_id in character_ids_list
        ):
            return json({"message": "Invalid character IDs"}, status=400)
        discovered_characters: dict[int, dict] = {}
        cached_character_ids: set[int] = set()
        cached_characters = redis_client.get_characters_by_ids_as_dict(
            character_ids_list
        )
        for character_id, character in cached_characters.items():
            character["is_online"] = True
            discovered_characters[character_id] = character
            cached_character_ids.add(character_id)

        if len(discovered_characters) < len(character_ids_list):
            remaining_ids = set(character_ids_list) - cached_character_ids
            persisted_characters = postgres_client.get_characters_by_ids(
                list(remaining_ids)
            )
            for character in persisted_characters:
                character.is_online = False
                discovered_characters[character.id] = character.model_dump()

    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": discovered_characters})


@character_blueprint.get("/<server_name:str>/<character_name:str>")
async def get_character_by_server_name_and_character_name(
    request: Request, server_name: str, character_name: str
):
    """
    Method: GET

    Route: /characters/<server_name:str>/<character_name:str>

    Description: Get a specific character from a specific server.
    """
    if server_name == "any":
        return await get_characters_by_character_name(character_name)

    if not is_server_name_valid(server_name):
        return json({"message": "Invalid server name"}, status=400)
    if not is_character_name_valid(character_name):
        return json({"message": "Invalid character name"}, status=400)

    character_name = character_name.lower().strip()
    source = "cache"
    found_character = redis_client.get_character_by_name_and_server_name_as_dict(
        character_name, server_name
    )
    if found_character:
        found_character["is_online"] = True
    else:
        source = "database"
        database_character = postgres_client.get_character_by_name_and_server(
            character_name, server_name
        )
        if database_character:
            database_character.is_online = False
            if database_character.is_anonymous:
                return json({"message": "Character is anonymous"}, status=403)
            found_character = database_character.model_dump()

    if not found_character:
        return json({"message": "Character not found"}, status=404)

    return json({"data": found_character, "source": source})


async def get_characters_by_character_name(character_name: str):
    """
    Description: Get and characters that match the specified name. Checks both the cache and the database.
    """
    if not is_character_name_valid(character_name):
        return json({"message": "Invalid character name"}, status=400)

    found_characters: dict[int, dict] = {}

    database_characters = postgres_client.get_characters_by_name(character_name)
    if database_characters:
        for character in database_characters:
            character.is_online = False
            found_characters[character.id] = character.model_dump()

    character_name = character_name.lower().strip()
    cached_characters = redis_client.get_characters_by_name_as_dict(character_name)
    if cached_characters and len(cached_characters.keys()):
        for character_id, character in cached_characters.items():
            character["is_online"] = True
            found_characters[character_id] = character

    if not found_characters:
        return json({"message": "Character not found"}, status=404)

    return json({"data": found_characters})


# ===================================


# ======= Internal endpoints ========
@character_blueprint.post("")
async def set_characters(request: Request):
    """
    Method: POST

    Route: /characters

    Description: Set characters in the Redis cache. Should only be called by DDO Audit Collections. Keyframes.
    """
    # validate request body
    try:
        request_body = CharacterRequestApiModel(**request.json)
    except Exception:
        return json({"message": "Invalid request body"}, status=400)

    # update in redis cache
    try:
        handle_incoming_characters(request_body, CharacterRequestType.set)
    except Exception as e:
        logMessage(
            message="Error handling incoming characters",
            level="error",
            action="set_characters",
            metadata={
                "error": str(e),
            },
        )
        print(f"Error handling incoming characters: {e}")
        return json({"message": str(e)}, status=500)

    try:
        character_collections_heartbeat()
    except Exception:
        pass

    return json({"message": "success"})


@character_blueprint.patch("")
async def update_characters(request: Request):
    """
    Method: PATCH

    Route: /characters

    Description: Update characters in the Redis cache. Should only be called by DDO Audit Collections. Delta updates.
    """

    try:
        request_body = CharacterRequestApiModel(**request.json)
    except Exception:
        return json({"message": "Invalid request body"}, status=400)

    try:
        handle_incoming_characters(request_body, CharacterRequestType.update)
    except Exception as e:
        logMessage(
            message="Error handling incoming characters",
            level="error",
            action="update_characters",
            metadata={
                "error": str(e),
                "request_body": request_body.model_dump() if request_body else None,
            },
        )
        print(f"Error handling incoming characters: {e}")
        return json({"message": str(e)}, status=500)

    try:
        character_collections_heartbeat()
    except Exception:
        pass

    return json({"message": "success"})


# ===================================
