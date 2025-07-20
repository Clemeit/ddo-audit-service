import services.redis as redis_client
import services.postgres as postgres_client
from models.game import PopulationPointInTime, PopulationDataPoint
from utils.time import datetime_to_datetime_string

from constants.redis import (
    POPULATION_1_DAY_CACHE_TTL,
    POPULATION_1_WEEK_CACHE_TTL,
    POPULATION_1_MONTH_CACHE_TTL,
    POPULATION_1_YEAR_CACHE_TTL,
    POPULATION_1_QUARTER_CACHE_TTL,
)

from datetime import datetime


def sum_server_population(
    first: PopulationPointInTime, second: PopulationPointInTime
) -> PopulationPointInTime:
    summed_data: list[dict[str, PopulationDataPoint]] = []
    for first_data in first.data:
        summed_data.append(first_data)
    for second_data in second.data:
        # if it exists in summed_data, add to it. Otherwise, append it.
        pass
    return PopulationPointInTime(timestamp=first.timestamp, data=summed_data)


def get_game_population_1_day() -> list[dict]:
    """
    Gets 1 day of game population reported by the minute.
    Checks cache then database.
    """

    def fetch_and_normalize_data():
        postgres_data = postgres_client.get_game_population_relative(1)
        normalized_data = normalize_population_data(postgres_data)
        return [datum.model_dump() for datum in normalized_data]

    return get_cached_data_with_fallback(
        "game_population_1_day",
        fetch_and_normalize_data,
        POPULATION_1_DAY_CACHE_TTL,
    )


def get_game_population_totals_1_day() -> list[dict]:
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
        "game_population_totals_1_day",
        fetch_data,
        POPULATION_1_DAY_CACHE_TTL,
    )


def get_game_population_1_week() -> list[dict]:
    """
    Gets 1 week of game population reported as hourly averages.
    Checks cache then database.
    """

    def fetch_data():
        postgres_data = postgres_client.get_game_population_last_week()
        averaged_data = average_hourly_data(postgres_data)
        normalized_data = normalize_population_data(averaged_data)
        return [datum.model_dump() for datum in normalized_data]

    return get_cached_data_with_fallback(
        "game_population_1_week",
        fetch_data,
        POPULATION_1_WEEK_CACHE_TTL,
    )


def get_game_population_totals_1_week() -> list[dict]:
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
        "game_population_totals_1_week",
        fetch_data,
        POPULATION_1_WEEK_CACHE_TTL,
    )


def get_game_population_1_month() -> list[dict]:
    """
    Gets 1 month of game population reported as daily averages.
    Checks cache then database.
    """

    def fetch_data():
        postgres_data = postgres_client.get_game_population_last_month()
        averaged_data = average_daily_data(postgres_data)
        normalized_data = normalize_population_data(averaged_data)
        return [datum.model_dump() for datum in normalized_data]

    return get_cached_data_with_fallback(
        "game_population_1_month",
        fetch_data,
        POPULATION_1_MONTH_CACHE_TTL,
    )


def get_game_population_totals_1_month() -> list[dict]:
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
        "game_population_totals_1_month",
        fetch_data,
        POPULATION_1_MONTH_CACHE_TTL,
    )


def get_game_population_1_year() -> list[dict]:
    """
    Gets 1 year of game population reported as daily averages.
    Checks cache then database.
    """

    def fetch_data():
        postgres_data = postgres_client.get_game_population_last_year()
        averaged_data = average_daily_data(postgres_data)
        normalized_data = normalize_population_data(averaged_data)
        return [datum.model_dump() for datum in normalized_data]

    return get_cached_data_with_fallback(
        "game_population_1_year",
        fetch_data,
        POPULATION_1_YEAR_CACHE_TTL,
    )


def get_game_population_totals_1_year() -> list[dict]:
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
        "game_population_totals_1_year",
        fetch_data,
        POPULATION_1_YEAR_CACHE_TTL,
    )


