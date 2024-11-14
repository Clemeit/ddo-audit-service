"""
Game endpoints.
"""

import services.redis as redis_client
import services.postgres as postgres_client
from models.redis import GameInfo

from sanic import Blueprint
from sanic.request import Request
from sanic.response import json

game_blueprint = Blueprint("game", url_prefix="/game", version=1)


# ===== Client-facing endpoints =====
@game_blueprint.get("/info")
async def get_game_info(request):
    """
    Method: GET

    Route: /game/info

    Description: Get's the latest game info from the Redis cache.
    """
    # update in redis cache
    try:
        game_info = redis_client.get_game_info().model_dump()
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": game_info})


@game_blueprint.get("/population")
async def get_game_stats(request: Request):
    """
    Method: GET

    Route: /game/population

    Description: Get the population (character and lfm counts) of the game servers.
    Can be filtered by date range. If no date range is provided, the data is for the last 24 hours.
    """
    # get query parameters
    start_date = request.args.get("start_date", None)
    end_date = request.args.get("end_date", None)

    # get data from redis cache
    try:
        data = postgres_client.get_game_population(start_date, end_date)
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": data})


# ===================================


# ======= Internal endpoints ========
@game_blueprint.patch("/info")
async def patch_game_info(request: Request):
    """
    Method: PATCH

    Route: /game/info

    Description: Set game info such as last_data_fetch, character_count, and lfm_count.
    This data is merged with existing info coming from the server_status report.
    """
    # validate request body
    try:
        body = GameInfo(**request.json)
    except Exception:
        return json({"message": "Invalid request body"}, status=400)

    # update in redis cache
    try:
        redis_client.set_game_info(body)
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"message": "success"})


# ===================================
