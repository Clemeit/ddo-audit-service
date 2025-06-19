"""
Service to interface with the Redis server.
"""

import os
import random

from constants.server import SERVER_NAMES_LOWERCASE
from models.character import Character
from models.redis import (
    GameInfo,
    ServerCharactersData,
    ServerLFMsData,
    ServerInfo,
    ValidAreaIdsModel,
    ServerInfoDict,
    ValidAreasModel,
    RedisKeys,
    REDIS_KEY_TYPE_MAPPING,
)
from time import time
from constants.redis import VALID_AREA_CACHE_LIFETIME, VALID_QUEST_CACHE_LIFETIME
from models.area import Area

import json
from typing import Optional

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
                self.client.json().set(key, path="$", obj=json.dumps(value))
            else:
                self.client.json().set(key, path="$", obj=value)

    def close(self):
        self.client.close()

    def get_as_class(self, key: RedisKeys, server: Optional[str] = None):
        """
        Retrieve and deserialize data from Redis based on the expected type.

        Args:
            key: The Redis key (as an Enum).
            server: Optional server name for server-specific keys.

        Returns:
            The deserialized data in the expected type, or None if the key doesn't exist.
        """
        try:
            # Handle server-specific keys
            if server and key in {RedisKeys.CHARACTERS, RedisKeys.LFMS}:
                key = key.value.format(server=server)

            # Get the expected type for the key
            expected_type = REDIS_KEY_TYPE_MAPPING.get(key)
            if not expected_type:
                raise ValueError(f"Key '{key}' is not in the Redis key type mapping.")

            # Retrieve the raw data from Redis
            raw_data = self.client.json().get(key)
            if raw_data is None:
                return None

            # Deserialize the data based on the expected type
            if issubclass(expected_type, BaseModel):
                return expected_type.model_validate(raw_data)  # TODO: test
            elif expected_type == dict:
                return json.loads(raw_data)
            else:
                return raw_data
        except Exception as e:
            print(
                f"Error retrieving data from Redis. key: {key}, expected_type: {expected_type}, message: {e}"
            )
            return None

    def get_as_dict(self, key: RedisKeys, server: Optional[str] = None):
        """
        Retrieve and deserialize data from Redis as a dictionary.

        Args:
            key: The Redis key (as an Enum).
            server: Optional server name for server-specific keys.

        Returns:
            The deserialized data as a dictionary, or None if the key doesn't exist.
        """
        try:
            # Handle server-specific keys
            if server and key in {RedisKeys.CHARACTERS, RedisKeys.LFMS}:
                key = key.value.format(server=server)

            # Retrieve the raw data from Redis
            raw_data = self.client.json().get(key)

            # Validate and serialize as needed
            if raw_data is None:
                return None
            if isinstance(raw_data, dict):
                return raw_data
            try:
                return json.loads(raw_data)
            except json.JSONDecodeError:
                return raw_data
        except Exception as e:
            print(f"Error retrieving data from Redis. key: {key}, message: {e}")
            return None

    def get_obj_len(self, key: RedisKeys, path: str):
        obj_dict = self.get_as_dict(key)
        if obj_dict is None:
            return 0
        return len(obj_dict.get(path, {}))

    def get_client(self):
        return self.client


redis_singleton = RedisSingleton()


def get_redis_client():
    return redis_singleton.get_client()


def get_redis_client_NEW():
    return redis_singleton


def initialize_redis():
    redis_singleton.initialize()


def close_redis():
    redis_singleton.close()


def get_characters_by_server_name_as_class(server_name: str) -> ServerCharactersData:
    server_name = server_name.lower()
    return ServerCharactersData(**get_characters_by_server_name_as_dict(server_name))


def get_characters_by_server_name_as_dict(server_name: str) -> dict:
    server_name = server_name.lower()
    return get_redis_client_NEW().get_as_dict(RedisKeys.CHARACTERS, server_name)


def get_character_count_by_server_name(server_name: str) -> int:
    server_name = server_name.lower()
    return get_redis_client_NEW().get_obj_len(f"{server_name}:characters", "characters")


def get_character_by_character_id(character_id: int) -> Character:
    for server_name in SERVER_NAMES_LOWERCASE:
        server_characters = get_characters_by_server_name_as_class(server_name)
        if character_id in server_characters.characters:
            return server_characters.characters.get(character_id)


def get_online_character_ids_by_server_name(server_name: str) -> list[int]:
    """
    Get a list of online character IDs for a given server name.
    """
    server_name = server_name.lower()
    with get_redis_client() as client:
        server_character_keys = client.json().objkeys(
            f"{server_name}:characters", "characters"
        )
        return [int(key) for key in server_character_keys if key.isdigit()]


