import services.redis as redis_client
import services.postgres as postgres_client
from models.game import PopulationPointInTime, PopulationDataPoint
from utils.time import datetime_to_datetime_string

from constants.redis import (
    POPULATION_1_DAY_CACHE_TTL,
    POPULATION_1_WEEK_CACHE_TTL,
    POPULATION_1_MONTH_CACHE_TTL,
)

from time import time
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
    cached_data = redis_client.get_game_population_1_day()
    if (
        cached_data
        and cached_data.get("timestamp")
        and time() - cached_data.get("timestamp") < POPULATION_1_DAY_CACHE_TTL
    ):
        return cached_data.get("data", [])

    postgres_data = postgres_client.get_game_population_relative(1)
    data_dump = [datum.model_dump() for datum in postgres_data]
    redis_client.set_game_population_1_day(data_dump)
    return data_dump


def get_game_population_1_week() -> list[dict]:
    """
    Gets 1 week of game population reported as hourly averages.
    Checks cache then database.
    """
    cached_data = redis_client.get_game_population_1_week()
    if (
        cached_data
        and cached_data.get("timestamp")
        and time() - cached_data.get("timestamp") < POPULATION_1_WEEK_CACHE_TTL
    ):
        return cached_data.get("data", [])

    postgres_data = postgres_client.get_game_population_last_week()
    averaged_data = average_hourly_data(postgres_data)
    data_dump = [datum.model_dump() for datum in averaged_data]
    redis_client.set_game_population_1_week(data_dump)
    return data_dump


def get_game_population_1_month() -> list[dict]:
    """
    Gets 1 month of game population reported as daily averages.
    Checks cache then database.
    """
    cached_data = redis_client.get_game_population_1_month()
    if (
        cached_data
        and cached_data.get("timestamp")
        and time() - cached_data.get("timestamp") < POPULATION_1_MONTH_CACHE_TTL
    ):
        return cached_data.get("data", [])

    postgres_data = postgres_client.get_game_population_last_month()
    averaged_data = average_daily_data(postgres_data)
    data_dump = [datum.model_dump() for datum in averaged_data]
    redis_client.set_game_population_1_month(data_dump)
    return data_dump


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


def averaged_population_data_points(
    input_data: list[PopulationPointInTime],
) -> dict[str, PopulationDataPoint]:
    if len(input_data) == 0:
        return []

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
