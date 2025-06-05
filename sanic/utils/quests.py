import services.postgres as postgres_client
import services.redis as redis_client


def get_quests() -> tuple[list[dict], str, str]:
    """
    Get all quests from the cache. If the cache is empty, fetch from the database
    and update the cache.
    """
    source = "cache"
    try:
        quests_dict, timestamp = redis_client.get_all_quests()
        if not quests_dict:
            quests = postgres_client.get_all_quests()
            if not quests:
                return ([], None, None)
            source = "database"
            quests_dict = [quest.model_dump() for quest in quests]
            redis_client.set_all_quests(quests_dict)
    except Exception as e:
        print(f"Error fetching quests: {e}")
        return ([], None, None)
    return (quests_dict, source, timestamp)
