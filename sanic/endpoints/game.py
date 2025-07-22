"""
Game endpoints.
"""

import services.redis as redis_client
from models.redis import ServerInfo

from sanic import Blueprint
from sanic.request import Request
from sanic.response import json

from utils.validation import is_server_name_valid
from utils.log import logMessage

game_blueprint = Blueprint("game", url_prefix="/game", version=1)


# ===== Client-facing endpoints =====
@game_blueprint.get("/server-info")
async def get_game_info(request):
    """
    Method: GET

    Route: /game/server-info

    Description: Gets the latest game info from the Redis cache.
    """
    # update in redis cache
    try:
        game_info = redis_client.get_server_info_as_dict()
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json(game_info)


@game_blueprint.get("/server-info/<server_name:str>")
async def get_server_info_by_server(request, server_name):
    """
    Method: GET

    Route: /game/server-info/<server_name:str>

    Description: Gets the latest server info for a specific server from the Redis cache.
    """
    if not is_server_name_valid(server_name):
        return json({"message": "Invalid server name"}, status=400)

    # update in redis cache
    try:
        server_info = redis_client.get_server_info_by_server_name_as_dict(server_name)
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json(server_info)


# ===================================


# ======= Internal endpoints ========
@game_blueprint.patch("/server-info")
async def patch_game_info(request: Request):
    """
    Method: PATCH

    Route: /game/server-info

    Description: Set game info such as last_data_fetch, character_count, and lfm_count.
    This data is merged with existing info coming from the server_status report.
    """
    # validate request body
    try:
        request_body = ServerInfo(**request.json)
    except Exception:
        return json({"message": "Invalid request body"}, status=400)

    # update in redis cache
    try:
        redis_client.merge_server_info(request_body)
    except Exception as e:
        logMessage(
            message="Error handling incoming game info",
            level="error",
            action="patch_game_info",
            metadata={
                "error": str(e),
                "request_body": request_body.model_dump() if request_body else None,
            },
        )
        return json({"message": str(e)}, status=500)

    return json({"message": "success"})


# ===================================
