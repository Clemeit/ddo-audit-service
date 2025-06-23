"""
Service to interface with the Redis server.
"""

import os
import random

from constants.server import SERVER_NAMES_LOWERCASE
from models.character import Character
from models.lfm import Lfm
from models.redis import (
    ServerInfo,
    ServerCharacterData,
    ServerLfmData,
    ServerSpecificInfo,
    KnownAreasModel,
    KnownQuestsModel,
    ServerInfoDict,
    RedisKeys,
    REDIS_KEY_TYPE_MAPPING,
)
from time import time
from constants.redis import VALID_AREA_CACHE_LIFETIME, VALID_QUEST_CACHE_LIFETIME
from models.area import Area
from models.service import News, PageMessage
from models.quest import Quest

import json
from typing import Optional, Any

from pydantic import BaseModel

import redis

REDIS_HOST = os.getenv("REDIS_HOST")
REDIS_PORT = int(os.getenv("REDIS_PORT"))


class RedisSingleton:
    _instance = None
    client: redis.Redis

    def __new__(cls):
        if cls._instance is None:
            print("Creating Redis client...")
            cls._instance = super(RedisSingleton, cls).__new__(cls)
            cls.client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT)
        return cls._instance

    def initialize(self):
        print("Getting the Redis cache ready...")
        self.client.flushall()

        # we want to initialize the cache with keys
        for key, value in REDIS_KEY_TYPE_MAPPING.items():
            key = key.value if isinstance(key, RedisKeys) else key

            # value is a class type, so we need to instantiate it if it's a BaseModel
            if isinstance(value, type) and issubclass(value, BaseModel):
                value = value()

            # model_dump if inherits from BaseModel, else just value
            if hasattr(value, "model_dump"):
                self.client.json().set(key, path="$", obj=value.model_dump())
            elif isinstance(value, dict):
                self.client.json().set(key, path="$", obj=value)
            else:
                self.client.json().set(key, path="$", obj=value)

    def close(self):
        self.client.close()

    # def hydrate_key(self, key: RedisKeys, server_name: Optional[str]) -> str:
    #     if server_name and key in {RedisKeys.CHARACTERS, RedisKeys.LFMS}:
    #         return key.value.format(server=server_name)
    #     else:
    #         return key.value

    # def get_as_class(self, key: RedisKeys, server_name: Optional[str] = None):
    #     """
    #     Retrieve and deserialize data from Redis based on the expected type.

    #     Args:
    #         key: The Redis key (as an Enum).
    #         server: Optional server name for server-specific keys.

    #     Returns:
    #         The deserialized data in the expected type, or None if the key doesn't exist.
    #     """
    #     try:
    #         key = self.hydrate_key(key, server_name)

    #         # Get the expected type for the key
    #         expected_type = REDIS_KEY_TYPE_MAPPING.get(key)
    #         if not expected_type:
    #             raise ValueError(f"Key '{key}' is not in the Redis key type mapping.")

    #         # Retrieve the raw data from Redis
    #         raw_data = self.client.json().get(key)
    #         if raw_data is None:
    #             return None

    #         # Deserialize the data based on the expected type
    #         if issubclass(expected_type, BaseModel):
    #             return expected_type.model_validate(raw_data)  # TODO: test
    #         elif expected_type == dict:
    #             return json.loads(raw_data)
    #         else:
    #             return raw_data
    #     except Exception as e:
    #         print(
    #             f"Error retrieving data from Redis. key: {key}, expected_type: {expected_type}, message: {e}"
    #         )
    #         return None

    # def get_as_dict(self, key: RedisKeys, server_name: Optional[str] = None):
    #     """
    #     Retrieve and deserialize data from Redis as a dictionary.

    #     Args:
    #         key: The Redis key (as an Enum).
    #         server: Optional server name for server-specific keys.

    #     Returns:
    #         The deserialized data as a dictionary, or None if the key doesn't exist.
    #     """
    #     try:
    #         key = self.hydrate_key(key, server_name)

    #         # Retrieve the raw data from Redis
    #         raw_data = self.client.json().get(key)

    #         # Validate and serialize as needed
    #         if raw_data is None:
    #             return None
    #         if isinstance(raw_data, dict):
    #             return raw_data
    #         try:
    #             return json.loads(raw_data)
    #         except json.JSONDecodeError:
    #             return raw_data
    #     except Exception as e:
    #         print(f"Error retrieving data from Redis. key: {key}, message: {e}")
    #         return None

    # def set_class(self, key: RedisKeys, data: Any, server_name: Optional[str] = None):
    #     """
    #     Serializes and sets data to Redis.

    #     Args:
    #         key: The Redis key (as an Enum).
    #         data: The data to set.
    #         server: Optional server name for server-specific keys.
    #     """
    #     try:
    #         key = self.hydrate_key(key, server_name)
    #         print(key)

    #         # Get the expected type for the key
    #         expected_type = REDIS_KEY_TYPE_MAPPING.get(key)
    #         if not expected_type:
    #             raise ValueError(f"Key '{key}' is not in the Redis key type mapping.")
    #         print(expected_type)

    #         # Check if the data is the correct type
    #         if not type(data) is expected_type:
    #             raise TypeError(f"Data is not the correct type {expected_type}.")

    #         raw_data = data
    #         if issubclass(expected_type, BaseModel):
    #             raw_data = data.model_dump()

    #         print(raw_data)

    #         self.client.json().set(f"{server_name}:characters", path="$", obj=raw_data)
    #     except Exception as e:
    #         print(f"Error setting data to Redis. key: {key}, message: {e}")

    # def merge_class(self, key: RedisKeys, data: Any, server_name: Optional[str] = None):
    #     """
    #     Serializes and sets data to Redis.

    #     Args:
    #         key: The Redis key (as an Enum).
    #         data: The data to set.
    #         server: Optional server name for server-specific keys.
    #     """
    #     try:
    #         key = self.hydrate_key(key, server_name)
    #         print(key)

    #         # Get the expected type for the key
    #         expected_type = REDIS_KEY_TYPE_MAPPING.get(key)
    #         if not expected_type:
    #             raise ValueError(f"Key '{key}' is not in the Redis key type mapping.")
    #         print(expected_type)

    #         # Check if the data is the correct type
    #         if not type(data) is expected_type:
    #             raise TypeError(f"Data is not the correct type {expected_type}.")

    #         raw_data = data
    #         if issubclass(expected_type, BaseModel):
    #             raw_data = data.model_dump()

    #         print(raw_data)

    #         self.client.json().merge(
    #             f"{server_name}:characters", path="$", obj=raw_data
    #         )
    #     except Exception as e:
    #         print(f"Error setting data to Redis. key: {key}, message: {e}")

    # def set_dict(self, key: RedisKeys, data: Any, server_name: Optional[str] = None):
    #     """
    #     Serializes and sets data to Redis.

    #     Args:
    #         key: The Redis key (as an Enum).
    #         data: The data to set.
    #         server: Optional server name for server-specific keys.
    #     """
    #     try:
    #         key = self.hydrate_key(key)

    #         # Get the expected type for the key
    #         expected_type = REDIS_KEY_TYPE_MAPPING.get(key)
    #         if not expected_type:
    #             raise ValueError(f"Key '{key}' is not in the Redis key type mapping.")

    #         raw_data = data
    #         if issubclass(expected_type, BaseModel):
    #             raw_data = data.model_dump()

    #         self.client.json().set(f"{server_name}:characters", path="$", obj=raw_data)
    #     except Exception as e:
    #         print(f"Error setting data to Redis. key: {key}, message: {e}")

    # def get_obj_len(self, key: RedisKeys, path: str, server_name: Optional[str] = None):
    #     key = self.hydrate_key(key, server_name)
    #     self.client.json().objlen(key.value, path)

    # def get_obj_keys(
    #     self, key: RedisKeys, path: str, server_name: Optional[str] = None
    # ):
    #     key = self.hydrate_key(key, server_name)
    #     self.client.json().objkeys(key.value, path)

    def get_client(self):
        return self.client


