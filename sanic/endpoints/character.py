"""
Character endpoints.
"""

import time

from constants.server import SERVER_NAMES_LOWERCASE
from models.api import CharacterRequestApiModel, ServerCharacterDataApiModel
from models.redis import ServerCharacters
from services.redis import (
    get_character_by_character_id,
    get_character_by_name_and_server_name,
    get_characters_by_server_name,
    set_characters_by_server_name,
)
from utils.server import is_server_name_valid

from sanic import Blueprint
from sanic.request import Request
from sanic.response import json

character_blueprint = Blueprint("character", url_prefix="/character", version=1)


# ===== Client-facing endpoints =====
@character_blueprint.get("")
async def get_all_characters(request):
    """
    Method: GET

    Route: /character

    Description: Get all characters from all servers from the Redis cache.
    """
    try:
        response = {}
        for server_name in SERVER_NAMES_LOWERCASE:
            response[server_name] = get_characters_by_server_name(
                server_name
            ).model_dump()
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": response})


@character_blueprint.get("/<server_name:str>")
async def get_characters_by_server(request, server_name):
    """
    Method: GET

    Route: /character/<server_name:str>

    Description: Get all characters from a specific server from the Redis cache.
    """
    if not is_server_name_valid(server_name):
        return json({"message": "Invalid server name"}, status=400)

    server_characters = get_characters_by_server_name(server_name)

    return json({"data": server_characters.model_dump()})


@character_blueprint.get("/<character_id:int>")
async def get_character_by_id(request, character_id):
    """
    Method: GET

    Route: /character/<character_id:str>

    Description: Get a specific character from the Redis cache.
    """
    source = "cache"
    character = get_character_by_character_id(character_id)

    if not character:
        # look up in database
        source = "database"
        pass

    if not character:
        return json({"message": "Character not found"}, status=404)

    return json({"data": character.model_dump(), "source": source})


@character_blueprint.get("/<server_name:str>/<character_name:str>")
async def get_character_by_server_name_and_character_name(
    request, server_name, character_name
):
    """
    Method: GET

    Route: /character/<server_name:str>/<character_name:str>

    Description: Get a specific character from a specific server from the Redis cache.
    """
    if not is_server_name_valid(server_name):
        return json({"message": "Invalid server name"}, status=400)

    character = get_character_by_name_and_server_name(character_name, server_name)

    if not character:
        return json({"message": "Character not found"}, status=404)

    return json({"data": character.model_dump()})


# ===================================


# ======= Internal endpoints ========
@character_blueprint.post("")
async def set_characters(request: Request):
    """
    Method: POST

    Route: /character

    Description: Set characters in the Redis cache. Should only be called by DDO Audit Collections. Keyfames.
    """
    # validate request body
    try:
        body = CharacterRequestApiModel(**request.json)
    except Exception:
        return json({"message": "Invalid request body"}, status=400)

    # update in redis cache
    try:
        for server_name, server_data in body.model_dump().items():
            server_data = ServerCharacterDataApiModel(**server_data)
            server_characters = ServerCharacters(
                characters={character.id: character for character in server_data.data},
                character_count=len(server_data.data),
                last_updated=time.time(),
            )
            set_characters_by_server_name(server_name, server_characters)
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"message": "success"})


@character_blueprint.patch("")
async def update_characters(request):
    """
    Method: PATCH

    Route: /character

    Description: Update characters in the Redis cache. Should only be called by DDO Audit Collections. Delta updates.
    """
    # update in redis cache
    return json({"message": "success"})


# ===================================
