"""
Demographics endpoints.
"""

from sanic import Blueprint
from sanic.request import Request
from sanic.response import json

import utils.demographics as demographics_utils

demographics_blueprint = Blueprint(
    "demographics", url_prefix="/demographics", version=1
)


@demographics_blueprint.get("/race/<period>")
async def get_population_race(request: Request, period: str):
    """
    Method: GET

    Route: /race/<period>

    Description: Get the race demographics for the specified time period.

    Supported periods: week, quarter
    """
    period = period.lower()

    # Map periods to their corresponding utility functions
    period_functions = {
        "week": demographics_utils.get_race_demographics_week,
        "quarter": demographics_utils.get_race_demographics_week,
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

    Supported periods: week, quarter
    """
    period = period.lower()

    # Map periods to their corresponding utility functions
    period_functions = {
        "week": demographics_utils.get_gender_demographics_week,
        "quarter": demographics_utils.get_gender_demographics_week,
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

    Supported periods: week, quarter
    """
    period = period.lower()

    # Map periods to their corresponding utility functions
    period_functions = {
        "week": demographics_utils.get_total_level_demographics_week,
        "quarter": demographics_utils.get_total_level_demographics_week,
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

    Supported periods: week, quarter
    """
    period = period.lower()

    # Map periods to their corresponding utility functions
    period_functions = {
        "week": demographics_utils.get_class_count_demographics_week,
        "quarter": demographics_utils.get_class_count_demographics_week,
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
