import services.redis as redis_client
import services.postgres as postgres_client
from models.game import PopulationPointInTime, PopulationDataPoint
from utils.time import datetime_to_datetime_string
from typing import Optional

from constants.redis import (
    POPULATION_1_DAY_CACHE_TTL,
    POPULATION_1_WEEK_CACHE_TTL,
    POPULATION_1_MONTH_CACHE_TTL,
    POPULATION_1_YEAR_CACHE_TTL,
    POPULATION_1_QUARTER_CACHE_TTL,
)

from datetime import datetime


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
        POPULATION_1_DAY_CACHE_TTL,
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
        POPULATION_1_DAY_CACHE_TTL,
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
        POPULATION_1_WEEK_CACHE_TTL,
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
        POPULATION_1_WEEK_CACHE_TTL,
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
        POPULATION_1_MONTH_CACHE_TTL,
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
        POPULATION_1_MONTH_CACHE_TTL,
    )


def get_game_population_year() -> list[dict]:
    """
    Gets 1 year of game population reported as daily averages.
    Checks cache then database.
    """

    def fetch_data():
        postgres_data = postgres_client.get_game_population_last_year()
        averaged_data = average_daily_data(postgres_data)
        # normalized_data = normalize_population_data(averaged_data)
        return [datum.model_dump() for datum in averaged_data]

    return get_cached_data_with_fallback(
        "get_game_population_year",
        fetch_data,
        POPULATION_1_YEAR_CACHE_TTL,
    )


def get_game_population_totals_year() -> list[dict]:
    """
    Gets 1 year of total game population per server.
    Checks cache then database.
    """

    def fetch_data():
        postgres_data = postgres_client.get_game_population_last_year()
        _, total_data = summed_population_data_points(postgres_data)
        return {
            serverName: datum.model_dump() for serverName, datum in total_data.items()
        }

    return get_cached_data_with_fallback(
        "get_game_population_totals_year",
        fetch_data,
        POPULATION_1_YEAR_CACHE_TTL,
    )


def get_unique_character_and_guild_count_breakdown_month() -> dict:
    """
    Gets a unique character and guild count breakdown for the last month.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_unique_character_and_guild_count_breakdown_month",
        lambda: postgres_client.get_unique_character_and_guild_count(30),
        POPULATION_1_QUARTER_CACHE_TTL,
    )


def get_unique_character_and_guild_count_breakdown_quarter() -> dict:
    """
    Gets a unique character and guild count breakdown for the last quarter.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_unique_character_and_guild_count_breakdown_quarter",
        lambda: postgres_client.get_unique_character_and_guild_count(90),
        POPULATION_1_QUARTER_CACHE_TTL,
    )


def get_character_activity_stats_quarter() -> dict:
    """
    Gets character activity stats for the last quarter.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_character_activity_stats_quarter",
        lambda: postgres_client.get_character_activity_stats(90),
        POPULATION_1_QUARTER_CACHE_TTL,
    )


def get_average_server_population_quarter() -> dict[str, Optional[float]]:
    """
    Gets 90 days of average server population data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_average_server_population_quarter",
        lambda: postgres_client.get_average_population_by_server(90),
    )


def get_average_server_population_week() -> dict[str, Optional[float]]:
    """
    Gets 7 days of average server population data.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "get_average_server_population_week",
        lambda: postgres_client.get_average_population_by_server(7),
    )


# ===== HELPER FUNCTIONS =====


def get_cached_data_with_fallback(key: str, fallback_func, ttl: int = 60 * 60) -> dict:
    """Get cached data, regenerate if expired."""
    cached_data = redis_client.get_by_key(key)

    if not cached_data:
        fresh_data = fallback_func()
        redis_client.set_by_key(key, fresh_data, ttl=ttl)
        return fresh_data

    return cached_data


def average_hourly_data(
    input_data: list[PopulationPointInTime],
) -> list[PopulationPointInTime]:
    if len(input_data) == 0:
        return []

    hourly_averaged_data: list[PopulationPointInTime] = []

    current_hour = datetime.fromisoformat(input_data[0].timestamp).hour
    current_data_points: list[PopulationPointInTime] = []

    for data_point in input_data:
        data_point_datetime = datetime.fromisoformat(data_point.timestamp)
        data_point_hour = data_point_datetime.hour

        if data_point_hour == current_hour:
            current_data_points.append(data_point)
        else:
            averaged_data_points = averaged_population_data_points(current_data_points)
            if len(current_data_points) > 0:
                timestamp_string = datetime_to_datetime_string(
                    datetime.fromisoformat(current_data_points[0].timestamp).replace(
                        hour=current_hour, minute=0, second=0, microsecond=0
                    )
                )
                hourly_averaged_data.append(
                    PopulationPointInTime(
                        timestamp=timestamp_string,
                        data=averaged_data_points,
                    )
                )
            current_hour = data_point_hour
            current_data_points = [data_point]

    return hourly_averaged_data


def average_daily_data(
    input_data: list[PopulationPointInTime],
) -> list[PopulationPointInTime]:
    if len(input_data) == 0:
        return []

    hourly_averaged_data: list[PopulationPointInTime] = []

    current_day = datetime.fromisoformat(input_data[0].timestamp).day
    current_data_points: list[PopulationPointInTime] = []

    for data_point in input_data:
        data_point_datetime = datetime.fromisoformat(data_point.timestamp)
        data_point_day = data_point_datetime.day

        if data_point_day == current_day:
            current_data_points.append(data_point)
        else:
            averaged_data_points = averaged_population_data_points(current_data_points)
            if len(current_data_points) > 0:
                timestamp_string = datetime_to_datetime_string(
                    datetime.fromisoformat(current_data_points[0].timestamp).replace(
                        day=current_day, hour=0, minute=0, second=0, microsecond=0
                    )
                )
                hourly_averaged_data.append(
                    PopulationPointInTime(
                        timestamp=timestamp_string,
                        data=averaged_data_points,
                    )
                )
            current_day = data_point_day
            current_data_points = [data_point]

    return hourly_averaged_data


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
