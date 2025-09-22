import services.redis as redis_client
import services.postgres as postgres_client
from typing import Any

from constants.redis import (
    UNIQUE_GUILDS_CACHE_TTL,
)
from constants.guilds import GUILD_NAME_MAX_LENGTH


def get_all_guilds() -> list[dict]:
    return get_cached_data_with_fallback(
        "all_guilds",
        postgres_client.get_all_guilds,
        ttl=UNIQUE_GUILDS_CACHE_TTL,
    )


def get_cached_data_with_fallback(key: str, fallback_func, ttl: int = 60 * 60) -> Any:
    """Get cached data, regenerate if expired."""
    cached_data = redis_client.get_by_key(key)

    if not cached_data:
        fresh_data = fallback_func()
        redis_client.set_by_key(key, fresh_data, ttl=ttl)
        return fresh_data

    return cached_data


def validate_guild_name(guild_name: str) -> bool:
    if not guild_name or len(guild_name) > GUILD_NAME_MAX_LENGTH:
        return False
    if not all(c.isalnum() or c.isspace() or c == "-" or c == "'" for c in guild_name):
        False
    return True
