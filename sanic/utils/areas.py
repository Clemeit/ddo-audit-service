import services.postgres as postgres_client
import services.redis as redis_client

area_cache_lifetime = 60 * 60 * 24 * 7  # 7 days in seconds

def get_valid_area_ids() -> tuple[list[int], str, str]:
    """
    Get all area IDs from the cache. If the cache is empty, fetch from the database
    and update the cache.
    """
    source = "cache"
    try:
        area_ids_dict, timestamp = redis_client.get_valid_area_ids()
        if not area_ids_dict:
            area_ids = postgres_client.get_all_area_ids()
            if not area_ids:
                return ([], None, None)
            source = "database"
            area_ids_dict = [area_id for area_id in area_ids]
            redis_client.set_valid_area_ids(area_ids_dict)
    except Exception as e:
        print(f"Error fetching area IDs: {e}")
        return ([], None, None)
    return (area_ids_dict, source, timestamp)


def get_areas() -> tuple[list[dict], str, str]:
    """
    Get all areas from the cache. If the cache is empty, fetch from the database
    and update the cache.
    """
    source = "cache"
    try:
        areas_dict, timestamp = redis_client.get_all_areas()
        if not areas_dict:
            areas = postgres_client.get_all_areas()
            if not areas:
                return ([], None, None)
            source = "database"
            areas_dict = [area.model_dump() for area in areas]
            redis_client.set_all_areas(areas_dict)
    except Exception as e:
        print(f"Error fetching areas: {e}")
        return ([], None, None)
    return (areas_dict, source, timestamp)
