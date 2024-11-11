"""
Activity endpoints.
"""

from sanic import Blueprint
from sanic.response import json

activity_blueprint = Blueprint("activity", url_prefix="/activity", version=1)


@activity_blueprint.get("/<character_id:int>/location")
async def get_location_activity_by_character_id(request, character_id):
    """
    Method: GET

    Route: /activity/<character_id:int>/location

    Description: Get location activity by character ID.
    """
    return json({"location_activity": []})


@activity_blueprint.get("/<character_id:int>/level")
async def get_level_activity_by_character_id(request, character_id):
    """
    Method: GET

    Route: /activity/<character_id:int>/level

    Description: Get level activity by character ID.
    """
    return json({"level_activity": []})


@activity_blueprint.get("/<character_id:int>/guild_name")
async def get_guild_name_activity_by_character_id(request, character_id):
    """
    Method: GET

    Route: /activity/<character_id:int>/guild_name

    Description: Get guild name activity by character ID.
    """
    return json({"guild_name_activity": []})


@activity_blueprint.get("/<character_id:int>/character_name")
async def get_character_name_activity_by_character_id(request, character_id):
    """
    Method: GET

    Route: /activity/<character_id:int>/character_name

    Description: Get character name activity by character ID.
    """
    return json({"character_name_activity": []})


@activity_blueprint.get("/<character_id:int>/status")
async def get_status_activity_by_character_id(request, character_id):
    """
    Method: GET

    Route: /activity/<character_id:int>/status

    Description: Get status activity (online or offline) by character ID.
    """
    return json({"status_activity": []})
