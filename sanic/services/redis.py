"""
Service to interface with the Redis server.
"""

import os
from datetime import datetime

from constants.server import SERVER_NAMES_LOWERCASE
from models.character import Character
from models.redis import CACHE_MODEL, GameInfo, ServerCharactersData, ServerLFMsData

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
        for key, value in CACHE_MODEL.items():
            self.client.json().set(key, path="$", obj=value.model_dump())

    def close(self):
        self.client.close()

    def get_client(self):
        return self.client


redis_singleton = RedisSingleton()


def get_redis_client():
    return redis_singleton.get_client()


def initialize_redis():
    redis_singleton.initialize()


def close_redis():
    redis_singleton.close()


def get_characters_by_server_name(server_name: str) -> ServerCharactersData:
    server_name = server_name.lower()
    return ServerCharactersData(
        **get_redis_client().json().get(f"{server_name}:characters")
    )


def get_character_by_character_id(character_id: int) -> Character:
    character_id = str(character_id)
    for server_name in SERVER_NAMES_LOWERCASE:
        server_characters = get_characters_by_server_name(server_name)
        if character_id in server_characters.characters:
            return server_characters.characters.get(character_id)


def get_characters_by_character_ids(
    character_ids: list[str] | set[str],
) -> list[Character]:
    # performance difference per 1000 characters with 50 logging off:
    # Old code: 8-10 seconds
    # New code: 35-50 milliseconds
    # A 200x improvement

    characters: list[Character] = []
    remaining_character_ids = set(character_ids)

    with get_redis_client() as client:
        for server_name in SERVER_NAMES_LOWERCASE:
            server_character_keys = client.json().objkeys(
                f"{server_name}:characters", "characters"
            )
            discovered_character_ids: set[str] = set()
            for character_id in remaining_character_ids:
                if character_id not in server_character_keys:
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
    character_ids: list[str] | set[str],
) -> list[Character]:
    characters: list[Character] = []

    with get_redis_client() as client:
        server_character_keys = client.json().objkeys(
            f"{server_name}:characters", "characters"
        )
        discovered_character_ids: set[str] = set()
        for character_id in character_ids:
            if character_id not in server_character_keys:
                continue
            character = client.json().get(
                f"{server_name}:characters", f"characters.{character_id}"
            )
            characters.append(Character(**character))
            discovered_character_ids.add(character_id)
    return characters


def get_character_by_name_and_server_name(
    character_name: str, server_name: str
) -> Character:
    character_name = character_name.lower()
    server_characters = get_characters_by_server_name(server_name)
    for character in server_characters.characters.values():
        if character.name.lower() == character_name:
            return character


def get_lfms_by_server_name(server_name: str) -> ServerLFMsData:
    server_name = server_name.lower()
    return ServerLFMsData(**get_redis_client().json().get(f"{server_name}:lfms"))


def set_characters_by_server_name(
    server_name: str, server_characters: ServerCharactersData
):
    server_name = server_name.lower()
    client = get_redis_client()

    client.json().set(
        f"{server_name}:characters", path="$", obj=server_characters.model_dump()
    )


def update_characters_by_server_name(
    server_name: str, server_characters: ServerCharactersData
):
    server_name = server_name.lower()
    client = get_redis_client()

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
    client = get_redis_client()
    pipeline = client.pipeline()

    # Pipeline deletion
    for character_id in character_ids:
        pipeline.json().delete(
            key=f"{server_name}:characters", path=f"characters.{character_id}"
        )
    pipeline.execute()


def set_lfms_by_server_name(server_name: str, server_lfms: ServerLFMsData):
    server_name = server_name.lower()
    get_redis_client().json().set(
        f"{server_name}:lfms", path="$", obj=server_lfms.model_dump()
    )


def update_lfms_by_server_name(server_name: str, server_lfms: ServerLFMsData):
    server_name = server_name.lower()
    client = get_redis_client()

    client.json().merge(
        name=f"{server_name}:lfms",
        path="$",
        obj=server_lfms.model_dump(exclude_unset=True),
    )


def delete_lfms_by_server_name_and_lfm_ids(server_name: str, lfm_ids: list[str]):
    if not lfm_ids:
        return

    server_name = server_name.lower()
    client = get_redis_client()
    pipeline = client.pipeline()

    # Pipeline deletion and update
    for lfm_id in lfm_ids:
        pipeline.json().delete(key=f"{server_name}:lfms", path=f"lfms.{lfm_id}")
    # pipeline.json().set(f"{server_name}:lfms", path="last_updated", obj=datetime.now())
    pipeline.execute()


def get_game_info() -> GameInfo:
    return GameInfo(**get_redis_client().json().get("game_info"))


def set_game_info(game_info: GameInfo):
    get_redis_client().json().merge(
        "game_info", path="$", obj=game_info.model_dump(exclude_unset=True)
    )
