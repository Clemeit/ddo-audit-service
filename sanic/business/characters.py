import services.postgres as postgres_client
import services.redis as redis_client
from constants.activity import CharacterActivityType
from constants.server import SERVER_NAMES_LOWERCASE
from models.api import CharacterRequestApiModel, CharacterRequestType
from models.character import CharacterActivity
from models.redis import ServerCharacterData

from utils.time import get_current_datetime_string
from utils.log import logMessage


def handle_incoming_characters(
    request_body: CharacterRequestApiModel,
    type: CharacterRequestType,
):
    # useful stuff
    deleted_ids = set(request_body.deleted_ids)

    # set up the main dicts
    characters_by_server_name: dict[str, ServerCharacterData] = {
        server_name: ServerCharacterData(characters={})
        for server_name in SERVER_NAMES_LOWERCASE
    }
    all_character_activity: list[dict] = []
    characters_to_persist_to_db: list[dict] = []

    # organize the characters into their servers
    for character in request_body.characters:
        server_name_lower = character.server_name.lower()
        if not server_name_lower in SERVER_NAMES_LOWERCASE:
            continue

        character.last_update = get_current_datetime_string()
        characters_by_server_name[server_name_lower].characters[
            character.id
        ] = character

    # go through each server...
    for server_name, server_character_data in characters_by_server_name.items():
        # useful stuff
        incoming_characters: dict[int, dict] = {
            character_id: character.model_dump()
            for character_id, character in server_character_data.characters.items()
        }
        incoming_character_ids = set(incoming_characters.keys())
        previous_characters = redis_client.get_characters_by_server_name_as_dict(
            server_name
        )
        previous_character_ids = set(previous_characters.keys())

        # handle characters that logged out
        # we can only save characters if they existed previous in the cache,
        # because otherwise we have no data to use
        # all logged-out characters will be persisted to the database at the end
        character_ids_we_can_save = deleted_ids.intersection(previous_character_ids)
        characters_to_persist_to_db.extend(
            [
                character
                for character_id, character in previous_characters.items()
                if character_id in character_ids_we_can_save
            ]
        )

        # handle character activity
        # all character activity will be persisted to the database at the end
        character_activity = aggregate_character_activity_for_server(
            previous_characters,
            incoming_characters,
            previous_character_ids,
            incoming_character_ids,
            deleted_character_ids=character_ids_we_can_save,
        )
        hydrated_characters = hydrate_characters_with_activity(
            incoming_characters, previous_characters, character_activity
        )
        for activities in character_activity.values():
            all_character_activity.extend(activities)

        if type == CharacterRequestType.set:
            redis_client.set_characters_by_server_name(hydrated_characters, server_name)
        elif type == CharacterRequestType.update:
            redis_client.update_characters_by_server_name(
                hydrated_characters, server_name
            )
            redis_client.delete_characters_by_id_and_server_name(
                character_ids_we_can_save, server_name
            )

    # persist on characters that logged off to the database
    persist_deleted_characters_to_db(characters_to_persist_to_db)
    # persist character activity
    persist_character_activity_to_db(all_character_activity)


def persist_deleted_characters_to_db(characters: list[dict]):
    """
    Characters that have just logged off are about to be deleted
    from the cache. Persist them to the database for long-term storage.
    """
    if not characters:
        return
    try:
        postgres_client.add_or_update_characters(characters)
    except Exception as e:
        print(f"Error persisting characters to database: {e}")


def aggregate_character_activity_for_server(
    previous_characters: dict[int, dict],
    current_characters: dict[int, dict],
    previous_character_ids: set[int],
    current_character_ids: set[int],
    deleted_character_ids: set[int],
) -> dict[int, list[dict]]:
    """
    Handle character activity events for a single server at a time. Returns a
    list of character data dict.
    """
    error_messages = []

    # For every previous character that is not in current, they logged off
    # For every current character that is not in previous, they logged on
    # For every character that is in both, check for activity

    logged_on_ids = current_character_ids - previous_character_ids

    character_activity: list[CharacterActivity] = []

    for character_id in deleted_character_ids:
        character_activity.append(
            CharacterActivity(
                character_id=character_id,
                activity_type=CharacterActivityType.STATUS,
                data={"value": False},
            )
        )

    for character_id in logged_on_ids:
        character_activity.append(
            CharacterActivity(
                character_id=character_id,
                activity_type=CharacterActivityType.STATUS,
                data={"value": True},
            )
        )

    possible_activity_ids = current_character_ids - logged_on_ids

    for character_id in possible_activity_ids:
        try:
            current_character = current_characters[character_id]
            previous_character = previous_characters[character_id]

            if not previous_character:
                # can't check for activity in this case
                continue

            # check for location change
            current_location = current_character.get("location_id")
            previous_location = previous_character.get("location_id")
            if current_location != previous_location:
                character_activity.append(
                    CharacterActivity(
                        character_id=character_id,
                        activity_type=CharacterActivityType.LOCATION,
                        data={"value": current_location},
                    )
                )

            # check for guild change
            current_guild = current_character.get("guild")
            previous_guild = previous_character.get("guild")
            if current_guild != previous_guild:
                character_activity.append(
                    CharacterActivity(
                        character_id=character_id,
                        activity_type=CharacterActivityType.GUILD_NAME,
                        data={"value": current_guild},
                    )
                )

            # check for level change
            current_level = current_character.get("total_level")
            previous_level = previous_character.get("total_level")
            if current_level != previous_level:
                character_activity.append(
                    CharacterActivity(
                        character_id=character_id,
                        activity_type=CharacterActivityType.TOTAL_LEVEL,
                        data={
                            "total_level": current_level,
                            "classes": current_character.get("classes"),
                        },
                    )
                )
        except Exception as e:
            print(f"Error processing character {character_id}: {e}")
            error_messages.append(f"Error processing character {character_id}: {e}")

    if len(error_messages) > 0:
        logMessage(
            message="Failed to generate character activity",
            level="error",
            action="aggregate_character_activity_for_server",
            metadata={
                "error_messages": error_messages,
                "failed_count": len(error_messages),
            },
        )
        print(f"Error: {len(error_messages)} failed activity check(s)")

    # return a dict of character_id to a list of activity events (dumped as dicts)
    character_activity_by_id: dict[int, list[dict]] = {}
    for activity in character_activity:
        character_id = activity.character_id
        if character_id not in character_activity_by_id:
            character_activity_by_id[character_id] = []
        character_activity_by_id[character_id].append(activity.model_dump())
    return character_activity_by_id


def persist_character_activity_to_db(
    activity_events: list[dict],
):
    """
    Persist character activity events from all servers to the database.
    """
    postgres_client.add_character_activity(activity_events)


def hydrate_characters_with_activity(
    characters: dict[int, dict],
    previous_characters: dict[int, dict],
    character_activity: dict[int, list[dict]],
) -> dict[int, dict]:
    """
    Hydrate characters with their activity events.
    """
    try:
        characters_with_activity = {}
        for character_id, character in characters.items():
            previous_character_events: list[dict] = previous_characters.get(
                character_id, {}
            ).get("activity", [])
            new_character_event = {
                "timestamp": character.get("last_update"),
                "events": [
                    {
                        "tag": event.get("activity_type"),
                        "data": event.get("data"),
                    }
                    for event in character_activity.get(character_id, [])
                ],
            }
            characters_with_activity[character_id] = {
                **character,
                "activity": previous_character_events + [new_character_event],
            }

        return characters_with_activity
    except Exception as e:
        print(f"Error hydrating characters with activity: {e}")
        return characters
