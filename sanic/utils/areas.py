import services.postgres as postgres_client
import services.redis as redis_client
from utils.time import timestamp_to_datetime_string, get_current_datetime_string

from constants.redis import VALID_AREA_CACHE_TTL

from time import time


def get_valid_area_ids() -> tuple[list[int], str, str]:
    """
    Get all area IDs from the cache. If the cache is empty, fetch from the database
    and update the cache.
    """
    try:
        known_areas, source, timestamp = get_areas()
        return ([area.get("id") for area in known_areas], source, timestamp)
    except Exception as e:
        print(f"Error fetching area IDs: {e}")
        return ([], None, None)


def get_areas() -> tuple[list[dict], str, str]:
    """
    Get all areas from the cache. If the cache is empty, fetch from the database
    and update the cache.
    """
    try:
        known_areas_cached_data = redis_client.get_known_areas()
        cached_areas = known_areas_cached_data.get("areas")
        cached_timestamp: float = known_areas_cached_data.get("timestamp")
        if cached_areas and time() - cached_timestamp < VALID_AREA_CACHE_TTL:
            return (
                cached_areas,
                "cache",
                timestamp_to_datetime_string(cached_timestamp),
            )
        database_areas = postgres_client.get_all_areas()
        if not database_areas:
            return ([], None, None)
        redis_client.set_known_areas(database_areas)
        return (
            [area.model_dump() for area in database_areas],
            "database",
            get_current_datetime_string(),
        )
    except Exception as e:
        print(f"Error fetching area IDs: {e}")
        return ([], None, None)
