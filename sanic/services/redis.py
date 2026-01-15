"""
Service to interface with the Redis server.
"""

import os
from datetime import datetime
from typing import Dict, List, Optional

from constants.server import SERVER_NAMES_LOWERCASE
from models.character import Character
from models.lfm import Lfm
from models.redis import (
    ServerInfo,
    ServerSpecificInfo,
    KnownAreasModel,
    KnownQuestsModel,
    RedisKeys,
    REDIS_KEY_TYPE_MAPPING,
)
from time import time
from models.area import Area
from models.service import News, PageMessage
from models.quest import Quest

import json
from typing import Optional, Any
import uuid

from pydantic import BaseModel

import redis
import redis.asyncio as aioredis
from redis.connection import ConnectionPool
from contextlib import contextmanager
import logging
from typing import Generator

# Redis configuration with defaults
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")
REDIS_MAX_CONNECTIONS = int(os.getenv("REDIS_MAX_CONNECTIONS", "50"))
REDIS_SOCKET_CONNECT_TIMEOUT = int(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "5"))
REDIS_SOCKET_TIMEOUT = int(os.getenv("REDIS_SOCKET_TIMEOUT", "5"))
REDIS_RETRY_ON_TIMEOUT = os.getenv("REDIS_RETRY_ON_TIMEOUT", "true").lower() == "true"
REDIS_HEALTH_CHECK_INTERVAL = int(os.getenv("REDIS_HEALTH_CHECK_INTERVAL", "30"))

# Traffic counters (for incident investigation)
TRAFFIC_COUNTERS_ENABLED = (
    os.getenv("TRAFFIC_COUNTERS_ENABLED", "true").lower() == "true"
)
TRAFFIC_COUNTERS_TTL_HOURS = int(os.getenv("TRAFFIC_COUNTERS_TTL_HOURS", "72"))
TRAFFIC_COUNTERS_BUCKET_SECONDS = int(
    os.getenv("TRAFFIC_COUNTERS_BUCKET_SECONDS", "60")
)
TRAFFIC_COUNTERS_MAX_MINUTES = int(os.getenv("TRAFFIC_COUNTERS_MAX_MINUTES", "1440"))
TRAFFIC_COUNTERS_PREFIX = os.getenv("TRAFFIC_COUNTERS_PREFIX", "traffic")

# Setup logging
logger = logging.getLogger(__name__)

# Global connection pool
_connection_pool: ConnectionPool = None
_async_connection_pool: aioredis.ConnectionPool = None


ONE_TIME_USER_SETTINGS_PREFIX = "one_time_user_settings:"

# Atomic JSON.GET + DEL (ensures true one-time access)
_ONE_TIME_USER_SETTINGS_GETDEL_LUA = """
local key = KEYS[1]
local val = redis.call('JSON.GET', key)
if val then
  redis.call('DEL', key)
end
return val
"""