def get_characters_by_character_ids(
    character_ids: list[int],
) -> list[Character]:
    characters: list[Character] = []
    remaining_character_ids = set(character_ids)

    with get_redis_client() as client:
        for server_name in SERVER_NAMES_LOWERCASE:
            server_character_keys = client.json().objkeys(
                f"{server_name}:characters", "characters"
            )
            discovered_character_ids: set[int] = set()
            for character_id in remaining_character_ids:
                if str(character_id) not in server_character_keys:
                    continue
                character = client.json().get(
                    f"{server_name}:characters", f"characters.{character_id}"
                )
                characters.append(Character(**character))
                discovered_character_ids.add(character_id)
                if not remaining_character_ids:
                    break
            remaining_character_ids -= discovered_character_ids
            if not remaining_character_ids:
                break
    return characters


def get_characters_by_server_name_and_character_ids(
    server_name: str,
    character_ids: list[int] | set[int],
) -> list[Character]:
    """
    Given a server name and a list of character IDs, return a list of Characters that
    exist in the cache for that server. If a character ID does not exist, it is skipped.
    """
    characters: list[Character] = []

    with get_redis_client() as client:
        server_character_keys = client.json().objkeys(
            f"{server_name}:characters", "characters"
        )
        server_character_keys = {int(key) for key in server_character_keys}
        # discovered_character_ids: set[int] = set()
        for character_id in character_ids:
            if character_id not in server_character_keys:
                continue
            character = client.json().get(
                f"{server_name}:characters", f"characters.{character_id}"
            )
            characters.append(Character(**character))
            # discovered_character_ids.add(character_id)
    return characters


def get_character_by_name_and_server_name(
    character_name: str, server_name: str
) -> Character:
    character_name = character_name.lower()
    server_characters = get_characters_by_server_name_as_class(server_name)
    for character in server_characters.characters.values():
        if character.name.lower() == character_name:
            return character


def get_lfms_by_server_name_as_class(server_name: str) -> ServerLFMsData:
    return ServerLFMsData(**get_lfms_by_server_name_as_dict(server_name))


def get_lfms_by_server_name_as_dict(server_name: str) -> dict:
    server_name = server_name.lower()
    return get_redis_client().json().get(f"{server_name.lower()}:lfms")


def get_lfm_count_by_server_name(server_name: str) -> int:
    server_name = server_name.lower()
    return get_redis_client().json().objlen(f"{server_name}:lfms", "lfms")


def set_characters_by_server_name(
    server_name: str, server_characters: ServerCharactersData
):
    server_name = server_name.lower()

    with get_redis_client() as client:
        client.json().set(
            f"{server_name}:characters", path="$", obj=server_characters.model_dump()
        )


def update_characters_by_server_name(
    server_name: str, server_characters: ServerCharactersData
):
    server_name = server_name.lower()

    with get_redis_client() as client:
        client.json().merge(
            name=f"{server_name}:characters",
            path="$",
            obj=server_characters.model_dump(exclude_unset=True),
        )


def delete_characters_by_server_name_and_character_ids(
    server_name: str, character_ids: list[str]
):
    if not character_ids:
        return

    server_name = server_name.lower()
    with get_redis_client() as client:
        with client.pipeline() as pipeline:
            for character_id in character_ids:
                pipeline.json().delete(
                    key=f"{server_name}:characters", path=f"characters.{character_id}"
                )
            pipeline.execute()


def set_lfms_by_server_name(server_name: str, server_lfms: ServerLFMsData):
    server_name = server_name.lower()
    with get_redis_client() as client:
        client.json().set(f"{server_name}:lfms", path="$", obj=server_lfms.model_dump())


def update_lfms_by_server_name(server_name: str, server_lfms: ServerLFMsData):
    server_name = server_name.lower()
    with get_redis_client() as client:
        client.json().merge(
            name=f"{server_name}:lfms",
            path="$",
            obj=server_lfms.model_dump(exclude_unset=True),
        )


def delete_lfms_by_server_name_and_lfm_ids(server_name: str, lfm_ids: list[str]):
    if not lfm_ids:
        return

    server_name = server_name.lower()
    with get_redis_client() as client:
        with client.pipeline() as pipeline:
            for lfm_id in lfm_ids:
                pipeline.json().delete(key=f"{server_name}:lfms", path=f"lfms.{lfm_id}")
            pipeline.execute()


def get_all_server_info_as_class() -> ServerInfoDict:
    return {
        server_name: ServerInfo(**server_info)
        for server_name, server_info in get_all_server_info_as_dict().items()
    }


def get_all_server_info_as_dict() -> dict:
    return get_redis_client().json().get("game_info", "servers")