redis_singleton = RedisSingleton()


def get_redis_client():
    return redis_singleton.get_client()


def initialize_redis():
    redis_singleton.initialize()


def close_redis():
    redis_singleton.close()


# ================================


# ========= CHARACTERS ===========
def get_all_characters_as_dict() -> dict[str, dict[int, dict]]:
    """Get a dict of server name to a dict of character id to character dict"""
    all_characters: dict[str, list[dict]] = {}
    for server_name in SERVER_NAMES_LOWERCASE:
        all_characters[server_name] = get_characters_by_server_name_as_dict(server_name)
    return all_characters


def get_all_characters() -> dict[str, dict[int, Character]]:
    """
    Get a dict of server name to a dict of character id to character object.

    THIS IS EXPENSIVE! Don't use this unless there's a good reason to.
    """
    all_characters: dict[str, dict[int, Character]] = {}
    for server_name in SERVER_NAMES_LOWERCASE:
        all_characters[server_name] = get_characters_by_server_name(server_name)
    return all_characters


def get_characters_by_server_name_as_dict(server_name: str) -> dict[int, dict]:
    """Get a dict of character id to character dict"""
    redis_data = (
        get_redis_client()
        .json()
        .get(RedisKeys.CHARACTERS.value.format(server=server_name.lower()))
    )
    return {int(k): v for k, v in redis_data.items()} if redis_data else {}