class RedisConnectionManager:
    """Manages Redis connections using connection pooling for optimal performance."""

    def __init__(self):
        self._sync_pool = None
        self._async_pool = None
        self._is_initialized = False

    def initialize(self):
        """Initialize Redis connection pools."""
        if self._is_initialized:
            logger.warning("Redis connection manager already initialized")
            return

        logger.info("Initializing Redis connection pools...")

        # Synchronous connection pool
        self._sync_pool = ConnectionPool(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD,
            max_connections=REDIS_MAX_CONNECTIONS,
            socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT,
            socket_timeout=REDIS_SOCKET_TIMEOUT,
            retry_on_timeout=REDIS_RETRY_ON_TIMEOUT,
            health_check_interval=REDIS_HEALTH_CHECK_INTERVAL,
        )

        # Asynchronous connection pool
        self._async_pool = aioredis.ConnectionPool(
            host=REDIS_HOST,
            port=REDIS_PORT,
            db=REDIS_DB,
            password=REDIS_PASSWORD,
            max_connections=REDIS_MAX_CONNECTIONS,
            socket_connect_timeout=REDIS_SOCKET_CONNECT_TIMEOUT,
            socket_timeout=REDIS_SOCKET_TIMEOUT,
            retry_on_timeout=REDIS_RETRY_ON_TIMEOUT,
            health_check_interval=REDIS_HEALTH_CHECK_INTERVAL,
        )

        self._is_initialized = True
        logger.info("Redis connection pools initialized successfully")

        # Initialize cache with default keys
        self._initialize_cache()

    def _initialize_cache(self):
        """Initialize the cache with default keys and values."""
        logger.info("Initializing Redis cache with default keys...")

        try:
            with self.get_sync_client() as client:
                # Don't flush all data - preserve existing cache data
                # client.flushall()  # Commented out to preserve AOF persistence

                # Initialize cache with keys from mapping - only if they don't exist
                for key, value in REDIS_KEY_TYPE_MAPPING.items():
                    key = key.value if isinstance(key, RedisKeys) else key

                    # Check if key already exists before setting
                    if not client.exists(key):
                        # value is a class type, so we need to instantiate it if it's a BaseModel
                        if isinstance(value, type) and issubclass(value, BaseModel):
                            value = value()

                        # model_dump if inherits from BaseModel, else just value
                        if hasattr(value, "model_dump"):
                            client.json().set(key, path="$", obj=value.model_dump())
                        elif isinstance(value, dict):
                            client.json().set(key, path="$", obj=value)
                        else:
                            client.json().set(key, path="$", obj=value)

            logger.info("Redis cache initialized successfully")

        except Exception as e:
            logger.error(f"Failed to initialize Redis cache: {e}")
            raise

    @contextmanager
    def get_sync_client(self) -> Generator[redis.Redis, None, None]:
        """Get a synchronous Redis client from the connection pool."""
        if not self._is_initialized:
            raise RuntimeError("Redis connection manager not initialized")

        client = redis.Redis(connection_pool=self._sync_pool)
        try:
            yield client
        finally:
            # Connection is automatically returned to pool when client goes out of scope
            pass

    async def get_async_client(self) -> aioredis.Redis:
        """Get an asynchronous Redis client from the connection pool."""
        if not self._is_initialized:
            raise RuntimeError("Redis connection manager not initialized")

        return aioredis.Redis(connection_pool=self._async_pool)

    def health_check(self) -> bool:
        """Perform a health check on the Redis connection."""
        try:
            with self.get_sync_client() as client:
                response = client.ping()
                return response is True
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return False

    async def health_check_async(self) -> bool:
        """Perform an asynchronous health check on the Redis connection."""
        try:
            client = await self.get_async_client()
            response = await client.ping()
            await client.aclose()
            return response is True
        except Exception as e:
            logger.error(f"Async Redis health check failed: {e}")
            return False

    def close(self):
        """Close all Redis connections."""
        if not self._is_initialized:
            return

        logger.info("Closing Redis connections...")

        try:
            if self._sync_pool:
                self._sync_pool.disconnect()
            if self._async_pool:
                # Note: async pool closing should be done in async context
                # but for shutdown we'll just disconnect the sync pool
                pass
        except Exception as e:
            logger.error(f"Error closing Redis connections: {e}")
        finally:
            self._is_initialized = False
            logger.info("Redis connections closed")

    async def close_async(self):
        """Close Redis connections asynchronously."""
        if not self._is_initialized:
            return

        logger.info("Closing Redis connections asynchronously...")

        try:
            if self._async_pool:
                await self._async_pool.disconnect()
            if self._sync_pool:
                self._sync_pool.disconnect()
        except Exception as e:
            logger.error(f"Error closing Redis connections: {e}")
        finally:
            self._is_initialized = False
            logger.info("Redis connections closed")


# Global connection manager instance
_redis_manager = RedisConnectionManager()


def get_redis_client():
    """Get a Redis client context manager for synchronous operations."""
    return _redis_manager.get_sync_client()


async def get_async_redis_client():
    """Get an async Redis client for asynchronous operations."""
    return await _redis_manager.get_async_client()


def initialize_redis():
    """Initialize Redis connection pools and cache."""
    _redis_manager.initialize()


def close_redis():
    """Close all Redis connections."""
    _redis_manager.close()


