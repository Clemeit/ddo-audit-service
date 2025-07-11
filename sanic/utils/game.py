import services.redis as redis_client
import services.postgres as postgres_client
from models.game import PopulationPointInTime, PopulationDataPoint

from functools import reduce

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
        and time() - cached_data.get("timestamp") < POPULATION_1_DAY_CACHE_TTL
    ):
        return cached_data.get("data")

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
        and time() - cached_data.get("timestamp") < POPULATION_1_WEEK_CACHE_TTL
    ):
        return cached_data.get("data")

    postgres_data = postgres_client.get_game_population_last_week()

    averaged_data: list[PopulationPointInTime] = []
    points_in_last_hour: list[PopulationPointInTime] = []
    last_checked_hour = -1
    # for each data point
    for data_point in postgres_data:
        # get the hour
        date_and_time = datetime.fromisoformat(data_point.timestamp)
        hour = date_and_time.hour
        # if the hour is different from the last checked hour
        if hour != last_checked_hour:
            # calculate the average over the last hour and add to array
            first_datetime = datetime.fromisoformat(
                points_in_last_hour[0].timestamp
            ).replace(minute=0, second=0, microsecond=0)
            local_summed_data = reduce(sum_server_population, points_in_last_hour)

            # clear data
            points_in_last_hour = []
            last_checked_hour = hour
        else:
            points_in_last_hour.append(data_point)

    data_dump = [datum.model_dump() for datum in averaged_data]
    redis_client.set_game_population_1_day(data_dump)
    return data_dump