def get_characters_by_server_name(server_name: str) -> dict[int, Character]:
    """
    Get a dict of character id to character object

    THIS IS EXPENSIVE! Don't use this unless there's a good reason to.
    """
    characters_by_server_name = get_characters_by_server_name_as_dict(server_name)
    return {
        character_id: Character(**character)
        for [character_id, character] in characters_by_server_name.items()
    }


def get_all_character_counts() -> dict[str, int]:
    """Get a dict of server name to character count"""
    character_counts: dict[str, int] = {}
    for server_name in SERVER_NAMES_LOWERCASE:
        character_counts[server_name] = get_character_count_by_server_name(server_name)
    return character_counts


def get_character_count_by_server_name(server_name: str) -> int:
    """Get the number of characters by server name"""
    return (
        get_redis_client()
        .json()
        .objlen(RedisKeys.CHARACTERS.value.format(server=server_name.lower()))
    )


def get_all_character_ids() -> dict[str, list[int]]:
    """Get a list of all online characters' IDs"""
    character_ids: dict[str, list[int]] = {}
    for server_name in SERVER_NAMES_LOWERCASE:
        character_ids[server_name] = get_character_ids_by_server_name(server_name)
    return character_ids


def get_character_ids_by_server_name(server_name: str) -> list[int]:
    """Get a list of all online characters' IDs by server name"""
    keys = (
        get_redis_client()
        .json()
        .objkeys(RedisKeys.CHARACTERS.value.format(server=server_name.lower()))
    )
    return [int(key) for key in keys if key.isdigit()]


def get_character_by_name_and_server_name_as_dict(
    character_name: str, server_name: str
) -> dict | None:
    """Get a character dict by name and server name"""
    character_name = character_name.lower()
    server_characters = get_characters_by_server_name_as_dict(server_name)
    for character_id, character in server_characters.items():
        if character and character.get("name").lower() == character_name:
            return character
    return None


def get_character_by_name_and_server_name(
    character_name: str, server_name: str
) -> Character | None:
    """Get a character object by name and server name"""
    character = get_character_by_name_and_server_name_as_dict(
        character_name, server_name
    )
    if character:
        return Character(**character)
    return None


def get_character_by_id_as_dict(character_id: int) -> dict | None:
    """Get a character dict by character ID"""
    for server_name in SERVER_NAMES_LOWERCASE:
        server_character_ids = get_character_ids_by_server_name(server_name)
        if character_id in server_character_ids:
            try:
                # this get() will throw an error if the key character_id doesn't exist,
                # which could theoretically happen if the character logged out between
                # the id lookup and this get()
                return (
                    get_redis_client()
                    .json()
                    .get(
                        RedisKeys.CHARACTERS.value.format(server=server_name),
                        character_id,
                    )
                )
            except Exception:
                return None
    return None


def get_character_by_id(character_id: int) -> Character | None:
    """Get a character object by character ID"""
    character = get_character_by_id_as_dict(character_id)
    if character:
        return Character(**character)
    return None


def get_characters_by_ids_as_dict(character_ids: int) -> dict[int, dict]:
    """Get a dict of character id to character dict"""
    characters: dict[int, dict] = {}
    for server_name in SERVER_NAMES_LOWERCASE:
        server_characters = get_characters_by_server_name_as_dict(server_name)
        for character_id in character_ids:
            if character_id in server_characters.keys():
                characters[character_id] = server_characters[character_id]
    return characters


