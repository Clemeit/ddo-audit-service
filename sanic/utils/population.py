import services.redis as redis_client
import services.postgres as postgres_client
import services.population_db as population_db
from models.game import PopulationPointInTime, PopulationDataPoint
from utils.time import datetime_to_datetime_string
from typing import Optional
from typing import Any

from constants.redis import (
    REPORT_1_DAY_CACHE_TTL,
    REPORT_1_WEEK_CACHE_TTL,
    REPORT_1_MONTH_CACHE_TTL,
    REPORT_1_YEAR_CACHE_TTL,
    REPORT_1_QUARTER_CACHE_TTL,
    POPULATION_TRENDS_HOURLY_CACHE_TTL,
    POPULATION_TRENDS_CACHE_TTL,
    POPULATION_TRENDS_SUMMARY_CACHE_TTL,
)

from datetime import datetime, timedelta, timezone


# def sum_server_population(
#     first: PopulationPointInTime, second: PopulationPointInTime
# ) -> PopulationPointInTime:
#     summed_data: list[dict[str, PopulationDataPoint]] = []
#     for first_data in first.data:
#         summed_data.append(first_data)
#     for second_data in second.data:
#         # if it exists in summed_data, add to it. Otherwise, append it.
#         pass
#     return PopulationPointInTime(timestamp=first.timestamp, data=summed_data)


def get_game_population_day() -> list[dict]:
    """
    Gets 1 day of game population reported by the minute.
    Checks cache then database.
    """

    def fetch_and_normalize_data():
        postgres_data = postgres_client.get_game_population_relative(1)
        # normalized_data = normalize_population_data(postgres_data)
        return [datum.model_dump() for datum in postgres_data]

    return get_cached_data_with_fallback(
        "get_game_population_day",
        fetch_and_normalize_data,
        REPORT_1_DAY_CACHE_TTL,
    )


def get_game_population_totals_day() -> list[dict]:
    """
    Gets 1 day of total game population per server.
    Checks cache then database.
    """

    def fetch_data():
        postgres_data = postgres_client.get_game_population_relative(1)
        _, total_data = summed_population_data_points(postgres_data)
        return {
            serverName: datum.model_dump() for serverName, datum in total_data.items()
        }

    return get_cached_data_with_fallback(
        "get_game_population_totals_day",
        fetch_data,
        REPORT_1_DAY_CACHE_TTL,
    )


def get_game_population_week() -> list[dict]:
    """
    Gets 1 week of game population reported as hourly averages.
    Checks cache then database.
    """

    def fetch_data():
        postgres_data = postgres_client.get_game_population_last_week()
        averaged_data = average_hourly_data(postgres_data)
        # normalized_data = normalize_population_data(averaged_data)
        return [datum.model_dump() for datum in averaged_data]

    return get_cached_data_with_fallback(
        "get_game_population_week",
        fetch_data,
        REPORT_1_WEEK_CACHE_TTL,
    )


def get_game_population_totals_week() -> list[dict]:
    """
    Gets 1 week of total game population per server.
    Checks cache then database.
    """

    def fetch_data():
        postgres_data = postgres_client.get_game_population_last_week()
        _, total_data = summed_population_data_points(postgres_data)
        return {
            serverName: datum.model_dump() for serverName, datum in total_data.items()
        }

    return get_cached_data_with_fallback(
        "get_game_population_totals_week",
        fetch_data,
        REPORT_1_WEEK_CACHE_TTL,
    )


def get_game_population_month() -> list[dict]:
    """
    Gets 1 month of game population reported as daily averages.
    Checks cache then database.
    """

    def fetch_data():
        postgres_data = postgres_client.get_game_population_last_month()
        averaged_data = average_daily_data(postgres_data)
        # normalized_data = normalize_population_data(averaged_data)
        return [datum.model_dump() for datum in averaged_data]

    return get_cached_data_with_fallback(
        "get_game_population_month",
        fetch_data,
        REPORT_1_MONTH_CACHE_TTL,
    )


