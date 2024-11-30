"""
Activity endpoints.
"""

import services.postgres as postgres_client
from constants.activity import CharacterActivityType

from sanic import Blueprint
from sanic.response import json
from sanic.request import Request

activity_blueprint = Blueprint("activity", url_prefix="/activity", version=1)


class AuthorizationError(Exception):
    pass


class VerificationError(Exception):
    pass


@activity_blueprint.get("/<character_id:int>/location")
async def get_location_activity_by_character_id(request: Request, character_id):
    """
    Method: GET

    Route: /activity/<character_id:int>/location

    Description: Get location activity by character ID.
    """

    try:
        verify_authorization(request, character_id)
        activity = postgres_client.get_character_activity_by_type_and_character_id(
            character_id, CharacterActivityType.location
        )
    except AuthorizationError as e:
        return json({"message": str(e)}, status=401)
    except VerificationError as e:
        return json({"message": str(e)}, status=403)
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"data": activity})


@activity_blueprint.get("/<character_id:int>/total_level")
async def get_level_activity_by_character_id(request, character_id):
    """
    Method: GET

    Route: /activity/<character_id:int>/level

    Description: Get level activity by character ID.
    """

    try:
        verify_authorization(request, character_id)
        activity = postgres_client.get_character_activity_by_type_and_character_id(
            character_id, CharacterActivityType.total_level
        )
    except AuthorizationError as e:
        return json({"message": str(e)}, status=401)
    except VerificationError as e:
        return json({"message": str(e)}, status=403)
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"data": activity})


@activity_blueprint.get("/<character_id:int>/guild_name")
async def get_guild_name_activity_by_character_id(request, character_id):
    """
    Method: GET

    Route: /activity/<character_id:int>/guild_name

    Description: Get guild name activity by character ID.
    """

    try:
        verify_authorization(request, character_id)
        activity = postgres_client.get_character_activity_by_type_and_character_id(
            character_id, CharacterActivityType.guild_name
        )
    except AuthorizationError as e:
        return json({"message": str(e)}, status=401)
    except VerificationError as e:
        return json({"message": str(e)}, status=403)
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"data": activity})


@activity_blueprint.get("/<character_id:int>/character_name")
async def get_character_name_activity_by_character_id(request, character_id):
    """
    Method: GET

    Route: /activity/<character_id:int>/character_name

    Description: Get character name activity by character ID.
    """

    return json({"message": "not implemented"}, status=501)


@activity_blueprint.get("/<character_id:int>/status")
async def get_status_activity_by_character_id(request, character_id):
    """
    Method: GET

    Route: /activity/<character_id:int>/status

    Description: Get status activity (online or offline) by character ID.
    """
    try:
        verify_authorization(request, character_id)
        activity = postgres_client.get_character_activity_by_type_and_character_id(
            character_id, CharacterActivityType.status
        )
    except AuthorizationError as e:
        return json({"message": str(e)}, status=401)
    except VerificationError as e:
        return json({"message": str(e)}, status=403)
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"data": activity})


def verify_authorization(request: Request, character_id: int):
    """
    Verify if the request is authorized.
    """

    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise AuthorizationError("Authorization required")
    access_token = postgres_client.get_access_token_by_character_id(character_id)
    if not access_token:
        raise VerificationError("This character has not been verified")
    if auth_header != access_token:
        raise AuthorizationError("Invalid access token")