async def close_redis_async():
    """Close all Redis connections asynchronously."""
    await _redis_manager.close_async()


def _clamp_int(value: Any, default: int, *, min_value: int, max_value: int) -> int:
    try:
        i = int(value)
    except Exception:
        i = default
    if i < min_value:
        return min_value
    if i > max_value:
        return max_value
    return i


def _traffic_bucket_id(now_s: Optional[float] = None) -> int:
    bucket_seconds = max(1, TRAFFIC_COUNTERS_BUCKET_SECONDS)
    now = time() if now_s is None else now_s
    return int(now // bucket_seconds)


def _traffic_key(suffix: str, bucket_id: int) -> str:
    return f"{TRAFFIC_COUNTERS_PREFIX}:{suffix}:{bucket_id}"


async def traffic_increment(
    *,
    ip: Optional[str],
    route: Optional[str],
    method: Optional[str],
    status: int,
    bytes_out: Optional[int],
) -> None:
    """Increment per-request traffic counters in Redis.

    Uses per-bucket sorted sets for quick "top N" investigation without storing raw logs.
    """

    if not TRAFFIC_COUNTERS_ENABLED:
        return

    bucket_id = _traffic_bucket_id()
    ttl_seconds = max(60, TRAFFIC_COUNTERS_TTL_HOURS * 3600)

    ip_key = (ip or "").strip()[:64]
    route_key = (route or "").strip()[:256]
    method_key = (method or "").strip()[:16]
    status_key = str(_clamp_int(status, 0, min_value=0, max_value=999))
    bytes_out_value = max(0, int(bytes_out)) if bytes_out is not None else 0

    # Avoid unbounded cardinality.
    if not ip_key:
        ip_key = "unknown"
    if not route_key:
        route_key = "unknown"

    try:
        client = await get_async_redis_client()

        pipe = client.pipeline(transaction=False)

        # Request counts
        pipe.zincrby(_traffic_key("req:ip", bucket_id), 1, ip_key)
        pipe.zincrby(_traffic_key("req:route", bucket_id), 1, route_key)
        pipe.incr(_traffic_key("req:total", bucket_id))

        # Bytes out (egress)
        if bytes_out_value:
            pipe.zincrby(
                _traffic_key("bytes_out:ip", bucket_id), bytes_out_value, ip_key
            )
            pipe.zincrby(
                _traffic_key("bytes_out:route", bucket_id), bytes_out_value, route_key
            )
            pipe.incrby(_traffic_key("bytes_out:total", bucket_id), bytes_out_value)

        # Small additional breakdowns (low cardinality)
        if method_key:
            pipe.hincrby(_traffic_key("req:method", bucket_id), method_key, 1)
        pipe.hincrby(_traffic_key("req:status", bucket_id), status_key, 1)

        # TTL
        pipe.expire(_traffic_key("req:ip", bucket_id), ttl_seconds)
        pipe.expire(_traffic_key("req:route", bucket_id), ttl_seconds)
        pipe.expire(_traffic_key("req:total", bucket_id), ttl_seconds)
        pipe.expire(_traffic_key("req:method", bucket_id), ttl_seconds)
        pipe.expire(_traffic_key("req:status", bucket_id), ttl_seconds)
        pipe.expire(_traffic_key("bytes_out:ip", bucket_id), ttl_seconds)
        pipe.expire(_traffic_key("bytes_out:route", bucket_id), ttl_seconds)
        pipe.expire(_traffic_key("bytes_out:total", bucket_id), ttl_seconds)

        await pipe.execute()
    except Exception as e:
        # Never break requests if Redis is degraded.
        logger.warning(f"traffic_increment failed: {e}")


async def _traffic_top_zset(
    *,
    suffix: str,
    minutes: int,
    limit: int,
) -> list[tuple[str, float]]:
    if not TRAFFIC_COUNTERS_ENABLED:
        return []

    minutes = _clamp_int(
        minutes, 60, min_value=1, max_value=TRAFFIC_COUNTERS_MAX_MINUTES
    )
    limit = _clamp_int(limit, 25, min_value=1, max_value=200)

    now_bucket = _traffic_bucket_id()
    bucket_ids = list(range(now_bucket - minutes + 1, now_bucket + 1))
    keys = [_traffic_key(suffix, b) for b in bucket_ids]

    try:
        client = await get_async_redis_client()
        tmp_key = _traffic_key(f"tmp:{suffix}", now_bucket) + ":" + uuid.uuid4().hex

        # Aggregate last N buckets into one temporary ZSET.
        await client.zunionstore(tmp_key, keys, aggregate="SUM")
        await client.expire(tmp_key, 60)

        # redis-py returns bytes members when decode_responses=False
        raw = await client.zrevrange(tmp_key, 0, limit - 1, withscores=True)
        await client.delete(tmp_key)

        out: list[tuple[str, float]] = []
        for member, score in raw:
            if isinstance(member, (bytes, bytearray)):
                member_str = member.decode("utf-8", errors="replace")
            else:
                member_str = str(member)
            out.append((member_str, float(score)))
        return out
    except Exception as e:
        logger.warning(f"_traffic_top_zset failed: {e}")
        return []


async def traffic_top_ips(
    *, minutes: int = 60, metric: str = "requests", limit: int = 25
):
    """Return top IPs by requests or bytes_out over the last N minutes."""
    suffix = "req:ip" if metric != "bytes_out" else "bytes_out:ip"
    rows = await _traffic_top_zset(suffix=suffix, minutes=minutes, limit=limit)
    key = "requests" if metric != "bytes_out" else "bytes_out"
    return [{"ip": member, key: score} for (member, score) in rows]


async def traffic_top_routes(
    *, minutes: int = 60, metric: str = "requests", limit: int = 25
):
    """Return top routes by requests or bytes_out over the last N minutes."""
    suffix = "req:route" if metric != "bytes_out" else "bytes_out:route"
    rows = await _traffic_top_zset(suffix=suffix, minutes=minutes, limit=limit)
    key = "requests" if metric != "bytes_out" else "bytes_out"
    return [{"route": member, key: score} for (member, score) in rows]


def redis_health_check() -> bool:
    """Check if Redis is healthy and responsive."""
    healthy = _redis_manager.health_check()
    return {
        "healthy": healthy,
    }


async def redis_health_check_async() -> bool:
    """Check if Redis is healthy and responsive (async version)."""
    return await _redis_manager.health_check_async()


# ================================
# PERFORMANCE OPTIMIZATION UTILITIES
# ================================


@contextmanager
def get_redis_pipeline():
    """Get a Redis pipeline for batch operations - use for better performance when doing multiple operations."""
    with get_redis_client() as client:
        pipeline = client.pipeline()
        try:
            yield pipeline
        finally:
            # Pipeline operations are executed when exiting context
            pass


def execute_batch_operations(operations: list[tuple[str, dict]]):
    """
    Execute multiple Redis operations in a single pipeline for optimal performance.

    Args:
        operations: List of tuples containing (operation_type, kwargs)
                   operation_type can be: 'json_set', 'json_get', 'json_delete', 'json_merge', 'json_objlen'

    Returns:
        List of results from each operation
    """
    with get_redis_pipeline() as pipeline:
        for operation_type, kwargs in operations:
            if operation_type == "json_set":
                pipeline.json().set(**kwargs)
            elif operation_type == "json_get":
                pipeline.json().get(**kwargs)
            elif operation_type == "json_delete":
                pipeline.json().delete(**kwargs)
            elif operation_type == "json_merge":
                pipeline.json().merge(**kwargs)
            elif operation_type == "json_objlen":
                pipeline.json().objlen(**kwargs)
            else:
                raise ValueError(f"Unsupported operation type: {operation_type}")

        return pipeline.execute()


# ================================
# OPTIMIZED BULK OPERATIONS
# ================================


def bulk_update_characters(server_character_updates: dict[str, dict[int, dict]]):
    """
    Efficiently update characters across multiple servers using pipelines.

    Args:
        server_character_updates: Dict of server_name -> character_updates
    """
    operations = []
    for server_name, character_updates in server_character_updates.items():
        if character_updates:
            operations.append(
                (
                    "json_merge",
                    {
                        "name": RedisKeys.CHARACTERS.value.format(
                            server=server_name.lower()
                        ),
                        "path": "$",
                        "obj": character_updates,
                    },
                )
            )

    if operations:
        execute_batch_operations(operations)


def bulk_update_lfms(server_lfm_updates: dict[str, dict[int, dict]]):
    """
    Efficiently update LFMs across multiple servers using pipelines.

    Args:
        server_lfm_updates: Dict of server_name -> lfm_updates
    """
    operations = []
    for server_name, lfm_updates in server_lfm_updates.items():
        if lfm_updates:
            operations.append(
                (
                    "json_merge",
                    {
                        "name": RedisKeys.LFMS.value.format(server=server_name.lower()),
                        "path": "$",
                        "obj": lfm_updates,
                    },
                )
            )

    if operations:
        execute_batch_operations(operations)


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
    with get_redis_client() as client:
        redis_data = client.json().get(
            RedisKeys.CHARACTERS.value.format(server=server_name.lower())
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
    """Get a dict of server name to character count - optimized with pipeline"""
    operations = []
    for server_name in SERVER_NAMES_LOWERCASE:
        operations.append(
            (
                "json_objlen",
                {"name": RedisKeys.CHARACTERS.value.format(server=server_name.lower())},
            )
        )

    # Execute all operations in a single pipeline for better performance
    with get_redis_pipeline() as pipeline:
        for server_name in SERVER_NAMES_LOWERCASE:
            pipeline.json().objlen(
                RedisKeys.CHARACTERS.value.format(server=server_name.lower())
            )
        results = pipeline.execute()

    return {
        server_name: count if count is not None else 0
        for server_name, count in zip(SERVER_NAMES_LOWERCASE, results)
    }


def get_character_count_by_server_name(server_name: str) -> int:
    """Get the number of characters by server name"""
    with get_redis_client() as client:
        return client.json().objlen(
            RedisKeys.CHARACTERS.value.format(server=server_name.lower())
        )


def get_all_character_ids() -> dict[str, list[int]]:
    """Get a list of all online characters' IDs"""
    character_ids: dict[str, list[int]] = {}
    for server_name in SERVER_NAMES_LOWERCASE:
        character_ids[server_name] = get_character_ids_by_server_name(server_name)
    return character_ids


def get_character_ids_by_server_name(server_name: str) -> list[int]:
    """Get a list of all online characters' IDs by server name"""
    with get_redis_client() as client:
        keys = client.json().objkeys(
            RedisKeys.CHARACTERS.value.format(server=server_name.lower())
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
                with get_redis_client() as client:
                    return client.json().get(
                        RedisKeys.CHARACTERS.value.format(server=server_name),
                        character_id,
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


def get_online_characters_by_server_and_guild_name_as_dict(
    server_name: str, guild_name: str
) -> dict[int, dict]:
    """Get all character dicts matching a guild name on a specific server"""
    guild_name_lower = guild_name.lower()
    characters: dict[int, dict] = {}
    server_characters = get_characters_by_server_name_as_dict(server_name)
    matching_characters = [
        character
        for character in server_characters.values()
        if character
        and character.get("guild_name")
        and character.get("guild_name").lower() == guild_name_lower
    ]
    for matching_character in matching_characters:
        characters[matching_character.get("id")] = matching_character
    return characters


def get_characters_by_group_id_as_dict(group_id: int) -> dict[int, dict]:
    """Get all character dicts matching a group ID"""
    if group_id <= 0:
        return {}
    characters: dict[int, dict] = {}
    for server_name in SERVER_NAMES_LOWERCASE:
        server_characters = get_characters_by_server_name_as_dict(server_name)
        matching_characters = [
            character
            for character in server_characters.values()
            if character and character.get("group_id") == group_id
        ]
        for matching_character in matching_characters:
            characters[matching_character.get("id")] = matching_character
    return characters


def get_characters_by_group_id(group_id: int) -> dict[int, Character]:
    """Get all character objects matching a group ID"""
    characters = get_characters_by_group_id_as_dict(group_id)
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
    with get_redis_client() as client:
        redis_data = client.json().get(
            RedisKeys.LFMS.value.format(server=server_name.lower())
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
    """Get a dict of server name to lfm count - optimized with pipeline"""
    with get_redis_pipeline() as pipeline:
        for server_name in SERVER_NAMES_LOWERCASE:
            pipeline.json().objlen(
                RedisKeys.LFMS.value.format(server=server_name.lower())
            )
        results = pipeline.execute()

    return {
        server_name: count if count is not None else 0
        for server_name, count in zip(SERVER_NAMES_LOWERCASE, results)
    }


def get_lfm_count_by_server_name(server_name: str) -> int:
    """Get the number of lfms by server name"""
    with get_redis_client() as client:
        return client.json().objlen(
            RedisKeys.LFMS.value.format(server=server_name.lower())
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
    with get_redis_client() as client:
        return client.json().get(RedisKeys.SERVER_INFO.value, "servers")


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
    with get_redis_client() as client:
        client.json().merge(
            RedisKeys.SERVER_INFO.value,
            path="$",
            obj=server_info.model_dump(exclude_unset=True),
        )


# ========== Server info =========


# ============ News ==============
def get_news_as_dict() -> list[dict]:
    with get_redis_client() as client:
        return client.json().get(RedisKeys.NEWS.value)


def get_news() -> list[News]:
    news = get_news_as_dict()
    return [News(**news) for news in news]


def set_news(news: list[News]):
    news_dump = [news.model_dump() for news in news]
    with get_redis_client() as client:
        client.json().set(
            RedisKeys.NEWS.value,
            path="$",
            obj=news_dump,
        )


# ============ News ==============


# ======== Page messages =========
def get_page_messages_as_dict() -> list[dict]:
    with get_redis_client() as client:
        return client.json().get(RedisKeys.PAGE_MESSAGES.value)


def get_news() -> list[PageMessage]:
    page_messages = get_page_messages_as_dict()
    return [PageMessage(**page_message) for page_message in page_messages]


def set_page_messages(page_messages: list[PageMessage]):
    page_message_dump = [message.model_dump() for message in page_messages]
    with get_redis_client() as client:
        client.json().set(
            RedisKeys.PAGE_MESSAGES.value,
            path="$",
            obj=page_message_dump,
        )


# ======== Page messages =========


# === Verification challenges ====
def get_challenge_for_character_by_character_id(character_id: int) -> str | None:
    with get_redis_client() as client:
        challenges: dict[str, str] = client.json().get(
            RedisKeys.VERIFICATION_CHALLENGES.value, "challenges"
        )
    return challenges.get(str(character_id))


def set_challenge_for_character_by_character_id(character_id: int, challenge_word: str):
    with get_redis_client() as client:
        client.json().set(
            RedisKeys.VERIFICATION_CHALLENGES.value,
            path=f"challenges.{character_id}",
            obj=challenge_word,
            nx=True,
        )


# === Verification challenges ====


# ======= Quests and Areas =======
def get_known_areas() -> dict:
    """Get all areas from the cache."""
    with get_redis_client() as client:
        return client.json().get("known_areas") or {}


def set_known_areas(areas: list[Area]):
    """Set the areas in the cache. It also sets the timestamp for cache expiration."""
    areas_entry = KnownAreasModel(
        areas=areas,
        timestamp=time(),
    )
    with get_redis_client() as client:
        client.json().set("known_areas", path="$", obj=areas_entry.model_dump())


def get_known_quests() -> dict:
    """Get all quests from the cache."""
    with get_redis_client() as client:
        return client.json().get("known_quests") or {}


def set_known_quests(quests: list[Quest]):
    """Set the quests in the cache. It also sets the timestamp for cache expiration."""
    quests_entry = KnownQuestsModel(
        quests=quests,
        timestamp=time(),
    )
    with get_redis_client() as client:
        client.json().set("known_quests", path="$", obj=quests_entry.model_dump())


# ======= Quests and Areas =======

# ======= Game Population ========


def get_game_population_1_day() -> dict:
    with get_redis_client() as client:
        return client.json().get("game_population_1_day")


def set_game_population_1_day(data: list[dict]):
    entry = {"data": data, "timestamp": time()}
    with get_redis_client() as client:
        client.json().set("game_population_1_day", path="$", obj=entry)


def get_game_population_totals_1_day() -> dict:
    with get_redis_client() as client:
        return client.json().get("game_population_totals_1_day")


def set_game_population_totals_1_day(data: list[dict]):
    entry = {"data": data, "timestamp": time()}
    with get_redis_client() as client:
        client.json().set("game_population_totals_1_day", path="$", obj=entry)


def get_game_population_1_week() -> dict:
    with get_redis_client() as client:
        return client.json().get("game_population_1_week")


def set_game_population_1_week(data: list[dict]):
    entry = {"data": data, "timestamp": time()}
    with get_redis_client() as client:
        client.json().set("game_population_1_week", path="$", obj=entry)


def get_game_population_totals_1_week() -> dict:
    with get_redis_client() as client:
        return client.json().get("game_population_totals_1_week")


def set_game_population_totals_1_week(data: list[dict]):
    entry = {"data": data, "timestamp": time()}
    with get_redis_client() as client:
        client.json().set("game_population_totals_1_week", path="$", obj=entry)


def get_game_population_1_month() -> dict:
    with get_redis_client() as client:
        return client.json().get("game_population_1_month")


def set_game_population_1_month(data: list[dict]):
    entry = {"data": data, "timestamp": time()}
    with get_redis_client() as client:
        client.json().set("game_population_1_month", path="$", obj=entry)


def get_game_population_totals_1_month() -> dict:
    with get_redis_client() as client:
        return client.json().get("game_population_totals_1_month")


def set_game_population_totals_1_month(data: list[dict]):
    entry = {"data": data, "timestamp": time()}
    with get_redis_client() as client:
        client.json().set("game_population_totals_1_month", path="$", obj=entry)


def get_game_population_totals_1_year() -> dict:
    with get_redis_client() as client:
        return client.json().get("game_population_totals_1_year")


def set_game_population_totals_1_year(data: list[dict]):
    entry = {"data": data, "timestamp": time()}
    with get_redis_client() as client:
        client.json().set("game_population_totals_1_year", path="$", obj=entry)


def get_unique_character_and_guild_count_month() -> dict:
    with get_redis_client() as client:
        return client.json().get("unique_character_and_guild_count_month")


def set_unique_character_count_month(data: list[dict]):
    entry = {"data": data, "timestamp": time()}
    with get_redis_client() as client:
        client.json().set("unique_character_and_guild_count_month", path="$", obj=entry)


def get_by_key(key: str) -> Optional[Any]:
    """Get data by key from the game population cache."""
    with get_redis_client() as client:
        return client.json().get(key)


def set_by_key(key: str, data: dict, ttl: int = None):
    """Set data by key in the game population cache."""
    with get_redis_client() as client:
        client.json().set(key, path="$", obj=data)
        if ttl:
            client.expire(key, ttl)  # Set TTL if provided


def expire_key_immediately(key: str):
    """Expire a Redis key immediately (force removal from cache)."""
    with get_redis_client() as client:
        client.expire(key, 0)


def store_one_time_user_settings(user_id: str, settings: dict):
    """Store one-time user settings (expires after 5 minutes)."""
    key = f"{ONE_TIME_USER_SETTINGS_PREFIX}{user_id}"
    with get_redis_client() as client:
        client.json().set(key, path="$", obj=settings)
        client.expire(key, 300)  # 5 minutes


# ========================================
# Active Quest Sessions (state tracking)
# ========================================


def get_active_quest_sessions_map() -> dict:
    """Return the entire active quest sessions map (character_id -> session dict)."""
    with get_redis_client() as client:
        data = client.json().get(RedisKeys.ACTIVE_QUEST_SESSIONS.value)
        return data if isinstance(data, dict) else {}


def get_active_quest_session_state(character_id: int) -> Optional[dict]:
    """Get active quest session state for a character from Redis.

    Returns a dict: {"quest_id": int, "entry_timestamp": str} or None.
    """
    try:
        with get_redis_client() as client:
            key = f"active_quest_session:{character_id}"
            raw_value = client.get(key)
            if raw_value:
                return json.loads(raw_value)
            return None
    except Exception:
        # Redis unavailable, return None
        return None


def set_active_quest_session_state(
    character_id: int, quest_id: int, entry_timestamp: datetime
) -> None:
    """Set or update the active quest session state for a character.
    
    Session is stored with a 48-hour TTL for automatic cleanup.
    """
    try:
        obj = {
            "quest_id": int(quest_id),
            "entry_timestamp": entry_timestamp.isoformat(),
        }
        with get_redis_client() as client:
            key = f"active_quest_session:{character_id}"
            # Set with 48-hour expiration (172800 seconds)
            client.setex(key, 172800, json.dumps(obj))
    except Exception:
        # Redis unavailable, silently continue
        pass


def clear_active_quest_session_state(character_id: int) -> None:
    """Clear the active quest session state for a character."""
    try:
        with get_redis_client() as client:
            key = f"active_quest_session:{character_id}"
            client.delete(key)
    except Exception:
        # Redis unavailable, silently continue
        pass


def batch_get_active_quest_session_states(
    character_ids: List[int],
) -> Dict[int, Optional[dict]]:
    """Batch get active quest session states for multiple characters.

    Args:
        character_ids: List of character IDs to fetch

    Returns:
        Dict mapping character_id -> session dict (or None if no active session)
    """
    try:
        with get_redis_client() as client:
            # Use pipeline for efficient batch retrieval
            pipe = client.pipeline()
            for char_id in character_ids:
                key = f"active_quest_session:{char_id}"
                pipe.get(key)
            
            results = pipe.execute()
            
            # Parse results
            result = {}
            for char_id, raw_value in zip(character_ids, results):
                if raw_value:
                    try:
                        result[char_id] = json.loads(raw_value)
                    except (json.JSONDecodeError, TypeError):
                        result[char_id] = None
                else:
                    result[char_id] = None
            return result
    except Exception:
        # Redis unavailable, return empty dict for all
        return {char_id: None for char_id in character_ids}


def batch_update_active_quest_session_states(
    updates_set: Dict[int, dict], updates_clear: List[int]
) -> None:
    """Batch update active quest session states for multiple characters.

    Each session is stored with a 48-hour TTL for automatic cleanup of stale sessions.

    Args:
        updates_set: Dict mapping character_id -> {"quest_id": int, "entry_timestamp": str}
        updates_clear: List of character_ids to clear (no active session)
    """
    if not updates_set and not updates_clear:
        return

    try:
        with get_redis_client() as client:
            pipe = client.pipeline()
            
            # Set new/updated sessions with 48-hour TTL
            for char_id, session_data in updates_set.items():
                key = f"active_quest_session:{char_id}"
                pipe.setex(key, 172800, json.dumps(session_data))  # 48 hours = 172800 seconds
            
            # Delete cleared sessions
            for char_id in updates_clear:
                key = f"active_quest_session:{char_id}"
                pipe.delete(key)
            
            pipe.execute()
    except Exception:
        # Redis unavailable, silently continue
        pass


def get_one_time_user_settings(user_id: str) -> Optional[dict]:
    """
    Atomically retrieve and delete one-time user settings.
    Returns None if already consumed or missing.
    """
    key = f"{ONE_TIME_USER_SETTINGS_PREFIX}{user_id}"
    with get_redis_client() as client:
        raw = client.eval(_ONE_TIME_USER_SETTINGS_GETDEL_LUA, 1, key)
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        return json.loads(raw)
    except Exception:
        # Fallback: if someone stored a non-JSON scalar unexpectedly
        return None


def one_time_user_settings_exists(user_id: str) -> bool:
    """Check existence without consuming."""
    key = f"{ONE_TIME_USER_SETTINGS_PREFIX}{user_id}"
    with get_redis_client() as client:
        return client.exists(key) == 1


# ======= Game Population ========
