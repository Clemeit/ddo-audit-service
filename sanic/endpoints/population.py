"""
Population endpoints.
"""

from sanic import Blueprint
from sanic.request import Request
from sanic.response import json

import utils.population as population_utils

population_blueprint = Blueprint("population", url_prefix="/population", version=1)


@population_blueprint.get("/timeseries/<period>")
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
        "day": population_utils.get_game_population_1_day,
        "week": population_utils.get_game_population_1_week,
        "month": population_utils.get_game_population_1_month,
        "year": population_utils.get_game_population_1_year,
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
        "day": population_utils.get_game_population_totals_1_day,
        "week": population_utils.get_game_population_totals_1_week,
        "month": population_utils.get_game_population_totals_1_month,
        "year": population_utils.get_game_population_totals_1_year,
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
async def get_unique_breakdown(request: Request, period: str):
    """
    Method: GET

    Route: /unique/<period>

    Description: Get the unique character count breakdown for the specified time period.

    Supported periods: month, quarter
    """
    period = period.lower()

    # Map periods to their corresponding utility functions
    period_functions = {
        "month": population_utils.get_unique_character_and_guild_count_breakdown_1_month,
        "quarter": population_utils.get_unique_character_and_guild_count_breakdown_1_quarter,
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
async def get_stats_breakdown(request: Request, period: str):
    """
    Method: GET

    Route: /stats/<period>

    Description: Get the character activity stats for the specified time period.

    Supported periods: quarter
    """
    period = period.lower()

    # Map periods to their corresponding utility functions
    period_functions = {
        "quarter": population_utils.get_character_activity_stats_1_quarter,
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
