"""
Character endpoints.
"""

import services.postgres as postgres_client
import services.redis as redis_client
from constants.server import SERVER_NAMES_LOWERCASE
from models.api import CharacterRequestApiModel, CharacterRequestType
from models.character import Character
from utils.validation import is_server_name_valid, is_character_name_valid
from business.characters import (
    handle_incoming_characters,
)

from sanic import Blueprint
from sanic.request import Request
from sanic.response import json


character_blueprint = Blueprint("character", url_prefix="/characters", version=1)


# ===== Client-facing endpoints =====
@character_blueprint.get("")
async def get_all_characters(request):
    """
    Method: GET

    Route: /characters

    Description: Get all characters from all servers from the Redis cache.
    """
    try:
        response = {}
        for server_name in SERVER_NAMES_LOWERCASE:
            response[server_name] = redis_client.get_characters_by_server_name_as_class(
                server_name
            ).model_dump()
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json(response)


@character_blueprint.get("/summary")
async def get_online_character_summary(request):
    """
    Method: GET

    Route: /characters/summary

    Description: Get the number of online characters for each server from the Redis cache.
    """
    # TODO: test this method
    try:
        response = {}
        for server_name in SERVER_NAMES_LOWERCASE:
            character_count = redis_client.get_character_count_by_server_name(
                server_name
            )
            response[server_name] = {
                "character_count": character_count,
            }
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json(response)


@character_blueprint.get("/ids")
async def get_online_character_ids(request):
    """
    Method: GET

    Route: /characters/ids

    Description: Gets a list of all online character IDs from the Redis cache.
    This is used to quickly check if a character is online.
    """
    try:
        response = {}
        for server_name in SERVER_NAMES_LOWERCASE:
            online_character_ids = redis_client.get_online_character_ids_by_server_name(
                server_name
            )
            response[server_name] = {
                "online_character_ids": online_character_ids,
            }
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json(response)


@character_blueprint.get("/<server_name:str>")
async def get_characters_by_server(request, server_name):
    """
    Method: GET

    Route: /characters/<server_name:str>

    Description: Get all characters from a specific server from the Redis cache.
    """
    if not is_server_name_valid(server_name):
        return json({"message": "Invalid server name"}, status=400)

    try:
        server_characters = redis_client.get_characters_by_server_name_as_dict(
            server_name
        )
        print("OUT HERE", server_characters)
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json(server_characters)


@character_blueprint.get("/<character_id:int>")
async def get_character_by_id(request, character_id):
    """
    Method: GET

    Route: /characters/<character_id:int>

    Description: Get a specific character from either the Redis cache or the database.
    """
    source = "cache"
    character = redis_client.get_character_by_character_id(character_id)
    if character:
        character.is_online = True
    else:
        source = "database"
        character = postgres_client.get_character_by_id(character_id)
        if character:
            character.is_online = False

    if not character:
        return json({"message": "Character not found"}, status=404)

    return json({"data": character.model_dump(), "source": source})


@character_blueprint.get("/ids/<character_ids:str>")
async def get_characters_by_ids(request, character_ids: str):
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
        discovered_characters: list[Character] = []
        cached_character_ids: set[int] = set()

        cached_characters = redis_client.get_characters_by_character_ids(
            character_ids_list
        )
        for character in cached_characters:
            character.is_online = True
            discovered_characters.append(character)
            cached_character_ids.add(character.id)

        if len(discovered_characters) < len(character_ids_list):
            remaining_ids = set(character_ids_list) - cached_character_ids
            persisted_characters = postgres_client.get_characters_by_ids(
                list(remaining_ids)
            )
            for character in persisted_characters:
                character.is_online = False
                discovered_characters.append(character)

        dumped_characters = [
            character.model_dump() for character in discovered_characters
        ]
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": dumped_characters})


@character_blueprint.get("/<server_name:str>/<character_name:str>")
async def get_character_by_server_name_and_character_name(
    request, server_name: str, character_name: str
):
    """
    Method: GET

    Route: /characters/<server_name:str>/<character_name:str>

    Description: Get a specific character from a specific server from the Redis cache.
    """
    if not is_server_name_valid(server_name):
        return json({"message": "Invalid server name"}, status=400)
    if not is_character_name_valid(character_name):
        return json({"message": "Invalid character name"}, status=400)

    character_name = character_name.lower().strip()
    source = "cache"
    character = redis_client.get_character_by_name_and_server_name(
        character_name, server_name
    )
    if character:
        character.is_online = True
        if character.is_anonymous:
            # TODO: this will never happen because an online character who
            # is anonymous will have no name, so the character will not be
            # found in the cache.
            return json({"message": "Character is anonymous"}, status=403)
    else:
        source = "database"
        character = postgres_client.get_character_by_name_and_server(
            character_name, server_name
        )
        if character:
            character.is_online = False
            if character.is_anonymous:
                return json({"message": "Character is anonymous"}, status=403)

    if not character:
        return json({"message": "Character not found"}, status=404)

    return json({"data": character.model_dump(), "source": source})


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
        print(f"Error handling incoming characters: {e}")
        return json({"message": str(e)}, status=500)

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
        print(f"Error handling incoming characters: {e}")
        return json({"message": str(e)}, status=500)

    return json({"message": "success"})


# ===================================
