from time import time

import services.postgres as postgres_client
import services.redis as redis_client
from utils.time import timestamp_to_datetime_string, get_current_datetime_string

from constants.redis import VALID_QUEST_CACHE_TTL
from models.quest import QuestV2


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


def get_quests(skip_cache: bool = False) -> tuple[list[dict], str, str]:
    """
    Get all quests from the cache. If the cache is empty, fetch from the database
    and update the cache.
    """
    try:
        if not skip_cache:
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


def get_quests_with_metrics(skip_cache: bool = False) -> tuple[list[dict], str, str]:
    """
    Get all quests with metrics from the cache. If the cache is empty, fetch from the database
    and update the cache.

    Returns quests with flattened metrics fields from the LEFT JOIN with quest_metrics table.
    Uses separate Redis cache key from get_quests() to avoid conflicts.

    Returns:
        tuple of (quest_list, source, timestamp) where:
        - quest_list: list of dicts with Quest fields + flattened metrics
        - source: "cache" or "database"
        - timestamp: ISO format datetime string
    """
    try:
        if not skip_cache:
            cached_data = redis_client.get_quests_with_metrics()
            cached_quests = cached_data.get("quests")
            cached_timestamp: float = cached_data.get("timestamp")
            if (
                cached_quests
                and cached_timestamp is not None
                and time() - cached_timestamp < VALID_QUEST_CACHE_TTL
            ):
                return (
                    cached_quests,
                    "cache",
                    timestamp_to_datetime_string(cached_timestamp),
                )

        # Fetch from database with metrics via LEFT JOIN
        quest_metrics_tuples = postgres_client.get_all_quests_with_metrics()
        if not quest_metrics_tuples:
            print("No quests found in the database.")
            return ([], None, None)

        # Convert (Quest, metrics_dict|None) tuples to QuestV2 objects
        quest_v2_list = []
        for quest, metrics in quest_metrics_tuples:
            # Create QuestV2 from quest base fields
            quest_dict = quest.model_dump()

            # Add metrics fields if they exist
            if metrics:
                quest_dict["heroic_xp_per_minute_relative"] = metrics.get(
                    "heroic_xp_per_minute_relative"
                )
                quest_dict["epic_xp_per_minute_relative"] = metrics.get(
                    "epic_xp_per_minute_relative"
                )
                quest_dict["heroic_popularity_relative"] = metrics.get(
                    "heroic_popularity_relative"
                )
                quest_dict["epic_popularity_relative"] = metrics.get(
                    "epic_popularity_relative"
                )

            quest_v2_list.append(QuestV2(**quest_dict))

        # Cache the result
        redis_client.set_quests_with_metrics(quest_v2_list)

        return (
            [quest.model_dump() for quest in quest_v2_list],
            "database",
            get_current_datetime_string(),
        )
    except Exception as e:
        print(f"Error fetching quests with metrics: {e}")
        return ([], None, None)
