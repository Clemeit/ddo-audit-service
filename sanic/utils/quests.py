import services.postgres as postgres_client
import services.redis as redis_client
from utils.time import timestamp_to_datetime_string, get_current_datetime_string

from constants.redis import VALID_QUEST_CACHE_TTL

from time import time


def get_valid_quest_ids() -> tuple[list[int], str, str]:
    """
    Get all quest IDs from the cache. If the cache is empty, fetch from the database
    and update the cache.
    """
    try:
        known_quests, source, timestamp = get_quests()
        return ([quest.get("id") for quest in known_quests], source, timestamp)
    except Exception as e:
        print(f"Error fetching quest IDs: {e}")
        return ([], None, None)


def get_quests() -> tuple[list[dict], str, str]:
    """
    Get all quests from the cache. If the cache is empty, fetch from the database
    and update the cache.
    """
    try:
        known_quests_cached_data = redis_client.get_known_quests()
        cached_quests = known_quests_cached_data.get("quests")
        cached_timestamp: float = known_quests_cached_data.get("timestamp")
        if cached_quests and time() - cached_timestamp < VALID_QUEST_CACHE_TTL:
            return (
                cached_quests,
                "cache",
                timestamp_to_datetime_string(cached_timestamp),
            )
        database_quests = postgres_client.get_all_quests()
        if not database_quests:
            print("No quests found in the database.")
            return ([], None, None)
        redis_client.set_known_quests(database_quests)
        return (
            [quest.model_dump() for quest in database_quests],
            "database",
            get_current_datetime_string(),
        )
    except Exception as e:
        print(f"Error fetching quest IDs: {e}")
        return ([], None, None)
