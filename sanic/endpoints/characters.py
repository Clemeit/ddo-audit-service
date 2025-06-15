"""
Character endpoints.
"""

import services.postgres as postgres_client
import services.redis as redis_client
from constants.activity import CharacterActivityType
from constants.server import SERVER_NAMES_LOWERCASE
from models.api import CharacterRequestApiModel, CharacterRequestType
from models.character import Character, CharacterActivity
from models.redis import ServerCharactersData
from utils.validation import is_server_name_valid, is_character_name_valid

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
    # deleted and updated keys (for characters that logged off or had avtivity)
    deleted_ids = set(request_body.deleted_ids)

    all_server_characters: dict[str, ServerCharactersData] = {}
    for server_name in SERVER_NAMES_LOWERCASE:
        all_server_characters[server_name] = ServerCharactersData(
            characters={},
        )
    all_servers_activity: dict[str, list[CharacterActivity]] = {}

    # update last_update timestamp for each server
    for server_name, value in request_body.last_update_timestamps.items():
        all_server_characters[server_name].last_update = value

    # organize characters by server
    for character in request_body.characters:
        server_name = character.server_name.lower()

        # the last time the player was seen was the last time data was
        # pulled from the game client
        character.last_update = all_server_characters[server_name].last_update

        all_server_characters[server_name].characters[character.id] = character

    # process each server's characters
    for server_name, data in all_server_characters.items():
        current_characters = data.characters
        current_character_ids = set(current_characters.keys())

        # previous character data, characters, and character ID for this server
        previous_characters_data = redis_client.get_characters_by_server_name_as_class(
            server_name
        )
        previous_characters = previous_characters_data.characters
        previous_character_ids = set(previous_characters.keys())

        # a set of character IDs from the incoming request that were
        # also present in the previous characters (i.e. valid)
        deleted_ids_on_server = deleted_ids.intersection(previous_character_ids)

        # get all of the character activity for this server
        character_activity = aggregate_character_activity_for_server(
            previous_characters,
            current_characters,
            previous_character_ids,
            current_character_ids,
            deleted_character_ids=deleted_ids_on_server,
        )
        all_servers_activity[server_name] = character_activity

        # TODO: probably better to use pipelining here instead of making these
        # calls for each server
        try:
            persist_deleted_characters_to_db_by_server_name_and_ids(
                server_name, deleted_ids_on_server
            )
        except Exception as e:
            print(
                f"Error persisting deleted characters for server {server_name}: {e}"
            )
        if type == CharacterRequestType.set:
            redis_client.set_characters_by_server_name(server_name, data)
        elif type == CharacterRequestType.update:
            redis_client.update_characters_by_server_name(server_name, data)
            redis_client.delete_characters_by_server_name_and_character_ids(
                server_name, deleted_ids
            )

    # persist all of the character activity events to the database for
    # all servers at the same time using pipelining
    persist_character_activity_to_db(all_servers_activity)


def persist_deleted_characters_to_db_by_server_name_and_ids(
    server_name: str, deleted_keys: set[int]
):
    """
    Characters that have just logged off are about to be deleted
    from the cache. Persist them to the database for long-term storage.
    """
    if not deleted_keys:
        return

    # get all of the characters' last known data from the cache:
    deleted_characters = redis_client.get_characters_by_server_name_and_character_ids(
        server_name, deleted_keys
    )
    # add all of the characters that are about to be deleted to the database:
    postgres_client.add_or_update_characters(deleted_characters)
    # print(deleted_keys)
    # print(deleted_characters)


def aggregate_character_activity_for_server(
    previous_characters: dict[int, Character],
    current_characters: dict[int, Character],
    previous_character_ids: set[int],
    current_character_ids: set[int],
    deleted_character_ids: set[int],
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
                activity_type=CharacterActivityType.status,
                data={"value": True},
            )
        )

    # characters that just logged off:
    for character_id in deleted_character_ids:
        activities.append(
            CharacterActivity(
                id=character_id,
                activity_type=CharacterActivityType.status,
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
                if cur_char.location_id is None:
                    return False

                previous_location_id: int = 0
                if prev_char.location_id is not None:
                    previous_location_id = prev_char.location_id
                current_location_id = cur_char.location_id

                if previous_location_id != current_location_id:
                    return True
                return False

            if is_location_change(
                current_characters[character_id], previous_characters[character_id]
            ):
                activities.append(
                    CharacterActivity(
                        id=character_id,
                        activity_type=CharacterActivityType.location,
                        data=current_characters[character_id].location_id,
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
                        activity_type=CharacterActivityType.total_level,
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
    activity_events: dict[str, list[CharacterActivity]],
):
    """
    Persist character activity events from all servers to the database.
    """
    postgres_client.add_character_activity(activity_events)


# ===================================
