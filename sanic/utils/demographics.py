import services.redis as redis_client
import services.postgres as postgres_client

from constants.redis import (
    DEMOGRAPHICS_CACHE_TTL,
)


def get_race_demographics_quarter() -> dict[str, int]:
    """
    Gets 90 days of race demographics data.
    Checks cache then database.
    """

    return get_cached_data_with_fallback(
        "get_race_demographics_quarter",
        lambda: postgres_client.get_race_distribution(90),
    )


def get_race_demographics_week() -> dict[str, int]:
    """
    Gets 7 days of race demographics data.
    Checks cache then database.
    """

    return get_cached_data_with_fallback(
        "get_race_demographics_week",
        lambda: postgres_client.get_race_distribution(7),
    )


def get_gender_demographics_quarter() -> dict[str, int]:
    """
    Gets 90 days of gender demographics data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_gender_demographics_quarter",
        lambda: postgres_client.get_gender_distribution(90),
    )


def get_gender_demographics_week() -> dict[str, int]:
    """
    Gets 7 days of gender demographics data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_gender_demographics_week",
        lambda: postgres_client.get_gender_distribution(7),
    )


def get_total_level_demographics_quarter() -> dict[str, int]:
    """
    Gets 90 days of total level demographics data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_total_level_demographics_quarter",
        lambda: postgres_client.get_total_level_distribution(90),
    )


def get_total_level_demographics_week() -> dict[str, int]:
    """
    Gets 7 days of total level demographics data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_total_level_demographics_week",
        lambda: postgres_client.get_total_level_distribution(7),
    )


def get_class_count_demographics_quarter() -> dict[str, int]:
    """
    Gets 90 days of class count demographics data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_class_count_demographics_quarter",
        lambda: postgres_client.get_class_count_distribution(90),
    )


def get_class_count_demographics_week() -> dict[str, int]:
    """
    Gets 7 days of class count demographics data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_class_count_demographics_week",
        lambda: postgres_client.get_class_count_distribution(7),
    )


def get_cached_data_with_fallback(key: str, fallback_func) -> dict:
    """Get cached data, regenerate if expired."""
    cached_data = redis_client.get_by_key(key)

    if not cached_data:
        fresh_data = fallback_func()
        redis_client.set_by_key(key, fresh_data, ttl=DEMOGRAPHICS_CACHE_TTL)
        return fresh_data

    return cached_data