def get_game_population_quarter() -> list[dict]:
    """
    Gets 1 quarter of game population reported as daily averages.
    Checks cache then database.
    """

    def fetch_data():
        end = _start_of_today_utc()
        start = end - timedelta(days=90)
        daily_points = population_db.get_daily_population_points(start, end)
        return [datum.model_dump() for datum in daily_points]

    return get_cached_data_with_fallback(
        "get_game_population_quarter",
        fetch_data,
        REPORT_1_QUARTER_CACHE_TTL,
    )


def get_game_population_totals_month() -> list[dict]:
    """
    Gets 1 month of total game population per server.
    Checks cache then database.
    """

    def fetch_data():
        postgres_data = postgres_client.get_game_population_last_month()
        _, total_data = summed_population_data_points(postgres_data)
        return {
            serverName: datum.model_dump() for serverName, datum in total_data.items()
        }

    return get_cached_data_with_fallback(
        "get_game_population_totals_month",
        fetch_data,
        REPORT_1_MONTH_CACHE_TTL,
    )


def get_game_population_totals_quarter() -> list[dict]:
    """
    Gets 1 quarter of total game population per server.
    Checks cache then database.
    """

    def fetch_data():
        end = _start_of_today_utc()
        start = end - timedelta(days=90)
        return population_db.get_population_totals(start, end)

    return get_cached_data_with_fallback(
        "get_game_population_totals_quarter",
        fetch_data,
        REPORT_1_QUARTER_CACHE_TTL,
    )


def get_game_population_year() -> list[dict]:
    """
    Gets 1 year of game population reported as daily averages.
    Checks cache then database.
    """

    def fetch_data():
        end = _start_of_today_utc()
        start = end - timedelta(days=365)
        daily_points = population_db.get_daily_population_points(start, end)
        return [datum.model_dump() for datum in daily_points]

    return get_cached_data_with_fallback(
        "get_game_population_year",
        fetch_data,
        REPORT_1_YEAR_CACHE_TTL,
    )


def get_game_population_totals_year() -> list[dict]:
    """
    Gets 1 year of total game population per server.
    Checks cache then database.
    """

    def fetch_data():
        end = _start_of_today_utc()
        start = end - timedelta(days=365)
        return population_db.get_population_totals(start, end)

    return get_cached_data_with_fallback(
        "get_game_population_totals_year",
        fetch_data,
        REPORT_1_YEAR_CACHE_TTL,
    )


def get_unique_character_and_guild_count_breakdown_day() -> dict:
    """
    Gets a unique character and guild count breakdown for the last day.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_unique_character_and_guild_count_breakdown_day",
        lambda: postgres_client.get_unique_character_and_guild_count(1),
        REPORT_1_DAY_CACHE_TTL,
    )


def get_unique_character_and_guild_count_breakdown_week() -> dict:
    """
    Gets a unique character and guild count breakdown for the last week.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_unique_character_and_guild_count_breakdown_week",
        lambda: postgres_client.get_unique_character_and_guild_count(7),
        REPORT_1_WEEK_CACHE_TTL,
    )


def get_unique_character_and_guild_count_breakdown_month() -> dict:
    """
    Gets a unique character and guild count breakdown for the last month.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_unique_character_and_guild_count_breakdown_month",
        lambda: postgres_client.get_unique_character_and_guild_count(30),
        REPORT_1_MONTH_CACHE_TTL,
    )


def get_unique_character_and_guild_count_breakdown_quarter() -> dict:
    """
    Gets a unique character and guild count breakdown for the last quarter.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_unique_character_and_guild_count_breakdown_quarter",
        lambda: postgres_client.get_unique_character_and_guild_count(90),
        REPORT_1_QUARTER_CACHE_TTL,
    )


def get_unique_character_and_guild_count_breakdown_year() -> dict:
    """
    Gets a unique character and guild count breakdown for the last year.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_unique_character_and_guild_count_breakdown_year",
        lambda: postgres_client.get_unique_character_and_guild_count(365),
        REPORT_1_YEAR_CACHE_TTL,
    )


def get_character_activity_stats_quarter() -> dict:
    """
    Gets character activity stats for the last quarter.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_character_activity_stats_quarter",
        lambda: postgres_client.get_character_activity_stats(90),
        REPORT_1_QUARTER_CACHE_TTL,
    )