def get_characters_by_ids(character_id: int) -> dict[int, Character]:
    """Get a dict of character id to character object"""
    characters: dict[int, Character] = {}
    for character_id, character in get_characters_by_ids_as_dict():
        characters[character_id] = Character(**character)
    return characters


def get_characters_by_name_as_dict(character_name: str) -> dict[int, dict]:
    """Get all character dicts matching a character name"""
    character_name_lower = character_name.lower()
    characters: dict[int, dict] = {}
    for server_name in SERVER_NAMES_LOWERCASE:
        server_characters = get_characters_by_server_name_as_dict(server_name)
        matching_characters = [
            character
            for character in server_characters.values()
            if character and character.get("name").lower() == character_name_lower
        ]
        for matching_character in matching_characters:
            characters[matching_character.get("id")] = matching_character
    return characters


def get_characters_by_name(character_name: str) -> dict[int, Character]:
    """Get all character objects matching a character name"""
    characters = get_characters_by_name_as_dict(character_name)
    return {
        character_id: Character(**character)
        for [character_id, character] in characters.items()
    }


def set_characters_by_server_name(server_characters: dict[int, dict], server_name: str):
    """Set all character objects by server name"""
    with get_redis_client() as client:
        client.json().set(
            name=RedisKeys.CHARACTERS.value.format(server=server_name.lower()),
            path="$",
            obj=server_characters,
        )


def update_characters_by_server_name(
    server_characters: dict[int, dict], server_name: str
):
    """Update all character objects by server name"""
    with get_redis_client() as client:
        client.json().merge(
            name=RedisKeys.CHARACTERS.value.format(server=server_name.lower()),
            path="$",
            obj=server_characters,
        )


def delete_characters_by_id_and_server_name(character_ids: list[int], server_name: str):
    """Delete characters by ID and server name"""
    if not character_ids:
        return

    server_name = server_name.lower()
    with get_redis_client() as client:
        with client.pipeline() as pipeline:
            for character_id in character_ids:
                pipeline.json().delete(
                    key=RedisKeys.CHARACTERS.value.format(server=server_name.lower()),
                    path=character_id,
                )
            pipeline.execute()


# ========= CHARACTERS ===========


# ============ LFMs ==============
def get_all_lfms_as_dict() -> dict[str, dict[int, dict]]:
    """Get a dict of server name to a dict of lfm id to lfm dict"""
    all_lfms: dict[str, list[dict]] = {}
    for server_name in SERVER_NAMES_LOWERCASE:
        all_lfms[server_name] = get_lfms_by_server_name_as_dict(server_name)
    return all_lfms


def get_all_lfms() -> dict[str, dict[int, Lfm]]:
    """
    Get a dict of server name to a dict of lfm id to lfm object.

    THIS IS EXPENSIVE! Don't use this unless there's a good reason to.
    """
    all_lfms: dict[str, dict[int, Lfm]] = {}
    for server_name in SERVER_NAMES_LOWERCASE:
        all_lfms[server_name] = get_lfms_by_server_name(server_name)
    return all_lfms


def get_lfms_by_server_name_as_dict(server_name: str) -> dict[int, dict]:
    """Get a dict of"""
    redis_data = (
        get_redis_client()
        .json()
        .get(RedisKeys.LFMS.value.format(server=server_name.lower()))
    )
    return {int(k): v for k, v in redis_data.items()} if redis_data else {}


def get_lfms_by_server_name(server_name: str) -> dict[int, Lfm]:
    """
    Get a dict of lfm id to lfm object

    THIS IS EXPENSIVE! Don't use this unless there's a good reason to.
    """
    lfms_by_server_name = get_lfms_by_server_name_as_dict(server_name)
    return {lfm_if: Lfm(**lfm) for lfm_if, lfm in lfms_by_server_name.items()}


def get_all_lfm_counts() -> dict[str, int]:
    """Get a dict of server name to lfm count"""
    lfm_counts: dict[str, int] = {}
    for server_name in SERVER_NAMES_LOWERCASE:
        lfm_counts[server_name] = get_lfm_count_by_server_name(server_name)
    return lfm_counts


