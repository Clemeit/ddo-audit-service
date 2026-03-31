"""
Population endpoints.
"""

from sanic import Blueprint
from sanic.request import Request
from sanic.response import json
from sanic_ext import openapi

import utils.population as population_utils

population_blueprint = Blueprint("population", url_prefix="/population", version=1)


@population_blueprint.get("/timeseries/<period>")
@openapi.summary("Get population time-series data")
@openapi.response(
    200, {"application/json": {"description": "Character and LFM counts over time"}}
)
@openapi.response(
    400, description="Invalid period. Supported: day, week, month, quarter, year"
)
async def get_population_timeseries(request: Request, period: str):
    """
    Method: GET

    Route: /timeseries/<period>

    Description: Get the population (character and lfm counts) of the game servers
    for the specified time period. Returns detailed time-series data.

    Supported periods: day, week, month, year
    """
    period = period.lower()

    # Map periods to their corresponding utility functions
    period_functions = {
        "day": population_utils.get_game_population_day,
        "week": population_utils.get_game_population_week,
        "month": population_utils.get_game_population_month,
        "quarter": population_utils.get_game_population_quarter,
        "year": population_utils.get_game_population_year,
    }

    if period not in period_functions:
        return json(
            {
                "message": f"Invalid period '{period}'. Supported periods: {', '.join(period_functions.keys())}"
            },
            status=400,
        )

    try:
        data = period_functions[period]()
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": data})


@population_blueprint.get("/totals/<period>")
@openapi.summary("Get summed population totals")
@openapi.response(
    200,
    {"application/json": {"description": "Summed population totals for the period"}},
)
@openapi.response(
    400, description="Invalid period. Supported: day, week, month, quarter, year"
)
async def get_population_totals(request: Request, period: str):
    """
    Method: GET

    Route: /totals/<period>

    Description: Get the total summed population for the specified time period.

    Supported periods: day, week, month, year
    """
    period = period.lower()

    # Map periods to their corresponding utility functions
    period_functions = {
        "day": population_utils.get_game_population_totals_day,
        "week": population_utils.get_game_population_totals_week,
        "month": population_utils.get_game_population_totals_month,
        "quarter": population_utils.get_game_population_totals_quarter,
        "year": population_utils.get_game_population_totals_year,
    }

    if period not in period_functions:
        return json(
            {
                "message": f"Invalid period '{period}'. Supported periods: {', '.join(period_functions.keys())}"
            },
            status=400,
        )

    try:
        data = period_functions[period]()
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": data})


@population_blueprint.get("/unique/<period>")
@openapi.summary("Get unique character and guild count breakdown")
@openapi.response(
    200,
    {
        "application/json": {
            "description": "Unique character and guild data for the period"
        }
    },
)
@openapi.response(
    400, description="Invalid period. Supported: day, week, month, quarter, year"
)
async def get_unique_breakdown(request: Request, period: str):
    """
    Method: GET

    Route: /unique/<period>

    Description: Get the unique character count breakdown for the specified time period.

    Supported periods: week, month, quarter
    """
    period = period.lower()

    period_functions = {
        "day": population_utils.get_unique_character_and_guild_count_breakdown_day,
        "week": population_utils.get_unique_character_and_guild_count_breakdown_week,
        "month": population_utils.get_unique_character_and_guild_count_breakdown_month,
        "quarter": population_utils.get_unique_character_and_guild_count_breakdown_quarter,
        "year": population_utils.get_unique_character_and_guild_count_breakdown_year,
    }

    if period not in period_functions:
        return json(
            {
                "message": f"Invalid period '{period}'. Supported periods: {', '.join(period_functions.keys())}"
            },
            status=400,
        )

    try:
        data = period_functions[period]()
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": data})