def get_average_server_population_day() -> dict[str, Optional[float]]:
    """
    Gets 1 day of average server population data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_average_server_population_day",
        lambda: postgres_client.get_average_population_by_server(1),
        REPORT_1_DAY_CACHE_TTL,
    )


def get_average_server_population_week() -> dict[str, Optional[float]]:
    """
    Gets 7 days of average server population data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_average_server_population_week",
        lambda: postgres_client.get_average_population_by_server(7),
        REPORT_1_WEEK_CACHE_TTL,
    )


def get_average_server_population_month() -> dict[str, Optional[float]]:
    """
    Gets 28 days of average server population data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_average_server_population_month",
        lambda: postgres_client.get_average_population_by_server(28),
        REPORT_1_MONTH_CACHE_TTL,
    )


def get_average_server_population_quarter() -> dict[str, Optional[float]]:
    """
    Gets 90 days of average server population data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_average_server_population_quarter",
        lambda: postgres_client.get_average_population_by_server(90),
        REPORT_1_QUARTER_CACHE_TTL,
    )


def get_average_server_population_year() -> dict[str, Optional[float]]:
    """
    Gets 365 days of average server population data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_average_server_population_year",
        lambda: postgres_client.get_average_population_by_server(365),
        REPORT_1_YEAR_CACHE_TTL,
    )


def get_hourly_server_population_day() -> dict[str, Optional[float]]:
    """
    Gets 1 day of average hourly server population data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_hourly_server_population_day",
        lambda: postgres_client.get_average_population_by_hour_per_server(1),
        REPORT_1_DAY_CACHE_TTL,
    )


def get_hourly_server_population_week() -> dict[str, Optional[float]]:
    """
    Gets 7 days of average hourly server population data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_hourly_server_population_week",
        lambda: postgres_client.get_average_population_by_hour_per_server(7),
        REPORT_1_WEEK_CACHE_TTL,
    )


def get_hourly_server_population_month() -> dict[str, Optional[float]]:
    """
    Gets 28 days of average hourly server population data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_hourly_server_population_month",
        lambda: postgres_client.get_average_population_by_hour_per_server(28),
        REPORT_1_MONTH_CACHE_TTL,
    )


def get_hourly_server_population_quarter() -> dict[str, Optional[float]]:
    """
    Gets 90 days of average hourly server population data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_hourly_server_population_quarter",
        lambda: postgres_client.get_average_population_by_hour_per_server(90),
        REPORT_1_QUARTER_CACHE_TTL,
    )


def get_hourly_server_population_year() -> dict[str, Optional[float]]:
    """
    Gets 365 days of average hourly server population data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_hourly_server_population_year",
        lambda: postgres_client.get_average_population_by_hour_per_server(365),
        REPORT_1_YEAR_CACHE_TTL,
    )


def get_daily_server_population_day() -> dict[str, Optional[float]]:
    """
    Gets 1 day of average daily server population data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_daily_server_population_day",
        lambda: postgres_client.get_average_population_by_day_of_week_per_server(1),
        REPORT_1_DAY_CACHE_TTL,
    )


def get_daily_server_population_week() -> dict[str, Optional[float]]:
    """
    Gets 7 days of average daily server population data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_daily_server_population_week",
        lambda: postgres_client.get_average_population_by_day_of_week_per_server(7),
        REPORT_1_WEEK_CACHE_TTL,
    )


def get_daily_server_population_month() -> dict[str, Optional[float]]:
    """
    Gets 28 days of average daily server population data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_daily_server_population_month",
        lambda: postgres_client.get_average_population_by_day_of_week_per_server(28),
        REPORT_1_MONTH_CACHE_TTL,
    )


def get_daily_server_population_quarter() -> dict[str, Optional[float]]:
    """
    Gets 90 days of average daily server population data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_daily_server_population_quarter",
        lambda: postgres_client.get_average_population_by_day_of_week_per_server(90),
        REPORT_1_QUARTER_CACHE_TTL,
    )


