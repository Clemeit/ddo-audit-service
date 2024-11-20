"""
Character endpoints.
"""

from datetime import datetime

import services.postgres as postgres_client
import services.redis as redis_client
from constants.server import SERVER_NAMES_LOWERCASE
from models.api import CharacterRequestApiModel, CharacterRequestType
from models.character import Character, CharacterActivity, CharacterActivityType
from models.redis import ServerCharactersData
from utils.server import is_server_name_valid

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
            response[server_name] = redis_client.get_characters_by_server_name(
                server_name
            ).model_dump()
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": response})


@character_blueprint.get("/summary")
async def get_online_character_summary(request):
    """
    Method: GET

    Route: /characters/summary

    Description: Get the number of online characters for each server from the Redis cache.
    """
    try:
        response = {}
        for server_name in SERVER_NAMES_LOWERCASE:
            server_data = redis_client.get_characters_by_server_name(server_name)
            anon_character_count = sum(
                1
                for character in server_data.characters.values()
                if character.is_anonymous
            )
            complete_data_count = sum(
                1
                for character in server_data.characters.values()
                if character.is_complete_data
            )
            response[server_name] = {
                "character_count": len(server_data.characters),
                "anonymous_count": anon_character_count,
                "complete_data_count": complete_data_count,
            }
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": response})


@character_blueprint.get("/<server_name:str>")
async def get_characters_by_server(request, server_name):
    """
    Method: GET

    Route: /characters/<server_name:str>

    Description: Get all characters from a specific server from the Redis cache.
    """
    if not is_server_name_valid(server_name):
        return json({"message": "Invalid server name"}, status=400)

    server_characters = redis_client.get_characters_by_server_name(server_name)

    return json({"data": server_characters.model_dump()})