@population_blueprint.get("/stats/<period>")
@openapi.summary("Get character activity stats")
@openapi.response(
    200,
    {"application/json": {"description": "Character activity stats for the period"}},
)
@openapi.response(400, description="Invalid period. Supported: quarter")
async def get_stats_breakdown(request: Request, period: str):
    """
    Method: GET

    Route: /stats/<period>

    Description: Get the character activity stats for the specified time period.

    Supported periods: quarter
    """
    period = period.lower()

    period_functions = {
        "quarter": population_utils.get_character_activity_stats_quarter,
    }

    if period not in period_functions:
        return json(
            {
                "message": f"Invalid period '{period}'. Supported periods: {', '.join(period_functions.keys())}"
            },
            status=400,
        )

    try:
        data = period_functions[period]()
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": data})


@population_blueprint.get("/average/<period>")
async def get_average_population(request: Request, period: str):
    """
    Method: GET

    Route: /average/<period>

    Description: Get the average server population for the specified time period.

    Supported periods: day, week, month, quarter, year
    """
    period = period.lower()

    period_functions = {
        "day": population_utils.get_average_server_population_day,
        "week": population_utils.get_average_server_population_week,
        "month": population_utils.get_average_server_population_month,
        "quarter": population_utils.get_average_server_population_quarter,
        "year": population_utils.get_average_server_population_year,
    }

    if period not in period_functions:
        return json(
            {
                "message": f"Invalid period '{period}'. Supported periods: {', '.join(period_functions.keys())}"
            },
            status=400,
        )

    try:
        data = period_functions[period]()
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": data})


@population_blueprint.get("/by-hour/<period>")
async def get_population_by_hour(request: Request, period: str):
    """
    Method: GET

    Route: /by-hour/<period>

    Description: Get the average server population per hour for the specified time period.

    Supported periods: day, week, month, quarter, year
    """
    period = period.lower()

    period_functions = {
        "day": population_utils.get_hourly_server_population_day,
        "week": population_utils.get_hourly_server_population_week,
        "month": population_utils.get_hourly_server_population_month,
        "quarter": population_utils.get_hourly_server_population_quarter,
        "year": population_utils.get_hourly_server_population_year,
    }

    if period not in period_functions:
        return json(
            {
                "message": f"Invalid period '{period}'. Supported periods: {', '.join(period_functions.keys())}"
            },
            status=400,
        )

    try:
        data = period_functions[period]()
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": data})


@population_blueprint.get("/by-day-of-week/<period>")
async def get_population_by_day_of_week(request: Request, period: str):
    """
    Method: GET

    Route: /by-day-of-week/<period>

    Description: Get the average server population per day of the week for the specified time period.

    Supported periods: day, week, month, quarter, year
    """
    period = period.lower()

    period_functions = {
        "day": population_utils.get_daily_server_population_day,
        "week": population_utils.get_daily_server_population_week,
        "month": population_utils.get_daily_server_population_month,
        "quarter": population_utils.get_daily_server_population_quarter,
        "year": population_utils.get_daily_server_population_year,
    }

    if period not in period_functions:
        return json(
            {
                "message": f"Invalid period '{period}'. Supported periods: {', '.join(period_functions.keys())}"
            },
            status=400,
        )

    try:
        data = period_functions[period]()
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": data})


@population_blueprint.get("/by-hour-and-day-of-week/<period>")
async def get_population_by_hour_and_day_of_week(request: Request, period: str):
    """
    Method: GET

    Route: /by-hour-and-day-of-week/<period>

    Description: Get the average server population per hour and by day of week for the specified time period.

    Supported periods: week, month, quarter, year
    """
    period = period.lower()

    period_functions = {
        "week": population_utils.get_by_hour_and_day_of_week_server_population_week,
        "month": population_utils.get_by_hour_and_day_of_week_server_population_month,
        "quarter": population_utils.get_by_hour_and_day_of_week_server_population_quarter,
        "year": population_utils.get_by_hour_and_day_of_week_server_population_year,
    }

    if period not in period_functions:
        return json(
            {
                "message": f"Invalid period '{period}'. Supported periods: {', '.join(period_functions.keys())}"
            },
            status=400,
        )

    try:
        data = period_functions[period]()
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": data})