def get_daily_server_population_year() -> dict[str, Optional[float]]:
    """
    Gets 365 days of average daily server population data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_daily_server_population_year",
        lambda: postgres_client.get_average_population_by_day_of_week_per_server(365),
        REPORT_1_YEAR_CACHE_TTL,
    )


def get_by_hour_and_day_of_week_server_population_week() -> (
    dict[str, dict[int, dict[int, dict[str, Optional[float]]]]]
):
    """
    Gets 7 days of average hourly server population data broken down by day of week.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_by_hour_and_day_of_week_server_population_week",
        lambda: postgres_client.get_average_population_by_hour_and_day_of_week_per_server(
            7
        ),
        REPORT_1_WEEK_CACHE_TTL,
    )


def get_by_hour_and_day_of_week_server_population_month() -> (
    dict[str, dict[int, dict[int, dict[str, Optional[float]]]]]
):
    """
    Gets 28 days of average hourly server population data broken down by day of week.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_by_hour_and_day_of_week_server_population_month",
        lambda: postgres_client.get_average_population_by_hour_and_day_of_week_per_server(
            28
        ),
        REPORT_1_MONTH_CACHE_TTL,
    )


def get_by_hour_and_day_of_week_server_population_quarter() -> (
    dict[str, dict[int, dict[int, dict[str, Optional[float]]]]]
):
    """
    Gets 90 days of average hourly server population data broken down by day of week.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_by_hour_and_day_of_week_server_population_quarter",
        lambda: postgres_client.get_average_population_by_hour_and_day_of_week_per_server(
            90
        ),
        REPORT_1_QUARTER_CACHE_TTL,
    )


