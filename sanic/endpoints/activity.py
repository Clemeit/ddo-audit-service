"""
Activity endpoints.
"""

from datetime import datetime

import services.postgres as postgres_client
from constants.activity import CharacterActivityType
from constants.server import MAX_REGISTERED_CHARACTER_COUNT

from sanic import Blueprint
from sanic.response import json
from sanic.request import Request

activity_blueprint = Blueprint("activity", url_prefix="/activity", version=1)


class AuthorizationError(Exception):
    pass


class VerificationError(Exception):
    pass


@activity_blueprint.get("/<character_id:int>/<activity_type:str>")
async def get_activity_by_character_id_and_activity_type(
    request: Request, character_id: int, activity_type: str
):
    """
    Method: GET

    Route: /activity/<character_id:int>/<activity_type:str>

    Description: Get activity by character ID and activity type.
    """
    start_date_str = request.args.get("start_date")
    end_date_str = request.args.get("end_date")
    limit_str = request.args.get("limit")

    try:
        verify_authorization(request, character_id)

        # validate activity type
        try:
            activity_type = CharacterActivityType(activity_type)
        except ValueError:
            return json({"message": "Invalid activity type"}, status=400)

        start_date = None
        end_date = None
        if start_date_str:
            try:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
            except ValueError:
                return json({"message": "start_date must be YYYY-MM-DD"}, status=400)
        if end_date_str:
            try:
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d")
            except ValueError:
                return json({"message": "end_date must be YYYY-MM-DD"}, status=400)

        if start_date and end_date and start_date > end_date:
            return json({"message": "start_date cannot be after end_date"}, status=400)

        limit = None
        if limit_str is not None:
            try:
                limit = int(limit_str)
            except ValueError:
                return json({"message": "limit must be an integer"}, status=400)
            if limit <= 0:
                return json({"message": "limit must be greater than 0"}, status=400)
            if limit > 500:
                return json({"message": "limit must be <= 500"}, status=400)

        activity = postgres_client.get_character_activity_by_type_and_character_id(
            character_id, activity_type, start_date, end_date, limit
        )
    except AuthorizationError as e:
        return json({"message": str(e)}, status=401)
    except VerificationError as e:
        return json({"message": str(e)}, status=403)
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"data": activity})


@activity_blueprint.get("/raids/<character_ids:str>")
async def get_recent_raid_activity_by_character_ids(
    request: Request, character_ids: str
):
    """
    Method: GET

    Route: /activity/raids/<character_ids:str>

    Description: Get recent activity by character IDs. No auth needed.
    """
    # validate that all character_ids are numbers
    if not all(character_id.isdigit() for character_id in character_ids.split(",")):
        return json({"message": "Invalid character IDs"}, status=400)

    try:
        character_ids_list = [int(id) for id in character_ids.split(",")]
        if len(character_ids_list) > MAX_REGISTERED_CHARACTER_COUNT:
            return json(
                {
                    "message": f"Cannot request more than {MAX_REGISTERED_CHARACTER_COUNT} character IDs at once"
                },
                status=400,
            )

        raid_activity = postgres_client.get_recent_raid_activity_by_character_ids(
            character_ids_list
        )
        return_data = {item["character_id"]: item for item in raid_activity}
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"data": return_data})


# @activity_blueprint.get("/<character_id:int>/level")
# async def get_level_activity_by_character_id(request, character_id: str):
#     """
#     Method: GET

#     Route: /activity/<character_id:int>/level

#     Description: Get level activity by character ID.
#     """
#     start_date = request.args.get("start_date")
#     end_date = request.args.get("end_date")
#     limit = request.args.get("limit")

#     try:
#         # convert dates to datetime objects
#         if start_date:
#             start_date = datetime.strptime(start_date, "%Y-%m-%d")
#         if end_date:
#             end_date = datetime.strptime(end_date, "%Y-%m-%d")
#         limit = int(limit) if limit else None

#         verify_authorization(request, character_id)
#         activity = postgres_client.get_character_activity_by_type_and_character_id(
#             character_id, CharacterActivityType.TOTAL_LEVEL, start_date, end_date
#         )
#     except AuthorizationError as e:
#         return json({"message": str(e)}, status=401)
#     except VerificationError as e:
#         return json({"message": str(e)}, status=403)
#     except Exception as e:
#         return json({"message": str(e)}, status=500)
#     return json({"data": activity})


# @activity_blueprint.get("/<character_id:int>/guild_name")
# async def get_guild_name_activity_by_character_id(request, character_id: str):
#     """
#     Method: GET

#     Route: /activity/<character_id:int>/guild_name

#     Description: Get guild name activity by character ID.
#     """
#     start_date = request.args.get("start_date")
#     end_date = request.args.get("end_date")
#     limit = request.args.get("limit")

#     try:
#         # convert dates to datetime objects
#         if start_date:
#             start_date = datetime.strptime(start_date, "%Y-%m-%d")
#         if end_date:
#             end_date = datetime.strptime(end_date, "%Y-%m-%d")
#         limit = int(limit) if limit else None

#         verify_authorization(request, character_id)
#         activity = postgres_client.get_character_activity_by_type_and_character_id(
#             character_id, CharacterActivityType.GUILD_NAME, start_date, end_date
#         )
#     except AuthorizationError as e:
#         return json({"message": str(e)}, status=401)
#     except VerificationError as e:
#         return json({"message": str(e)}, status=403)
#     except Exception as e:
#         return json({"message": str(e)}, status=500)
#     return json({"data": activity})


# @activity_blueprint.get("/<character_id:int>/character_name")
# async def get_character_name_activity_by_character_id(request, character_id: str):
#     """
#     Method: GET

#     Route: /activity/<character_id:int>/character_name

#     Description: Get character name activity by character ID.
#     """

#     return json({"message": "not implemented"}, status=501)


# @activity_blueprint.get("/<character_id:int>/status")
# async def get_status_activity_by_character_id(request, character_id: str):
#     """
#     Method: GET

#     Route: /activity/<character_id:int>/status

#     Description: Get status activity (online or offline) by character ID.
#     """
#     start_date = request.args.get("start_date")
#     end_date = request.args.get("end_date")
#     limit = request.args.get("limit")

#     try:
#         # convert dates to datetime objects
#         if start_date:
#             start_date = datetime.strptime(start_date, "%Y-%m-%d")
#         if end_date:
#             end_date = datetime.strptime(end_date, "%Y-%m-%d")
#         limit = int(limit) if limit else None

#         verify_authorization(request, character_id)
#         activity = postgres_client.get_character_activity_by_type_and_character_id(
#             character_id, CharacterActivityType.STATUS, start_date, end_date
#         )
#     except AuthorizationError as e:
#         return json({"message": str(e)}, status=401)
#     except VerificationError as e:
#         return json({"message": str(e)}, status=403)
#     except Exception as e:
#         return json({"message": str(e)}, status=500)
#     return json({"data": activity})


@activity_blueprint.get("/<character_id:int>/quests")
async def get_recent_quests_by_character_id(request, character_id: str):
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
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise AuthorizationError("Authorization required")
    access_token = postgres_client.get_access_token_by_character_id(character_id)
    if not access_token:
        raise VerificationError("This character has not been verified")
    if auth_header != access_token:
        raise AuthorizationError("Invalid access token")
