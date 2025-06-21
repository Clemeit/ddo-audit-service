import services.postgres as postgres_client
import services.redis as redis_client
from constants.activity import CharacterActivityType
from constants.server import SERVER_NAMES_LOWERCASE
from models.api import CharacterRequestApiModel, CharacterRequestType
from models.character import Character, CharacterActivity
from models.redis import ServerCharacterData

import datetime


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
    character_activity_by_server_name: dict[str, list[CharacterActivity]] = {
        server_name: [] for server_name in SERVER_NAMES_LOWERCASE
    }
    characters_to_persist_to_db: list[Character] = []

    # organize the characters into their servers
    for character in request_body.characters:
        server_name_lower = character.server_name.lower()
        if not server_name_lower in SERVER_NAMES_LOWERCASE:
            continue

        character.last_update = datetime.datetime.now().isoformat()
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
        deleted_ids_on_server = deleted_ids.intersection(incoming_character_ids)
        character_activity_on_this_server: list[CharacterActivity] = []
        # aggregate_character_activity_for_server(
        #     previous_characters,
        #     incoming_characters,
        #     previous_character_ids,
        #     incoming_character_ids,
        #     deleted_character_ids=deleted_ids_on_server,
        # )
        character_activity_by_server_name[server_name] = (
            character_activity_on_this_server
        )

        # update the redis cache for this server
        if type == CharacterRequestType.set:
            # if it's a set operation, just override the cache completely
            redis_client.set_characters_by_server_name(incoming_characters, server_name)
        elif type == CharacterRequestType.update:
            # if it's an update operation, update the characters and delete
            # any characters that logged off
            redis_client.update_characters_by_server_name(
                incoming_characters, server_name
            )
            redis_client.delete_characters_by_id_and_server_name(
                deleted_ids_on_server, server_name
            )

    # persist on characters that logged off to the database
    persist_deleted_characters_to_db(characters_to_persist_to_db)
    # persist character activity
    persist_character_activity_to_db(character_activity_by_server_name)


def persist_deleted_characters_to_db(characters: list[Character]):
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
) -> list[CharacterActivity]:
    """
    Handle character activity events for a single server at a time.
    """

    # TODO: Probably call .model_dump(exclude_unset=True) on the characters
    # before processing them. If any current fields are left unset, then
    # we really don't care about them.

    update_failure_count = 0
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
                        data={"value": current_characters[character_id].location_id},
                    )
                )
        except Exception:
            update_failure_count += 1

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
            update_failure_count += 1

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
            update_failure_count += 1

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
            update_failure_count += 1

    if update_failure_count > 0:
        print(f"There were a total of {update_failure_count} update failures")

    return activities


def persist_character_activity_to_db(
    activity_events: dict[str, list[CharacterActivity]],
):
    """
    Persist character activity events from all servers to the database.
    """
    postgres_client.add_character_activity(activity_events)
