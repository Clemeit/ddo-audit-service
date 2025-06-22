"""
Activity endpoints.
"""

from datetime import datetime, timedelta

import services.postgres as postgres_client
from constants.activity import CharacterActivityType
from models.character import QuestTimer

from sanic import Blueprint
from sanic.response import json
from sanic.request import Request

activity_blueprint = Blueprint("activity", url_prefix="/activity", version=1)


class AuthorizationError(Exception):
    pass


class VerificationError(Exception):
    pass


@activity_blueprint.get("/<character_id:int>/location")
async def get_location_activity_by_character_id(request: Request, character_id: int):
    """
    Method: GET

    Route: /activity/<character_id:int>/location

    Description: Get location activity by character ID.
    """
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    limit = request.args.get("limit")

    try:
        # convert dates to datetime objects
        if start_date:
            start_date = datetime.strptime(start_date, "%Y-%m-%d")
        if end_date:
            end_date = datetime.strptime(end_date, "%Y-%m-%d")
        limit = int(limit) if limit else None

        verify_authorization(request, character_id)
        activity = postgres_client.get_character_activity_by_type_and_character_id(
            character_id, CharacterActivityType.LOCATION, start_date, end_date, limit
        )
    except AuthorizationError as e:
        return json({"message": str(e)}, status=401)
    except VerificationError as e:
        return json({"message": str(e)}, status=403)
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"data": activity})


@activity_blueprint.get("/<character_id:int>/level")
async def get_level_activity_by_character_id(request, character_id: str):
    """
    Method: GET

    Route: /activity/<character_id:int>/level

    Description: Get level activity by character ID.
    """
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    limit = request.args.get("limit")

    try:
        # convert dates to datetime objects
        if start_date:
            start_date = datetime.strptime(start_date, "%Y-%m-%d")
        if end_date:
            end_date = datetime.strptime(end_date, "%Y-%m-%d")
        limit = int(limit) if limit else None

        verify_authorization(request, character_id)
        activity = postgres_client.get_character_activity_by_type_and_character_id(
            character_id, CharacterActivityType.TOTAL_LEVEL, start_date, end_date
        )
    except AuthorizationError as e:
        return json({"message": str(e)}, status=401)
    except VerificationError as e:
        return json({"message": str(e)}, status=403)
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"data": activity})


@activity_blueprint.get("/<character_id:int>/guild_name")
async def get_guild_name_activity_by_character_id(request, character_id: str):
    """
    Method: GET

    Route: /activity/<character_id:int>/guild_name

    Description: Get guild name activity by character ID.
    """
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    limit = request.args.get("limit")

    try:
        # convert dates to datetime objects
        if start_date:
            start_date = datetime.strptime(start_date, "%Y-%m-%d")
        if end_date:
            end_date = datetime.strptime(end_date, "%Y-%m-%d")
        limit = int(limit) if limit else None

        verify_authorization(request, character_id)
        activity = postgres_client.get_character_activity_by_type_and_character_id(
            character_id, CharacterActivityType.GUILD_NAME, start_date, end_date
        )
    except AuthorizationError as e:
        return json({"message": str(e)}, status=401)
    except VerificationError as e:
        return json({"message": str(e)}, status=403)
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"data": activity})


@activity_blueprint.get("/<character_id:int>/character_name")
async def get_character_name_activity_by_character_id(request, character_id: str):
    """
    Method: GET

    Route: /activity/<character_id:int>/character_name

    Description: Get character name activity by character ID.
    """

    return json({"message": "not implemented"}, status=501)


@activity_blueprint.get("/<character_id:int>/status")
async def get_status_activity_by_character_id(request, character_id: str):
    """
    Method: GET

    Route: /activity/<character_id:int>/status

    Description: Get status activity (online or offline) by character ID.
    """
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    limit = request.args.get("limit")

    try:
        # convert dates to datetime objects
        if start_date:
            start_date = datetime.strptime(start_date, "%Y-%m-%d")
        if end_date:
            end_date = datetime.strptime(end_date, "%Y-%m-%d")
        limit = int(limit) if limit else None

        verify_authorization(request, character_id)
        activity = postgres_client.get_character_activity_by_type_and_character_id(
            character_id, CharacterActivityType.STATUS, start_date, end_date
        )
    except AuthorizationError as e:
        return json({"message": str(e)}, status=401)
    except VerificationError as e:
        return json({"message": str(e)}, status=403)
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"data": activity})


@activity_blueprint.get("/<character_id:int>/quests")
async def get_quest_quests_by_character_id(request, character_id: str):
    """
    Method: GET

    Route: /activity/<character_id:int>/quests

    Description: Get quest quests by character ID.
    """

    try:
        verify_authorization(request, character_id)
        quest_activity = postgres_client.get_recent_quest_activity_by_character_id(
            character_id
        )
    except AuthorizationError as e:
        return json({"message": str(e)}, status=401)
    except VerificationError as e:
        return json({"message": str(e)}, status=403)
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"data": quest_activity})


def verify_authorization(request: Request, character_id: int):
    """
    Verify if the request is authorized.
    """
    pass  # TODO: remove
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise AuthorizationError("Authorization required")
    access_token = postgres_client.get_access_token_by_character_id(character_id)
    if not access_token:
        raise VerificationError("This character has not been verified")
    if auth_header != access_token:
        raise AuthorizationError("Invalid access token")