def get_by_hour_and_day_of_week_server_population_year() -> (
    dict[str, dict[int, dict[int, dict[str, Optional[float]]]]]
):
    """
    Gets 365 days of average hourly server population data broken down by day of week.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_by_hour_and_day_of_week_server_population_year",
        lambda: postgres_client.get_average_population_by_hour_and_day_of_week_per_server(
            365
        ),
        REPORT_1_YEAR_CACHE_TTL,
    )


# ===== HELPER FUNCTIONS =====


def get_cached_data_with_fallback(key: str, fallback_func, ttl: int = 60 * 60) -> Any:
    """Get cached data, regenerate if expired."""
    cached_data = redis_client.get_by_key(key)

    if cached_data is None:
        fresh_data = fallback_func()
        redis_client.set_by_key(key, fresh_data, ttl=ttl)
        return fresh_data

    return cached_data


async def async_get_cached_data_with_fallback(
    key: str, fallback_func, ttl: int = 60 * 60
) -> Any:
    """Async version: get cached data, regenerate if expired.

    *fallback_func* must be an async callable (coroutine function).
    Uses the async Redis client so the event loop is never blocked.
    """
    cached_data = await redis_client.async_get_by_key(key)

    if cached_data is None:
        fresh_data = await fallback_func()
        await redis_client.async_set_by_key(key, fresh_data, ttl=ttl)
        return fresh_data

    return cached_data


def average_hourly_data(
    input_data: list[PopulationPointInTime],
) -> list[PopulationPointInTime]:
    if len(input_data) == 0:
        return []

    hourly_averaged_data: list[PopulationPointInTime] = []

    # Use (date, hour) tuple for grouping
    def get_date_hour(dt: datetime):
        return (dt.date(), dt.hour)

    current_dt = datetime.fromisoformat(input_data[0].timestamp)
    current_group = get_date_hour(current_dt)
    current_data_points: list[PopulationPointInTime] = []

    for data_point in input_data:
        data_point_datetime = datetime.fromisoformat(data_point.timestamp)
        group = get_date_hour(data_point_datetime)

        if group == current_group:
            current_data_points.append(data_point)
        else:
            averaged_data_points = averaged_population_data_points(current_data_points)
            if len(current_data_points) > 0:
                timestamp_string = datetime_to_datetime_string(
                    data_point_datetime.replace(
                        year=current_group[0].year,
                        month=current_group[0].month,
                        day=current_group[0].day,
                        hour=current_group[1],
                        minute=0,
                        second=0,
                        microsecond=0,
                    )
                )
                hourly_averaged_data.append(
                    PopulationPointInTime(
                        timestamp=timestamp_string,
                        data=averaged_data_points,
                    )
                )
            current_group = group
            current_data_points = [data_point]

    # Process the last batch
    if len(current_data_points) > 0:
        last_dt = datetime.fromisoformat(current_data_points[0].timestamp)
        timestamp_string = datetime_to_datetime_string(
            last_dt.replace(
                hour=current_group[1],
                minute=0,
                second=0,
                microsecond=0,
            )
        )
        averaged_data_points = averaged_population_data_points(current_data_points)
        hourly_averaged_data.append(
            PopulationPointInTime(
                timestamp=timestamp_string,
                data=averaged_data_points,
            )
        )

    return hourly_averaged_data


def average_daily_data(
    input_data: list[PopulationPointInTime],
) -> list[PopulationPointInTime]:
    if len(input_data) == 0:
        return []

    daily_averaged_data: list[PopulationPointInTime] = []

    # Use date object for grouping
    def get_date(dt: datetime):
        return dt.date()

    current_dt = datetime.fromisoformat(input_data[0].timestamp)
    current_group = get_date(current_dt)
    current_data_points: list[PopulationPointInTime] = []

    for data_point in input_data:
        data_point_datetime = datetime.fromisoformat(data_point.timestamp)
        group = get_date(data_point_datetime)

        if group == current_group:
            current_data_points.append(data_point)
        else:
            averaged_data_points = averaged_population_data_points(current_data_points)
            if len(current_data_points) > 0:
                timestamp_string = datetime_to_datetime_string(
                    data_point_datetime.replace(
                        year=current_group.year,
                        month=current_group.month,
                        day=current_group.day,
                        hour=0,
                        minute=0,
                        second=0,
                        microsecond=0,
                    )
                )
                daily_averaged_data.append(
                    PopulationPointInTime(
                        timestamp=timestamp_string,
                        data=averaged_data_points,
                    )
                )
            current_group = group
            current_data_points = [data_point]

    # Process the last batch
    if len(current_data_points) > 0:
        last_dt = datetime.fromisoformat(current_data_points[0].timestamp)
        timestamp_string = datetime_to_datetime_string(
            last_dt.replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0,
            )
        )
        averaged_data_points = averaged_population_data_points(current_data_points)
        daily_averaged_data.append(
            PopulationPointInTime(
                timestamp=timestamp_string,
                data=averaged_data_points,
            )
        )

    return daily_averaged_data


def summed_population_data_points(
    input_data: list[PopulationPointInTime],
) -> tuple[dict[str, int], dict[str, PopulationDataPoint]]:
    if len(input_data) == 0:
        return {}, {}

    total_counts: dict[str, int] = {}
    summed_data_points: dict[str, PopulationDataPoint] = {}

    for data_point in input_data:
        for server_name, server_data in data_point.data.items():
            if server_name in summed_data_points.keys():
                summed_data_points[
                    server_name
                ].character_count += server_data.character_count
                summed_data_points[server_name].lfm_count += server_data.lfm_count
            else:
                summed_data_points[server_name] = PopulationDataPoint(
                    character_count=server_data.character_count,
                    lfm_count=server_data.lfm_count,
                )
            if server_name in total_counts.keys():
                total_counts[server_name] += 1
            else:
                total_counts[server_name] = 1

    return total_counts, summed_data_points


def averaged_population_data_points(
    input_data: list[PopulationPointInTime],
) -> dict[str, PopulationDataPoint]:
    if len(input_data) == 0:
        return []

    total_counts, summed_data_points = summed_population_data_points(input_data)

    averaged_data_points: dict[str, PopulationDataPoint] = {}
    for server_name, server_data in summed_data_points.items():
        average_character_count = (
            round(
                summed_data_points[server_name].character_count
                / total_counts[server_name],
                4,
            )
            if total_counts[server_name] > 0
            else 0
        )
        average_lfm_count = (
            round(
                summed_data_points[server_name].lfm_count / total_counts[server_name], 4
            )
            if total_counts[server_name] > 0
            else 0
        )
        averaged_data_points[server_name] = PopulationDataPoint(
            character_count=average_character_count, lfm_count=average_lfm_count
        )

    return averaged_data_points


def normalize_population_data(
    input_data: list[PopulationPointInTime],
) -> list[PopulationPointInTime]:
    """
    Normalize population data per server using min-max normalization.
    Each server's values are normalized to 0-1 range based on that server's own min/max values.
    """
    if not input_data:
        return []

    # First pass: collect all values per server to find their individual min/max
    server_stats = {}
    valid_data_points = []

    for data_point in input_data:
        if not data_point.data:
            continue

        valid_server_data = {}
        for server_name, server_data in data_point.data.items():
            try:
                character_count = max(0.0, float(server_data.character_count))
                lfm_count = max(0.0, float(server_data.lfm_count))

                valid_server_data[server_name] = {
                    "character_count": character_count,
                    "lfm_count": lfm_count,
                }

                # Initialize server stats if not exists
                if server_name not in server_stats:
                    server_stats[server_name] = {
                        "character_counts": [],
                        "lfm_counts": [],
                    }

                server_stats[server_name]["character_counts"].append(character_count)
                server_stats[server_name]["lfm_counts"].append(lfm_count)

            except (ValueError, TypeError, AttributeError):
                continue

        if valid_server_data:
            valid_data_points.append(
                {
                    "timestamp": data_point.timestamp,
                    "data": valid_server_data,
                }
            )

    if not server_stats:
        return []

    # Calculate min/max and ranges for each server
    server_normalization_params = {}
    for server_name, stats in server_stats.items():
        char_counts = stats["character_counts"]
        lfm_counts = stats["lfm_counts"]

        char_min, char_max = min(char_counts), max(char_counts)
        lfm_min, lfm_max = min(lfm_counts), max(lfm_counts)

        server_normalization_params[server_name] = {
            "char_min": char_min,
            "char_range": max(0.0, char_max - char_min),
            "lfm_min": lfm_min,
            "lfm_range": max(0.0, lfm_max - lfm_min),
        }

    # Second pass: normalize values to 0-1 range per server
    normalized_data = []
    for data_point in valid_data_points:
        normalized_server_data = {}

        for server_name, server_data in data_point["data"].items():
            params = server_normalization_params[server_name]

            # Normalize character count
            if params["char_range"] > 0:
                normalized_char_count = (
                    server_data["character_count"] - params["char_min"]
                ) / params["char_range"]
            else:
                normalized_char_count = 0.0

            # Normalize LFM count
            if params["lfm_range"] > 0:
                normalized_lfm_count = (
                    server_data["lfm_count"] - params["lfm_min"]
                ) / params["lfm_range"]
            else:
                normalized_lfm_count = 0.0

            normalized_server_data[server_name] = PopulationDataPoint(
                character_count=round(normalized_char_count, 6),
                lfm_count=round(normalized_lfm_count, 6),
            )

        normalized_data.append(
            PopulationPointInTime(
                timestamp=data_point["timestamp"],
                data=normalized_server_data,
            )
        )

    return normalized_data


# ===== TRENDS (backed by the population_hourly rollup) =====

# Range name -> lookback in days (None = everything we have).
TRENDS_RANGES: dict[str, Optional[int]] = {
    "week": 7,
    "month": 30,
    "quarter": 90,
    "year": 365,
    "all": None,
}

# Range name -> (default bucket, allowed buckets). Keeps every response to a few thousand
# points per server at most.
TRENDS_BUCKETS: dict[str, tuple[str, tuple[str, ...]]] = {
    "week": ("hour", ("hour", "day")),
    "month": ("day", ("hour", "day", "week")),
    "quarter": ("day", ("day", "week")),
    "year": ("day", ("day", "week", "month")),
    "all": ("week", ("day", "week", "month")),
}

# Earliest bucket considered for range=all; older than any game_info data.
TRENDS_EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)


def _start_of_today_utc() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _start_of_hour_utc() -> datetime:
    return datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)


def truncate_to_bucket(value: datetime, unit: str) -> datetime:
    """Start of the UTC bucket containing ``value`` (weeks start on Monday, like date_trunc)."""
    value = value.replace(minute=0, second=0, microsecond=0)
    if unit == "hour":
        return value
    value = value.replace(hour=0)
    if unit == "day":
        return value
    if unit == "week":
        return value - timedelta(days=value.weekday())
    if unit == "month":
        return value.replace(day=1)
    raise ValueError(f"Invalid bucket unit '{unit}'")


def resolve_trends_query(
    range_name: Optional[str], bucket: Optional[str]
) -> tuple[str, str]:
    """Validate and normalize ?range=&bucket=. Raises ValueError with a user-facing message."""
    range_name = (range_name or "month").lower()
    if range_name not in TRENDS_RANGES:
        raise ValueError(
            f"Invalid range '{range_name}'. Supported ranges: {', '.join(TRENDS_RANGES)}"
        )
    default_bucket, allowed = TRENDS_BUCKETS[range_name]
    bucket = (bucket or default_bucket).lower()
    if bucket not in allowed:
        raise ValueError(
            f"Invalid bucket '{bucket}' for range '{range_name}'. "
            f"Supported buckets: {', '.join(allowed)}"
        )
    return range_name, bucket


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 2)


def build_trends_series(rows: list[dict], end: datetime, bucket: str) -> dict:
    """Group rollup rows into {server: [point, ...]} and flag the in-progress bucket."""
    current_bucket = truncate_to_bucket(end, bucket).replace(tzinfo=None)
    has_partial = current_bucket != end.replace(tzinfo=None)
    series: dict[str, list[dict]] = {}
    for row in rows:
        t = row["t"]
        if t.tzinfo is not None:
            t = t.astimezone(timezone.utc).replace(tzinfo=None)
        point = {
            "t": datetime_to_datetime_string(t),
            "samples": int(row["samples"]),
            "players": {
                "avg": _round(row["players_avg"]),
                "min": int(row["players_min"]),
                "max": int(row["players_max"]),
            },
            "lfms": {
                "avg": _round(row["lfms_avg"]),
                "min": int(row["lfms_min"]),
                "max": int(row["lfms_max"]),
            },
        }
        if has_partial and t == current_bucket:
            point["partial"] = True
        series.setdefault(row["server_name"], []).append(point)
    return series


def build_trends_total(rows: list[dict], end: datetime, bucket: str) -> list[dict]:
    """Combined-server points; min/max are the lowest/highest hourly total in each bucket."""
    current_bucket = truncate_to_bucket(end, bucket).replace(tzinfo=None)
    has_partial = current_bucket != end.replace(tzinfo=None)
    points = []
    for row in rows:
        t = row["t"]
        if t.tzinfo is not None:
            t = t.astimezone(timezone.utc).replace(tzinfo=None)
        point = {
            "t": datetime_to_datetime_string(t),
            "hours": int(row["hours"]),
            "players": {
                "avg": _round(row["players_avg"]),
                "min": _round(row["players_min"]),
                "max": _round(row["players_max"]),
            },
            "lfms": {
                "avg": _round(row["lfms_avg"]),
                "min": _round(row["lfms_min"]),
                "max": _round(row["lfms_max"]),
            },
        }
        if has_partial and t == current_bucket:
            point["partial"] = True
        points.append(point)
    return points


def get_population_trends(range_name: str, bucket: str) -> dict:
    """Per-server population series for a validated (range, bucket). Checks cache then database.

    The range ends at the start of the current UTC hour. For day/week/month buckets the start is
    aligned down to a bucket boundary so the first bucket is complete; the last one may be
    in progress and is marked ``partial``.
    """

    def fetch_data():
        end = _start_of_hour_utc()
        days = TRENDS_RANGES[range_name]
        start = TRENDS_EPOCH if days is None else end - timedelta(days=days)
        start = truncate_to_bucket(start, bucket)
        rows = population_db.get_population_buckets(start, end, bucket)
        total_rows = population_db.get_total_population_buckets(start, end, bucket)
        return {
            "range": range_name,
            "bucket": bucket,
            "start": datetime_to_datetime_string(start),
            "end": datetime_to_datetime_string(end),
            "series": build_trends_series(rows, end, bucket),
            "total": build_trends_total(total_rows, end, bucket),
        }

    ttl = (
        POPULATION_TRENDS_HOURLY_CACHE_TTL
        if bucket == "hour"
        else POPULATION_TRENDS_CACHE_TTL
    )
    return get_cached_data_with_fallback(
        f"population_trends_{range_name}_{bucket}", fetch_data, ttl
    )


def _minus_one_year(value: datetime) -> datetime:
    try:
        return value.replace(year=value.year - 1)
    except ValueError:  # Feb 29
        return value.replace(year=value.year - 1, day=28)


def summarize_window(start: datetime, end: datetime) -> Optional[dict]:
    """Average players/LFMs over [start, end), or None when there is no data at all.

    ``coverage`` is the fraction of hours in the window that have data, so the UI can flag
    comparisons against a period with gaps.
    """
    servers, hours_with_data = population_db.get_population_window(start, end)
    if not servers:
        return None
    expected_hours = max(1, int((end - start).total_seconds() // 3600))
    return {
        "start": datetime_to_datetime_string(start),
        "end": datetime_to_datetime_string(end),
        "coverage": round(min(1.0, hours_with_data / expected_hours), 3),
        "total": {
            "players": _round(sum(s["players"] for s in servers.values())),
            "lfms": _round(sum(s["lfms"] for s in servers.values())),
        },
        "servers": {
            name: {"players": _round(s["players"]), "lfms": _round(s["lfms"])}
            for name, s in servers.items()
        },
    }


def comparison_windows(end: datetime) -> dict[str, tuple[tuple, tuple]]:
    """(current, previous) windows for each headline comparison, all ending at ``end``."""
    week = timedelta(days=7)
    month = timedelta(days=30)
    month_start = end.replace(day=1, hour=0)
    if end - month_start < timedelta(days=1):
        # First hours of a month: compare the month that just finished instead.
        current_end = month_start
        month_start = (month_start - timedelta(days=1)).replace(day=1)
    else:
        current_end = end
    return {
        "week": ((end - week, end), (end - 2 * week, end - week)),
        "month": ((end - month, end), (end - 2 * month, end - month)),
        "year_over_year": (
            (month_start, current_end),
            (_minus_one_year(month_start), _minus_one_year(current_end)),
        ),
    }


def get_population_trends_summary() -> dict:
    """Headline comparisons and records for the Trends page. Checks cache then database."""

    def fetch_data():
        end = _start_of_hour_utc()
        comparisons = {
            name: {
                "current": summarize_window(*current),
                "previous": summarize_window(*previous),
            }
            for name, (current, previous) in comparison_windows(end).items()
        }
        by_weekday, by_hour = population_db.get_average_total_by_weekday_and_hour(
            end - timedelta(days=90), end
        )
        peak_all_time = population_db.get_peak_total_hour(TRENDS_EPOCH, end)
        peak_last_year = population_db.get_peak_total_hour(
            end - timedelta(days=365), end
        )
        for peak in (peak_all_time, peak_last_year):
            if peak:
                peak["players"] = _round(peak["players"])
        records = {
            "peak_hour_all_time": peak_all_time,
            "peak_hour_last_year": peak_last_year,
            "peak_server_all_time": population_db.get_peak_server_snapshot(
                TRENDS_EPOCH, end
            ),
            "last_90_days_by_weekday": [
                {"day": d["day"], "players": _round(d["players"])} for d in by_weekday
            ],
            "last_90_days_by_hour": [
                {"hour": h["hour"], "players": _round(h["players"])} for h in by_hour
            ],
        }
        return {
            "generated_at": datetime_to_datetime_string(end),
            "comparisons": comparisons,
            "records": records,
        }

    return get_cached_data_with_fallback(
        "population_trends_summary",
        fetch_data,
        POPULATION_TRENDS_SUMMARY_CACHE_TTL,
    )