def get_unique_character_and_guild_count_breakdown_1_month() -> dict:
    """
    Gets a unique character and guild count breakdown for the last month.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "unique_character_and_guild_count_breakdown_1_month",
        lambda: postgres_client.get_unique_character_and_guild_count(30),
        POPULATION_1_QUARTER_CACHE_TTL,
    )


def get_unique_character_and_guild_count_breakdown_1_quarter() -> dict:
    """
    Gets a unique character and guild count breakdown for the last quarter.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "unique_character_and_guild_count_breakdown_1_quarter",
        lambda: postgres_client.get_unique_character_and_guild_count(90),
        POPULATION_1_QUARTER_CACHE_TTL,
    )


def get_character_activity_stats_1_quarter() -> dict:
    """
    Gets character activity stats for the last quarter.
    Checks cache then database.
    """
    return get_cached_data_with_fallback(
        "character_activity_stats_1_quarter",
        lambda: postgres_client.get_character_activity_stats(90),
        POPULATION_1_QUARTER_CACHE_TTL,
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
    Normalize the population data to ensure all servers are represented.
    If a server is missing, it will be added with zero counts.
    """
    if len(input_data) == 0:
        return []

    # First pass: collect all values to find min/max for normalization
    per_server_character_counts: dict[str, list[int]] = {}
    per_server_lfm_counts: dict[str, list[int]] = {}
    valid_data_points = []

    for data_point in input_data:
        try:
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
                    if server_name not in per_server_character_counts:
                        per_server_character_counts[server_name] = []
                    if server_name not in per_server_lfm_counts:
                        per_server_lfm_counts[server_name] = []
                    per_server_character_counts[server_name].append(character_count)
                    per_server_lfm_counts[server_name].append(lfm_count)
                except (ValueError, TypeError, AttributeError):
                    continue

            if valid_server_data:
                valid_data_points.append(
                    {
                        "timestamp": data_point.timestamp,
                        "data": valid_server_data,
                    }
                )
        except (AttributeError, TypeError):
            continue

    if not per_server_character_counts or not per_server_lfm_counts:
        return []

    # Find min/max for normalization
    per_server_min_character_count = {
        server_name: min(counts)
        for server_name, counts in per_server_character_counts.items()
    }
    per_server_max_character_count = {
        server_name: max(counts)
        for server_name, counts in per_server_character_counts.items()
    }
    per_server_min_lfm_count = {
        server_name: min(counts)
        for server_name, counts in per_server_lfm_counts.items()
    }
    per_server_max_lfm_count = {
        server_name: max(counts)
        for server_name, counts in per_server_lfm_counts.items()
    }

    # Avoid division by zero
    per_server_character_range = {
        server_name: max(
            0.0,
            per_server_max_character_count[server_name]
            - per_server_min_character_count[server_name],
        )
        for server_name in per_server_character_counts
    }
    per_server_lfm_range = {
        server_name: max(
            0.0,
            per_server_max_lfm_count[server_name]
            - per_server_min_lfm_count[server_name],
        )
        for server_name in per_server_lfm_counts
    }

    # Second pass: normalize values to 0-1 range
    normalized_data: list[PopulationPointInTime] = []
    for data_point in valid_data_points:
        normalized_server_data: PopulationDataPoint = {}
        for server_name, server_data in data_point["data"].items():
            # Normalize to 0-1 range using min-max normalization
            if per_server_character_range[server_name] > 0:
                normalized_character_count = (
                    server_data["character_count"]
                    - per_server_min_character_count[server_name]
                ) / per_server_character_range[server_name]
            else:
                normalized_character_count = 0.0

            if per_server_lfm_range > 0:
                normalized_lfm_count = (
                    server_data["lfm_count"] - per_server_min_lfm_count[server_name]
                ) / per_server_lfm_range
            else:
                normalized_lfm_count = 0.0

            normalized_server_data[server_name] = PopulationDataPoint(
                character_count=round(normalized_character_count, 6),
                lfm_count=round(normalized_lfm_count, 6),
            )

        normalized_data.append(
            PopulationPointInTime(
                timestamp=data_point["timestamp"],
                data=normalized_server_data,
            )
        )

    return normalized_data
