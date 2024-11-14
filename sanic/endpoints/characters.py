"""
Character endpoints.
"""

import time

import services.postgres as postgres_client
import services.redis as redis_client
from constants.server import SERVER_NAMES_LOWERCASE
from models.api import CharacterRequestApiModel, ServerCharacterDataApiModel
from models.redis import ServerCharactersData
from utils.server import is_server_name_valid
from models.character import Character, CharacterActivity, CharacterActivityType

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
async def get_online_characters(request):
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
            response[server_name] = {
                "character_count": len(server_data.characters),
                "anonymous_count": anon_character_count,
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
        character = character.model_dump()
        character["is_online"] = True

    if not character:
        source = "database"
        character = postgres_client.get_character_by_id(character_id)
        if character:
            character["is_online"] = False

    if not character:
        return json({"message": "Character not found"}, status=404)

    return json({"data": character, "source": source})


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
        character = character.model_dump()
        character["is_online"] = True

    if not character:
        source = "database"
        character = postgres_client.get_character_by_name_and_server(
            character_name, server_name
        )
        if character:
            character["is_online"] = False

    if not character:
        return json({"message": "Character not found"}, status=404)

    return json({"data": character, "source": source})


# ===================================


# ======= Internal endpoints ========
@character_blueprint.post("")
async def set_characters(request: Request):
    """
    Method: POST

    Route: /characters

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
            data = server_data.data  # diff data
            updated_ids = server_data.updated  # updated ids
            deleted_ids = server_data.deleted
            server_characters = ServerCharactersData(
                characters={character.id: character for character in data},
                last_updated=time.time(),
            )

            persist_deleted_characters_to_db_by_id(deleted_ids)

            # ========= Update the activity table in the database =========
            # TODO: create a dict with all the activity events for this server and then
            # pass it to the postgres client to update the activity table
            # =============================================================

            redis_client.set_characters_by_server_name(server_name, server_characters)
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
        body = CharacterRequestApiModel(**request.json)
    except Exception:
        return json({"message": "Invalid request body"}, status=400)

    try:
        all_servers_activity: dict[str, list[CharacterActivity]] = {}
        for server_name, server_data in body.model_dump(exclude_unset=True).items():
            server_data = ServerCharacterDataApiModel(**server_data)
            data = server_data.data  # diff data
            updated_ids = server_data.updated  # updated ids
            # deleted_ids = server_data.deleted  # deleted ids

            # current character data, characters, and character ID for this server
            current_characters_data = ServerCharactersData(
                characters={character.id: character for character in data},
                character_count=len(data),
                last_updated=time.time(),
            )
            current_characters = current_characters_data.characters
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
            persist_deleted_characters_to_db_by_id(list(deleted_ids))
            redis_client.update_characters_by_server_name(
                server_name, current_characters_data
            )
            redis_client.delete_characters_by_server_name_and_character_ids(
                server_name, list(deleted_ids)
            )

        # persist all of the character activity events to the database for
        # all servers at the same time using pipelining
        print(all_servers_activity)
        persist_character_activity_to_db(all_servers_activity)
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"message": "success"})


def get_character_activity_events(
    current_characters: list[Character], previous_characters: list[Character]
):
    relevant_fields = [
        "classes",
        "location",
        "guild_name",
        "server_name",
        "is_online",
    ]


def persist_deleted_characters_to_db_by_id(deleted_ids: list[str]):
    """
    Characters that have just logged off are about to be deleted
    from the cache. Persist them to the database for long-term storage.
    """
    # get all of the characters' last known data from the cache:
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

    # characters that moved to a new location:
    moved_location_ids: set[str] = set()
    for character_id in current_character_ids:
        try:
            if current_characters[character_id].location is None:
                continue

            previous_location_name = ""
            if previous_characters[character_id].location is not None:
                previous_location_name = previous_characters[character_id].location.name
            current_location_name = current_characters[character_id].location.name

            if previous_location_name != current_location_name:
                moved_location_ids.add(character_id)
        except Exception:
            pass
    for character_id in moved_location_ids:
        try:
            activities.append(
                CharacterActivity(
                    id=character_id,
                    activity_type=CharacterActivityType.location,
                    data=current_characters[character_id].location.model_dump(),
                )
            )
        except Exception:
            pass

    # characters that joined a new guild:
    changed_guild_ids: set[str] = set()
    for character_id in current_character_ids:
        try:
            if (
                current_characters[character_id].guild_name
                != previous_characters[character_id].guild_name
            ):
                changed_guild_ids.add(character_id)
        except Exception:
            pass
    for character_id in changed_guild_ids:
        try:
            activities.append(
                CharacterActivity(
                    id=character_id,
                    activity_type=CharacterActivityType.guild_name,
                    data={"value": current_characters[character_id].guild_name},
                )
            )
        except Exception:
            pass

    # characters that changed classes (based on total level):
    leveled_up_ids: set[str] = set()
    for character_id in current_character_ids:
        try:
            if (
                current_characters[character_id].total_level
                != previous_characters[character_id].total_level
            ):
                leveled_up_ids.add(character_id)
        except Exception:
            pass
    for character_id in leveled_up_ids:
        try:
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

    # characters that moved servers:
    moved_server_ids: set[str] = set()
    for character_id in current_character_ids:
        try:
            if current_characters[character_id].server_name is not None and (
                current_characters[character_id].server_name
                != previous_characters[character_id].server_name
            ):
                moved_server_ids.add(character_id)
        except Exception:
            pass
    for character_id in moved_server_ids:
        try:
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