def get_lfm_count_by_server_name(server_name: str) -> int:
    """Get the number of lfms by server name"""
    return (
        get_redis_client()
        .json()
        .objlen(RedisKeys.LFMS.value.format(server=server_name.lower()))
    )


def set_lfms_by_server_name(server_lfms: dict[int, dict], server_name: str):
    """Set all lfm objects by server name"""
    with get_redis_client() as client:
        client.json().set(
            RedisKeys.LFMS.value.format(server=server_name.lower()),
            path="$",
            obj=server_lfms,
        )


def update_lfms_by_server_name(server_lfms: dict[int, dict], server_name: str):
    """Update all lfm objects by server name"""
    with get_redis_client() as client:
        client.json().merge(
            name=RedisKeys.LFMS.value.format(server=server_name.lower()),
            path="$",
            obj=server_lfms,
        )


def delete_lfms_by_id_and_server_name(lfm_ids: list[int], server_name: str):
    """Delete lfms by ID and server name"""
    if not lfm_ids:
        return

    server_name = server_name.lower()
    with get_redis_client() as client:
        with client.pipeline() as pipeline:
            for lfm_id in lfm_ids:
                pipeline.json().delete(
                    key=RedisKeys.LFMS.value.format(server=server_name.lower()),
                    path=lfm_id,
                )
            pipeline.execute()


# ============ LFMs ==============


# ========== Server info =========
def get_server_info_as_dict() -> dict[str, dict]:
    """Get a dict of server name to server info dict"""
    return get_redis_client().json().get(RedisKeys.SERVER_INFO.value, "servers")


def get_server_info() -> dict[str, ServerSpecificInfo]:
    """Get a dict of server name to server info object"""
    server_info = get_server_info_as_dict()
    return {
        server_name: ServerSpecificInfo(**server_info)
        for [server_name, server_info] in server_info
    }


def get_server_info_by_server_name_as_dict(server_name: str) -> dict:
    """Get a server info dict by server name"""
    server_info = get_server_info_as_dict()
    return server_info.get(server_name.lower())


def get_server_info_by_server_name(server_name: str) -> ServerSpecificInfo:
    """Get a server info object by server name"""
    server_info = get_server_info_by_server_name_as_dict(server_name)
    return ServerSpecificInfo(**server_info)


def merge_server_info(server_info: ServerInfo):
    """Merge a server info object into the cache"""
    get_redis_client().json().merge(
        RedisKeys.SERVER_INFO.value,
        path="$",
        obj=server_info.model_dump(exclude_unset=True),
    )


# ========== Server info =========


# ============ News ==============
def get_news_as_dict() -> list[dict]:
    return get_redis_client().json().get(RedisKeys.NEWS.value)


def get_news() -> list[News]:
    news = get_news_as_dict()
    return [News(**news) for news in news]


def set_news(news: list[News]):
    news_dump = [news.model_dump() for news in news]
    get_redis_client().json().set(
        RedisKeys.NEWS.value,
        path="$",
        obj=news_dump,  # TODO: does this need to be serialized?
    )


# ============ News ==============


# ======== Page messages =========
def get_page_messages_as_dict() -> list[dict]:
    return get_redis_client().json().get(RedisKeys.PAGE_MESSAGES.value)


def get_news() -> list[PageMessage]:
    page_messages = get_page_messages_as_dict()
    return [PageMessage(**page_message) for page_message in page_messages]


def set_page_messages(page_messages: list[PageMessage]):
    page_message_dump = [message.model_dump() for message in page_messages]
    get_redis_client().json().set(
        RedisKeys.PAGE_MESSAGES.value,
        path="$",
        obj=page_message_dump,  # TODO: does this need to be serialized?
    )


# ======== Page messages =========


# === Verification challenges ====
def get_challenge_for_character_by_character_id(character_id: int) -> str | None:
    challenges: dict[str, str] = (
        get_redis_client()
        .json()
        .get(RedisKeys.VERIFICATION_CHALLENGES.value, "challenges")
    )
    print(challenges)
    return challenges.get(str(character_id))


def set_challenge_for_character_by_character_id(character_id: int, challenge_word: str):
    get_redis_client().json().set(
        RedisKeys.VERIFICATION_CHALLENGES.value,
        path=f"challenges.{character_id}",
        obj=challenge_word,
        nx=True,
    )