@character_blueprint.get("/<character_id:int>")
async def get_character_by_id(request, character_id):
    """
    Method: GET

    Route: /characters/<character_id:str>

    Description: Get a specific character from the Redis cache.
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
            character.is_complete_data = True

    if not character:
        return json({"message": "Character not found"}, status=404)

    return json({"data": character.model_dump(), "source": source})


@character_blueprint.get("/<server_name:str>/<character_name:str>")
async def get_character_by_server_name_and_character_name(
    request, server_name, character_name
):
    """
    Method: GET

    Route: /characters/<server_name:str>/<character_name:str>

    Description: Get a specific character from a specific server from the Redis cache.
    """
    if not is_server_name_valid(server_name):
        return json({"message": "Invalid server name"}, status=400)

    source = "cache"
    character = redis_client.get_character_by_name_and_server_name(
        character_name, server_name
    )
    if character:
        character.is_online = True
    else:
        source = "database"
        character = postgres_client.get_character_by_name_and_server(
            character_name, server_name
        )
        if character:
            character.is_online = False
            character.is_complete_data = True

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
        return json({"message": str(e)}, status=500)

    return json({"message": "success"})


def handle_incoming_characters(
    request_body: CharacterRequestApiModel,
    type: CharacterRequestType,
):
    all_server_characters: dict[str, ServerCharactersData] = {}
    for server_name in SERVER_NAMES_LOWERCASE:
        all_server_characters[server_name] = ServerCharactersData(
            characters={},
            last_updated=datetime.now(),
        )
    all_servers_activity: dict[str, list[CharacterActivity]] = {}

    # organize characters by server
    for character in request_body.characters:
        server_name = character.server_name.lower()
        character.last_seen = datetime.now()
        if type == CharacterRequestType.set:
            character.is_complete_data = True
        all_server_characters[server_name].characters[character.id] = character

    # process each server's characters
    for server_name, data in all_server_characters.items():
        data: ServerCharactersData
        current_characters = data.characters
        current_character_ids = set(current_characters.keys())

        # previous character data, characters, and character ID for this server
        previous_characters_data = redis_client.get_characters_by_server_name(
            server_name
        )
        previous_characters = previous_characters_data.characters
        previous_character_ids = set(previous_characters.keys())

        # the set of all character IDs from characters that just logged off
        deleted_ids = previous_character_ids - current_character_ids

        # get all of the character activity for this server
        character_activity = aggregate_character_activity_for_server(
            previous_characters,
            current_characters,
            previous_character_ids,
            current_character_ids,
        )
        all_servers_activity[server_name] = character_activity

        # TODO: probably better to use pipelining here instead of making these
        # calls for each server
        persist_deleted_characters_to_db_by_id(deleted_ids)
        if type == CharacterRequestType.set:
            redis_client.set_characters_by_server_name(server_name, data)
        elif type == CharacterRequestType.update:
            redis_client.update_characters_by_server_name(server_name, data)
            redis_client.delete_characters_by_server_name_and_character_ids(
                server_name, list(deleted_ids)
            )

    # persist all of the character activity events to the database for
    # all servers at the same time using pipelining
    persist_character_activity_to_db(all_servers_activity)


def persist_deleted_characters_to_db_by_id(deleted_ids: list[str]):
    """
    Characters that have just logged off are about to be deleted
    from the cache. Persist them to the database for long-term storage.
    """
    # get all of the characters' last known data from the cache:
    print(deleted_ids)
    deleted_characters = redis_client.get_characters_by_character_ids(deleted_ids)
    # add all of the characters that are about to be deleted to the database:
    postgres_client.add_or_update_characters(deleted_characters)


def aggregate_character_activity_for_server(
    previous_characters: dict[str, Character],
    current_characters: dict[str, Character],
    previous_character_ids: set[str],
    current_character_ids: set[str],
) -> list[CharacterActivity]:
    """
    Handle character activity events for a single server at a time.
    """

    # TODO: Probably call .model_dump(exclude_unset=True) on the characters
    # before processing them. If any current fields are left unset, then
    # we really don't care about them.

    activities: list[CharacterActivity] = []

    # characters that just logged in:
    new_ids = current_character_ids - previous_character_ids
    for character_id in new_ids:
        activities.append(
            CharacterActivity(
                id=character_id,
                activity_type=CharacterActivityType.is_online,
                data={"value": True},
            )
        )

    # characters that just logged off:
    deleted_ids = previous_character_ids - current_character_ids
    for character_id in deleted_ids:
        activities.append(
            CharacterActivity(
                id=character_id,
                activity_type=CharacterActivityType.is_online,
                data={"value": False},
            )
        )

    # character ids that potentially have activity:
    # (we don't care about character's that just logged in)
    # TODO: for characters that are just logging in, we should
    # actually diff their data against whatever data is store
    # in the database. Scenario: character logs off, changes
    # server, and then logs in.
    potential_activity_ids = current_character_ids - new_ids
    for character_id in potential_activity_ids:
        # location change
        try:

            def is_location_change(cur_char: Character, prev_char: Character) -> bool:
                if cur_char.location is None:
                    return False

                previous_location_name = ""
                if prev_char.location is not None:
                    previous_location_name = prev_char.location.name
                current_location_name = cur_char.location.name

                if previous_location_name != current_location_name:
                    return True
                return False

            if is_location_change(
                current_characters[character_id], previous_characters[character_id]
            ):
                activities.append(
                    CharacterActivity(
                        id=character_id,
                        activity_type=CharacterActivityType.location,
                        data=current_characters[character_id].location.model_dump(),
                    )
                )
        except Exception:
            pass

        # guild change
        try:
            if current_characters[character_id].guild_name is not None and (
                current_characters[character_id].guild_name
                != previous_characters[character_id].guild_name
            ):
                activities.append(
                    CharacterActivity(
                        id=character_id,
                        activity_type=CharacterActivityType.guild_name,
                        data={"value": current_characters[character_id].guild_name},
                    )
                )
        except Exception:
            pass

        # classes changed (level up)
        try:
            if current_characters[character_id].total_level is not None and (
                current_characters[character_id].total_level
                != previous_characters[character_id].total_level
            ):
                class_list = [
                    class_list_item.model_dump()
                    for class_list_item in current_characters[character_id].classes
                ]
                activities.append(
                    CharacterActivity(
                        id=character_id,
                        activity_type=CharacterActivityType.classes,
                        data={
                            "total_level": current_characters[character_id].total_level,
                            "classes": class_list,
                        },
                    )
                )
        except Exception:
            pass

        # server change
        try:
            if current_characters[character_id].server_name is not None and (
                current_characters[character_id].server_name
                != previous_characters[character_id].server_name
            ):
                activities.append(
                    CharacterActivity(
                        id=character_id,
                        activity_type=CharacterActivityType.server_name,
                        data={"value": current_characters[character_id].server_name},
                    )
                )
        except Exception:
            pass

    return activities


def persist_character_activity_to_db(
    activity_events: dict[str, list[CharacterActivity]]
):
    """
    Persist character activity events from all servers to the database.
    """
    postgres_client.add_character_activity(activity_events)


# ===================================