def get_server_info_by_server_name_as_class(server_name: str) -> ServerInfo:
    return ServerInfo(**get_server_info_by_server_name_as_dict(server_name))


def get_server_info_by_server_name_as_dict(server_name: str) -> dict:
    return get_redis_client().json().get("game_info", f"servers.{server_name}")


def get_game_info_as_class() -> GameInfo:
    """
    Get the game info from the Redis cache as a GameInfo class instance.
    """
    game_info_dict = get_game_info_as_dict()
    if not game_info_dict:
        return GameInfo(servers={})
    return GameInfo(**game_info_dict)


def get_game_info_as_dict() -> dict:
    """
    Get the game info from the Redis cache as a dictionary.
    """
    game_info_dict = get_redis_client().json().get("game_info")
    if not game_info_dict:
        return {}
    return game_info_dict


def merge_game_info(game_info: GameInfo):
    """
    Merge the game info into the Redis cache. This will update the existing game info
    or create it if it doesn't exist.
    """
    get_redis_client().get
    get_redis_client().json().merge(
        "game_info", path="$", obj=game_info.model_dump(exclude_unset=True)
    )


def get_verification_challenge(character_id: str) -> str:
    # TODO: move this out
    challenge_words = [
        "kobold",
        "goblin",
        "dwarf",
        "elf",
        "halfling",
        "aasimar",
        "dragonborn",
        "gnome",
        "tiefling",
        "orc",
        "bugbear",
        "eladrin",
        "tabaxi",
    ]

    challenge_word = random.choice(challenge_words)
    get_redis_client().json().set(
        "verification_challenges", path=character_id, obj=challenge_word, nx=True
    )
    cached_challenge_word = (
        get_redis_client().json().get("verification_challenges", character_id)
    )
    return cached_challenge_word


def get_valid_area_ids() -> tuple[list[int], str]:
    """
    Get all area IDs from the cache. If the cache is expired, clear the cache.
    """
    try:
        area_ids_entry = ValidAreaIdsModel.model_validate(
            get_redis_client().json().get("known_area_ids")
        )
        if not area_ids_entry:
            return ([], None)
        area_ids = area_ids_entry.valid_area_ids or []
        timestamp = area_ids_entry.timestamp or 0
        if time() - timestamp > VALID_AREA_CACHE_LIFETIME:
            # Cache is expired, clear it
            area_ids = []
            set_valid_area_ids(area_ids)
            return ([], None)
    except Exception:
        return ([], None)
    return (area_ids, timestamp)


def set_valid_area_ids(area_ids: list[int]):
    """
    Set the valid area IDs in the cache. It also sets the timestamp for cache expiration.
    """
    valid_area_ids_entry = ValidAreaIdsModel(
        valid_area_ids=area_ids,
        timestamp=time(),
    )
    get_redis_client().json().set(
        "known_area_ids", path="$", obj=valid_area_ids_entry.model_dump()
    )


def get_all_areas() -> tuple[list[Area], str]:
    """
    Get all areas from the cache. If the cache is expired, clear the cache.
    """
    try:
        areas_entry = ValidAreasModel.model_validate(
            get_redis_client().json().get("known_areas")
        )
        if not areas_entry:
            return ([], None)
        areas: list[Area] = areas_entry.valid_areas or []
        timestamp = areas_entry.timestamp or 0
        if time() - timestamp > VALID_AREA_CACHE_LIFETIME:
            # Cache is expired, clear it
            areas = []
            set_all_areas(areas)
            return ([], None)
    except Exception:
        return ([], None)
    return (areas, timestamp)


def set_all_areas(areas: list[Area]):
    """
    Set the areas in the cache. It also sets the timestamp for cache expiration.
    """
    areas_entry = ValidAreasModel(
        valid_areas=areas,
        timestamp=time(),
    )
    get_redis_client().json().set("known_areas", path="$", obj=areas_entry.model_dump())


def get_all_quests() -> tuple[list[dict], str]:
    """
    Get all quests from the cache. If the cache is expired, clear the cache.
    """
    try:
        quests_entry: dict = get_redis_client().json().get("quests")
        if not quests_entry:
            return ([], None)
        quests: list[dict] = quests_entry.get("quests", [])
        timestamp = quests_entry.get("timestamp", 0)
        if time() - timestamp > VALID_QUEST_CACHE_LIFETIME:
            # Cache is expired, clear it
            quests = []
            set_all_quests(quests)
            return ([], None)
    except Exception:
        return ([], None)
    return (quests, timestamp)


def set_all_quests(quests: list[dict]):
    """
    Set the quests in the cache. It also sets the timestamp for cache expiration.
    """
    quests_entry = {
        "quests": quests,
        "timestamp": time(),
    }
    get_redis_client().json().set("quests", path="$", obj=quests_entry)