# === Verification challenges ====


# ======= Quests and Areas =======
def get_known_areas() -> dict:
    """Get all areas from the cache."""
    return get_redis_client().json().get("known_areas")


def set_known_areas(areas: list[Area]):
    """Set the areas in the cache. It also sets the timestamp for cache expiration."""
    areas_entry = KnownAreasModel(
        areas=areas,
        timestamp=time(),
    )
    get_redis_client().json().set("known_areas", path="$", obj=areas_entry.model_dump())


def get_known_quests() -> dict:
    """Get all quests from the cache."""
    return get_redis_client().json().get("known_quests")


def set_known_quests(quests: list[Quest]):
    """Set the quests in the cache. It also sets the timestamp for cache expiration."""
    quests_entry = KnownQuestsModel(
        quests=quests,
        timestamp=time(),
    )
    get_redis_client().json().set(
        "known_quests", path="$", obj=quests_entry.model_dump()
    )


# ======= Quests and Areas =======


### verification challenge
## get
# challenge by character id

### areas
## get
# all areas
# all area ids
## set
# all areas

### quests
## get
# all quests
# all quest ids
## set
# all quests


# def get_characters_by_server_name_as_class(server_name: str) -> ServerCharactersData:
#     server_name = server_name.lower()
#     return ServerCharactersData(**get_characters_by_server_name_as_dict(server_name))


# def get_characters_by_server_name_as_dict(server_name: str) -> dict:
#     server_name = server_name.lower()
#     return get_redis_client_NEW().get_as_dict(RedisKeys.CHARACTERS, server_name)


# def get_character_count_by_server_name(server_name: str) -> int:
#     server_name = server_name.lower()
#     return get_redis_client_NEW().get_obj_len(f"{server_name}:characters", "characters")


# def get_character_by_character_id(character_id: int) -> Character:
#     for server_name in SERVER_NAMES_LOWERCASE:
#         server_characters = get_characters_by_server_name_as_class(server_name)
#         if character_id in server_characters.characters:
#             return server_characters.characters.get(character_id)


# def get_online_character_ids_by_server_name(server_name: str) -> list[int]:
#     """
#     Get a list of online character IDs for a given server name.
#     """
#     server_name = server_name.lower()
#     with get_redis_client() as client:
#         server_character_keys = client.json().objkeys(
#             f"{server_name}:characters", "characters"
#         )
#         return [int(key) for key in server_character_keys if key.isdigit()]


# def get_characters_by_character_ids(
#     character_ids: list[int],
# ) -> list[Character]:
#     characters: list[Character] = []
#     remaining_character_ids = set(character_ids)

#     with get_redis_client() as client:
#         for server_name in SERVER_NAMES_LOWERCASE:
#             server_character_keys = client.json().objkeys(
#                 f"{server_name}:characters", "characters"
#             )
#             discovered_character_ids: set[int] = set()
#             for character_id in remaining_character_ids:
#                 if str(character_id) not in server_character_keys:
#                     continue
#                 character = client.json().get(
#                     f"{server_name}:characters", f"characters.{character_id}"
#                 )
#                 characters.append(Character(**character))
#                 discovered_character_ids.add(character_id)
#                 if not remaining_character_ids:
#                     break
#             remaining_character_ids -= discovered_character_ids
#             if not remaining_character_ids:
#                 break
#     return characters


# def get_characters_by_server_name_and_character_ids(
#     server_name: str,
#     character_ids: list[int] | set[int],
# ) -> list[Character]:
#     """
#     Given a server name and a list of character IDs, return a list of Characters that
#     exist in the cache for that server. If a character ID does not exist, it is skipped.
#     """
#     characters: list[Character] = []

#     with get_redis_client() as client:
#         server_character_keys = client.json().objkeys(
#             f"{server_name}:characters", "characters"
#         )
#         server_character_keys = {int(key) for key in server_character_keys}
#         # discovered_character_ids: set[int] = set()
#         for character_id in character_ids:
#             if character_id not in server_character_keys:
#                 continue
#             character = client.json().get(
#                 f"{server_name}:characters", f"characters.{character_id}"
#             )
#             characters.append(Character(**character))
#             # discovered_character_ids.add(character_id)
#     return characters


