"""
Demographics endpoints.
"""

from sanic import Blueprint
from sanic.request import Request
from sanic.response import json

import utils.demographics as demographics_utils
from utils.demographics import ReportLookback

demographics_blueprint = Blueprint(
    "demographics", url_prefix="/demographics", version=1
)


@demographics_blueprint.get("/race/<period>")
async def get_population_race(request: Request, period: str):
    """
    Method: GET

    Route: /race/<period>

    Description: Get the race demographics for the specified time period.

    Supported periods: day, week, month, quarter, year
    """
    period = period.lower()

    period_functions = {
        "day": lambda: demographics_utils.get_race_distribution(ReportLookback.day),
        "week": lambda: demographics_utils.get_race_distribution(ReportLookback.week),
        "month": lambda: demographics_utils.get_race_distribution(ReportLookback.month),
        "quarter": lambda: demographics_utils.get_race_distribution(
            ReportLookback.quarter
        ),
        "year": lambda: demographics_utils.get_race_distribution(ReportLookback.year),
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


@demographics_blueprint.get("/gender/<period>")
async def get_population_gender(request: Request, period: str):
    """
    Method: GET

    Route: /gender/<period>

    Description: Get the gender demographics for the specified time period.

    Supported periods: day, week, month, quarter, year
    """
    period = period.lower()

    period_functions = {
        "day": lambda: demographics_utils.get_gender_distribution(ReportLookback.day),
        "week": lambda: demographics_utils.get_gender_distribution(ReportLookback.week),
        "month": lambda: demographics_utils.get_gender_distribution(
            ReportLookback.month
        ),
        "quarter": lambda: demographics_utils.get_gender_distribution(
            ReportLookback.quarter
        ),
        "year": lambda: demographics_utils.get_gender_distribution(ReportLookback.year),
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


@demographics_blueprint.get("/total-level/<period>")
async def get_population_total_level(request: Request, period: str):
    """
    Method: GET

    Route: /total-level/<period>

    Description: Get the total level demographics for the specified time period.

    Supported periods: day, week, month, quarter, year
    """
    period = period.lower()
    activity_level = request.args.get("activity_level", "all").lower()
    if activity_level not in ("all", "active", "inactive"):
        return json(
            {
                "message": f"Invalid activity_level '{activity_level}'. Supported values: all, active, inactive"
            },
            status=400,
        )

    period_functions = {
        "day": lambda: demographics_utils.get_total_level_distribution(
            ReportLookback.day, activity_level
        ),
        "week": lambda: demographics_utils.get_total_level_distribution(
            ReportLookback.week, activity_level
        ),
        "month": lambda: demographics_utils.get_total_level_distribution(
            ReportLookback.month, activity_level
        ),
        "quarter": lambda: demographics_utils.get_total_level_distribution(
            ReportLookback.quarter, activity_level
        ),
        "year": lambda: demographics_utils.get_total_level_distribution(
            ReportLookback.year, activity_level
        ),
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


@demographics_blueprint.get("/class-count/<period>")
async def get_population_class_count(request: Request, period: str):
    """
    Method: GET

    Route: /class-count/<period>

    Description: Get the class count demographics for the specified time period.

    Supported periods: day, week, month, quarter, year
    """
    period = period.lower()

    period_functions = {
        "day": lambda: demographics_utils.get_class_count_distribution(
            ReportLookback.day
        ),
        "week": lambda: demographics_utils.get_class_count_distribution(
            ReportLookback.week
        ),
        "month": lambda: demographics_utils.get_class_count_distribution(
            ReportLookback.month
        ),
        "quarter": lambda: demographics_utils.get_class_count_distribution(
            ReportLookback.quarter
        ),
        "year": lambda: demographics_utils.get_class_count_distribution(
            ReportLookback.year
        ),
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


@demographics_blueprint.get("/primary-class/<period>")
async def get_population_primary_class(request: Request, period: str):
    """
    Method: GET

    Route: /primary-class/<period>

    Description: Get the primary class demographics for the specified time period.

    Supported periods: day, week, month, quarter, year
    """
    period = period.lower()

    period_functions = {
        "day": lambda: demographics_utils.get_primary_class_distribution(
            ReportLookback.day
        ),
        "week": lambda: demographics_utils.get_primary_class_distribution(
            ReportLookback.week
        ),
        "month": lambda: demographics_utils.get_primary_class_distribution(
            ReportLookback.month
        ),
        "quarter": lambda: demographics_utils.get_primary_class_distribution(
            ReportLookback.quarter
        ),
        "year": lambda: demographics_utils.get_primary_class_distribution(
            ReportLookback.year
        ),
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


@demographics_blueprint.get("/guild-affiliated/<period>")
async def get_guild_affiliation_demographics(request: Request, period: str):
    """
    Method: GET

    Route: /guild-affiliated/<period>

    Description: Get the number of characters in a guild and not in a guild for the specified time period.

    Supported periods: day, week, month, quarter, year
    """
    period = period.lower()

    period_functions = {
        "day": lambda: demographics_utils.get_guild_affiliation_distribution(
            ReportLookback.day
        ),
        "week": lambda: demographics_utils.get_guild_affiliation_distribution(
            ReportLookback.week
        ),
        "month": lambda: demographics_utils.get_guild_affiliation_distribution(
            ReportLookback.month
        ),
        "quarter": lambda: demographics_utils.get_guild_affiliation_distribution(
            ReportLookback.quarter
        ),
        "year": lambda: demographics_utils.get_guild_affiliation_distribution(
            ReportLookback.year
        ),
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
