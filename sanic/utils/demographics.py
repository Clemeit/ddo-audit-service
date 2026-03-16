from enum import Enum
import services.redis as redis_client
import services.postgres as postgres_client
from typing import Any

from constants.redis import (
    REPORT_1_DAY_CACHE_TTL,
    REPORT_1_WEEK_CACHE_TTL,
    REPORT_1_MONTH_CACHE_TTL,
    REPORT_1_QUARTER_CACHE_TTL,
    REPORT_1_YEAR_CACHE_TTL,
)


class ReportLookback(str, Enum):
    day = "day"
    week = "week"
    month = "month"
    quarter = "quarter"
    year = "year"


report_lookback_map = {
    ReportLookback.day: {
        "days": 1,
        "cache_ttl": REPORT_1_DAY_CACHE_TTL,
    },
    ReportLookback.week: {
        "days": 7,
        "cache_ttl": REPORT_1_WEEK_CACHE_TTL,
    },
    ReportLookback.month: {
        "days": 28,
        "cache_ttl": REPORT_1_MONTH_CACHE_TTL,
    },
    ReportLookback.quarter: {
        "days": 90,
        "cache_ttl": REPORT_1_QUARTER_CACHE_TTL,
    },
    ReportLookback.year: {
        "days": 365,
        "cache_ttl": REPORT_1_YEAR_CACHE_TTL,
    },
}


async def get_race_distribution(
    period: ReportLookback, activity_level: str = "all"
) -> dict[str, int]:
    """
    Gets race demographics data for the specified period.
    Checks cache then database.
    """
    if period not in report_lookback_map:
        raise ValueError(
            f"Invalid period '{period}'. Supported periods: {', '.join(report_lookback_map.keys())}"
        )

    days = report_lookback_map[period]["days"]
    cache_ttl = report_lookback_map[period]["cache_ttl"]

    return await async_get_cached_data_with_fallback(
        f"race_distribution_{period}_{activity_level}",
        lambda: postgres_client.async_get_race_distribution(days, activity_level),
        cache_ttl,
    )


async def get_gender_distribution(
    period: ReportLookback, activity_level: str = "all"
) -> dict[str, int]:
    """
    Gets gender demographics data for the specified period.
    Checks cache then database.
    """
    if period not in report_lookback_map:
        raise ValueError(
            f"Invalid period '{period}'. Supported periods: {', '.join(report_lookback_map.keys())}"
        )

    days = report_lookback_map[period]["days"]
    cache_ttl = report_lookback_map[period]["cache_ttl"]

    return await async_get_cached_data_with_fallback(
        f"gender_distribution_{period}_{activity_level}",
        lambda: postgres_client.async_get_gender_distribution(days, activity_level),
        cache_ttl,
    )


async def get_total_level_distribution(
    period: ReportLookback, activity_level: str = "all"
) -> dict[str, int]:
    """
    Gets total level demographics data for the specified period.
    Checks cache then database.
    """
    if period not in report_lookback_map:
        raise ValueError(
            f"Invalid period '{period}'. Supported periods: {', '.join(report_lookback_map.keys())}"
        )

    days = report_lookback_map[period]["days"]
    cache_ttl = report_lookback_map[period]["cache_ttl"]

    return await async_get_cached_data_with_fallback(
        f"total_level_distribution_{period}_{activity_level}",
        lambda: postgres_client.async_get_total_level_distribution(
            days, activity_level
        ),
        cache_ttl,
    )


async def get_class_count_distribution(
    period: ReportLookback, activity_level: str = "all"
) -> dict[str, int]:
    """
    Gets class count demographics data for the specified period.
    Checks cache then database.
    """
    if period not in report_lookback_map:
        raise ValueError(
            f"Invalid period '{period}'. Supported periods: {', '.join(report_lookback_map.keys())}"
        )

    days = report_lookback_map[period]["days"]
    cache_ttl = report_lookback_map[period]["cache_ttl"]

    return await async_get_cached_data_with_fallback(
        f"class_count_distribution_{period}_{activity_level}",
        lambda: postgres_client.async_get_class_count_distribution(
            days, activity_level
        ),
        cache_ttl,
    )


async def get_primary_class_distribution(
    period: ReportLookback, activity_level: str = "all"
) -> dict[str, int]:
    """
    Gets primary class demographics data for the specified period.
    Checks cache then database.
    """
    if period not in report_lookback_map:
        raise ValueError(
            f"Invalid period '{period}'. Supported periods: {', '.join(report_lookback_map.keys())}"
        )

    days = report_lookback_map[period]["days"]
    cache_ttl = report_lookback_map[period]["cache_ttl"]

    return await async_get_cached_data_with_fallback(
        f"primary_class_distribution_{period}_{activity_level}",
        lambda: postgres_client.async_get_primary_class_distribution(
            days, activity_level
        ),
        cache_ttl,
    )


async def get_guild_affiliation_distribution(
    period: ReportLookback, activity_level: str = "all"
) -> dict[str, dict[str, int]]:
    """
    Gets guild affiliation demographics data for the specified period.
    Checks cache then database.
    """
    if period not in report_lookback_map:
        raise ValueError(
            f"Invalid period '{period}'. Supported periods: {', '.join(report_lookback_map.keys())}"
        )

    days = report_lookback_map[period]["days"]
    cache_ttl = report_lookback_map[period]["cache_ttl"]

    return await async_get_cached_data_with_fallback(
        f"guild_affiliation_distribution_{period}_{activity_level}",
        lambda: postgres_client.async_get_guild_affiliation_distribution(
            days, activity_level
        ),
        cache_ttl,
    )


def get_cached_data_with_fallback(key: str, fallback_func, cache_ttl: int) -> dict:
    """Get cached data, regenerate if expired."""
    cached_data = redis_client.get_by_key(key)

    if cached_data is None:
        fresh_data = fallback_func()
        redis_client.set_by_key(key, fresh_data, ttl=cache_ttl)
        return fresh_data

    return cached_data


async def async_get_cached_data_with_fallback(
    key: str, fallback_func, cache_ttl: int
) -> Any:
    """Async version: get cached data, regenerate if expired.

    *fallback_func* must be an async callable (coroutine function).
    Uses the async Redis client so the event loop is never blocked.
    """
    cached_data = await redis_client.async_get_by_key(key)

    if cached_data is None:
        fresh_data = await fallback_func()
        await redis_client.async_set_by_key(key, fresh_data, ttl=cache_ttl)
        return fresh_data

    return cached_data