# def get_character_by_name_and_server_name(
#     character_name: str, server_name: str
# ) -> Character:
#     character_name = character_name.lower()
#     server_characters = get_characters_by_server_name_as_class(server_name)
#     for character in server_characters.characters.values():
#         if character.name.lower() == character_name:
#             return character


# def get_lfms_by_server_name_as_class(server_name: str) -> ServerLFMsData:
#     return ServerLFMsData(**get_lfms_by_server_name_as_dict(server_name))


# def get_lfms_by_server_name_as_dict(server_name: str) -> dict:
#     server_name = server_name.lower()
#     return get_redis_client().json().get(RedisKeys.LFMS.value.format(server=server_name.lower()))


# def get_lfm_count_by_server_name(server_name: str) -> int:
#     server_name = server_name.lower()
#     return get_redis_client().json().objlen(f"{server_name}:lfms", "lfms")


# def set_characters_by_server_name(
#     server_name: str, server_characters: ServerCharactersData
# ):
#     server_name = server_name.lower()

#     with get_redis_client() as client:
#         client.json().set(
#             f"{server_name}:characters", path="$", obj=server_characters.model_dump()
#         )


# def update_characters_by_server_name(
#     server_name: str, server_characters: ServerCharactersData
# ):
#     server_name = server_name.lower()

#     with get_redis_client() as client:
#         client.json().merge(
#             name=f"{server_name}:characters",
#             path="$",
#             obj=server_characters.model_dump(exclude_unset=True),
#         )


# def delete_characters_by_server_name_and_character_ids(
#     server_name: str, character_ids: list[str]
# ):
#     if not character_ids:
#         return

#     server_name = server_name.lower()
#     with get_redis_client() as client:
#         with client.pipeline() as pipeline:
#             for character_id in character_ids:
#                 pipeline.json().delete(
#                     key=f"{server_name}:characters", path=f"characters.{character_id}"
#                 )
#             pipeline.execute()


# def set_lfms_by_server_name(server_name: str, server_lfms: ServerLFMsData):
#     server_name = server_name.lower()
#     with get_redis_client() as client:
#         client.json().set(f"{server_name}:lfms", path="$", obj=server_lfms.model_dump())


# def update_lfms_by_server_name(server_name: str, server_lfms: ServerLFMsData):
#     server_name = server_name.lower()
#     with get_redis_client() as client:
#         client.json().merge(
#             name=f"{server_name}:lfms",
#             path="$",
#             obj=server_lfms.model_dump(exclude_unset=True),
#         )


# def delete_lfms_by_server_name_and_lfm_ids(server_name: str, lfm_ids: list[str]):
#     if not lfm_ids:
#         return

#     server_name = server_name.lower()
#     with get_redis_client() as client:
#         with client.pipeline() as pipeline:
#             for lfm_id in lfm_ids:
#                 pipeline.json().delete(key=f"{server_name}:lfms", path=f"lfms.{lfm_id}")
#             pipeline.execute()


# def get_all_server_info_as_class() -> ServerInfoDict:
#     return {
#         server_name: ServerInfo(**server_info)
#         for server_name, server_info in get_all_server_info_as_dict().items()
#     }


# def get_all_server_info_as_dict() -> dict:
#     return get_redis_client().json().get("game_info", "servers")


# def get_server_info_by_server_name_as_class(server_name: str) -> ServerInfo:
#     return ServerInfo(**get_server_info_by_server_name_as_dict(server_name))


# def get_server_info_by_server_name_as_dict(server_name: str) -> dict:
#     return get_redis_client().json().get("game_info", f"servers.{server_name}")


# def get_game_info_as_class() -> GameInfo:
#     """
#     Get the game info from the Redis cache as a GameInfo class instance.
#     """
#     game_info_dict = get_game_info_as_dict()
#     if not game_info_dict:
#         return GameInfo(servers={})
#     return GameInfo(**game_info_dict)


# def get_game_info_as_dict() -> dict:
#     """
#     Get the game info from the Redis cache as a dictionary.
#     """
#     game_info_dict = get_redis_client().json().get("game_info")
#     if not game_info_dict:
#         return {}
#     return game_info_dict


