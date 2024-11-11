"""
Service to interface with the Redis server.
"""

import os

from constants.server import SERVER_NAMES_LOWERCASE
from models.character import Character
from models.lfm import LFM
from models.redis import CACHE_MODEL, ServerCharacters, ServerLFMs

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
            print(f"Setting {key} to {value}")
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


def get_characters_by_server_name(server_name: str) -> ServerCharacters:
    server_name = server_name.lower()
    return ServerCharacters(
        **get_redis_client().json().get(f"{server_name}:characters")
    )


def get_character_by_character_id(character_id: int) -> Character:
    character_id = str(character_id)
    for server_name in SERVER_NAMES_LOWERCASE:
        server_characters = get_characters_by_server_name(server_name)
        if character_id in server_characters.characters:
            return server_characters.characters.get(character_id)


def get_character_by_name_and_server_name(
    character_name: str, server_name: str
) -> Character:
    character_name = character_name.lower()
    server_characters = get_characters_by_server_name(server_name)
    for character in server_characters.characters.values():
        if character.name.lower() == character_name:
            return character


def get_lfms_by_server_name(server_name: str) -> ServerLFMs:
    server_name = server_name.lower()
    return ServerLFMs(**get_redis_client().json().get(f"{server_name}:lfms"))


def set_characters_by_server_name(
    server_name: str, server_characters: ServerCharacters
):
    server_name = server_name.lower()
    get_redis_client().json().set(
        f"{server_name}:characters", path="$", obj=server_characters.model_dump()
    )


def set_lfms_by_server_name(server_name: str, server_lfms: ServerLFMs):
    server_name = server_name.lower()
    get_redis_client().json().set(
        f"{server_name}:lfms", path="$", obj=server_lfms.model_dump()
    )
