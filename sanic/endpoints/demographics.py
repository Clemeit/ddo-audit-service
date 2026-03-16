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
    activity_level = request.args.get("activity_level", "all").lower()
    if not validate_activity_level(activity_level):
        return json(
            {
                "message": f"Invalid activity_level '{activity_level}'. Supported values: all, active, inactive"
            },
            status=400,
        )

    period_map = {
        "day": ReportLookback.day,
        "week": ReportLookback.week,
        "month": ReportLookback.month,
        "quarter": ReportLookback.quarter,
        "year": ReportLookback.year,
    }

    if period not in period_map:
        return json(
            {
                "message": f"Invalid period '{period}'. Supported periods: {', '.join(period_map.keys())}"
            },
            status=400,
        )

    try:
        data = await demographics_utils.get_race_distribution(
            period_map[period], activity_level
        )
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
    activity_level = request.args.get("activity_level", "all").lower()
    if not validate_activity_level(activity_level):
        return json(
            {
                "message": f"Invalid activity_level '{activity_level}'. Supported values: all, active, inactive"
            },
            status=400,
        )

    period_map = {
        "day": ReportLookback.day,
        "week": ReportLookback.week,
        "month": ReportLookback.month,
        "quarter": ReportLookback.quarter,
        "year": ReportLookback.year,
    }

    if period not in period_map:
        return json(
            {
                "message": f"Invalid period '{period}'. Supported periods: {', '.join(period_map.keys())}"
            },
            status=400,
        )

    try:
        data = await demographics_utils.get_gender_distribution(
            period_map[period], activity_level
        )
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
    if not validate_activity_level(activity_level):
        return json(
            {
                "message": f"Invalid activity_level '{activity_level}'. Supported values: all, active, inactive"
            },
            status=400,
        )

    period_map = {
        "day": ReportLookback.day,
        "week": ReportLookback.week,
        "month": ReportLookback.month,
        "quarter": ReportLookback.quarter,
        "year": ReportLookback.year,
    }

    if period not in period_map:
        return json(
            {
                "message": f"Invalid period '{period}'. Supported periods: {', '.join(period_map.keys())}"
            },
            status=400,
        )

    try:
        data = await demographics_utils.get_total_level_distribution(
            period_map[period], activity_level
        )
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
    activity_level = request.args.get("activity_level", "all").lower()
    if not validate_activity_level(activity_level):
        return json(
            {
                "message": f"Invalid activity_level '{activity_level}'. Supported values: all, active, inactive"
            },
            status=400,
        )

    period_map = {
        "day": ReportLookback.day,
        "week": ReportLookback.week,
        "month": ReportLookback.month,
        "quarter": ReportLookback.quarter,
        "year": ReportLookback.year,
    }

    if period not in period_map:
        return json(
            {
                "message": f"Invalid period '{period}'. Supported periods: {', '.join(period_map.keys())}"
            },
            status=400,
        )

    try:
        data = await demographics_utils.get_class_count_distribution(
            period_map[period], activity_level
        )
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
    activity_level = request.args.get("activity_level", "all").lower()
    if not validate_activity_level(activity_level):
        return json(
            {
                "message": f"Invalid activity_level '{activity_level}'. Supported values: all, active, inactive"
            },
            status=400,
        )

    period_map = {
        "day": ReportLookback.day,
        "week": ReportLookback.week,
        "month": ReportLookback.month,
        "quarter": ReportLookback.quarter,
        "year": ReportLookback.year,
    }

    if period not in period_map:
        return json(
            {
                "message": f"Invalid period '{period}'. Supported periods: {', '.join(period_map.keys())}"
            },
            status=400,
        )

    try:
        data = await demographics_utils.get_primary_class_distribution(
            period_map[period], activity_level
        )
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
    activity_level = request.args.get("activity_level", "all").lower()
    if not validate_activity_level(activity_level):
        return json(
            {
                "message": f"Invalid activity_level '{activity_level}'. Supported values: all, active, inactive"
            },
            status=400,
        )

    period_map = {
        "day": ReportLookback.day,
        "week": ReportLookback.week,
        "month": ReportLookback.month,
        "quarter": ReportLookback.quarter,
        "year": ReportLookback.year,
    }

    if period not in period_map:
        return json(
            {
                "message": f"Invalid period '{period}'. Supported periods: {', '.join(period_map.keys())}"
            },
            status=400,
        )

    try:
        data = await demographics_utils.get_guild_affiliation_distribution(
            period_map[period], activity_level
        )
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": data})


def validate_activity_level(activity_level: str) -> str | None:
    activity_level = activity_level.lower()
    if activity_level in ("all", "active", "inactive"):
        return activity_level
    return None