# def merge_game_info(game_info: GameInfo):
#     """
#     Merge the game info into the Redis cache. This will update the existing game info
#     or create it if it doesn't exist.
#     """
#     get_redis_client().get
#     get_redis_client().json().merge(
#         "game_info", path="$", obj=game_info.model_dump(exclude_unset=True)
#     )


# def get_verification_challenge(character_id: str) -> str:
#     # TODO: move this out
#     challenge_words = [
#         "kobold",
#         "goblin",
#         "dwarf",
#         "elf",
#         "halfling",
#         "aasimar",
#         "dragonborn",
#         "gnome",
#         "tiefling",
#         "orc",
#         "bugbear",
#         "eladrin",
#         "tabaxi",
#     ]

#     challenge_word = random.choice(challenge_words)
#     get_redis_client().json().set(
#         "verification_challenges", path=character_id, obj=challenge_word, nx=True
#     )
#     cached_challenge_word = (
#         get_redis_client().json().get("verification_challenges", character_id)
#     )
#     return cached_challenge_word


# def get_valid_area_ids() -> tuple[list[int], str]:
#     """
#     Get all area IDs from the cache. If the cache is expired, clear the cache.
#     """
#     try:
#         area_ids_entry = ValidAreaIdsModel.model_validate(
#             get_redis_client().json().get("known_area_ids")
#         )
#         if not area_ids_entry:
#             return ([], None)
#         area_ids = area_ids_entry.valid_area_ids or []
#         timestamp = area_ids_entry.timestamp or 0
#         if time() - timestamp > VALID_AREA_CACHE_LIFETIME:
#             # Cache is expired, clear it
#             area_ids = []
#             set_valid_area_ids(area_ids)
#             return ([], None)
#     except Exception:
#         return ([], None)
#     return (area_ids, timestamp)


# def set_valid_area_ids(area_ids: list[int]):
#     """
#     Set the valid area IDs in the cache. It also sets the timestamp for cache expiration.
#     """
#     valid_area_ids_entry = ValidAreaIdsModel(
#         valid_area_ids=area_ids,
#         timestamp=time(),
#     )
#     get_redis_client().json().set(
#         "known_area_ids", path="$", obj=valid_area_ids_entry.model_dump()
#     )


# def get_all_areas() -> tuple[list[Area], str]:
#     """
#     Get all areas from the cache. If the cache is expired, clear the cache.
#     """
#     try:
#         areas_entry = ValidAreasModel.model_validate(
#             get_redis_client().json().get("known_areas")
#         )
#         if not areas_entry:
#             return ([], None)
#         areas: list[Area] = areas_entry.valid_areas or []
#         timestamp = areas_entry.timestamp or 0
#         if time() - timestamp > VALID_AREA_CACHE_LIFETIME:
#             # Cache is expired, clear it
#             areas = []
#             set_all_areas(areas)
#             return ([], None)
#     except Exception:
#         return ([], None)
#     return (areas, timestamp)


# def set_all_areas(areas: list[Area]):
#     """
#     Set the areas in the cache. It also sets the timestamp for cache expiration.
#     """
#     areas_entry = ValidAreasModel(
#         valid_areas=areas,
#         timestamp=time(),
#     )
#     get_redis_client().json().set("known_areas", path="$", obj=areas_entry.model_dump())


# def get_all_quests() -> tuple[list[dict], str]:
#     """
#     Get all quests from the cache. If the cache is expired, clear the cache.
#     """
#     try:
#         quests_entry: dict = get_redis_client().json().get("quests")
#         if not quests_entry:
#             return ([], None)
#         quests: list[dict] = quests_entry.get("quests", [])
#         timestamp = quests_entry.get("timestamp", 0)
#         if time() - timestamp > VALID_QUEST_CACHE_LIFETIME:
#             # Cache is expired, clear it
#             quests = []
#             set_all_quests(quests)
#             return ([], None)
#     except Exception:
#         return ([], None)
#     return (quests, timestamp)


# def set_all_quests(quests: list[dict]):
#     """
#     Set the quests in the cache. It also sets the timestamp for cache expiration.
#     """
#     quests_entry = {
#         "quests": quests,
#         "timestamp": time(),
#     }
#     get_redis_client().json().set("quests", path="$", obj=quests_entry)
