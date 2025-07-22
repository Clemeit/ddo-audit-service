"""
LFM endpoints.
"""

import services.redis as redis_client
from models.api import LfmRequestApiModel, LfmRequestType
from utils.validation import is_server_name_valid

from sanic import Blueprint
from sanic.request import Request
from sanic.response import json
from business.lfms import handle_incoming_lfms

from utils.log import logMessage

lfm_blueprint = Blueprint("lfm", url_prefix="/lfms", version=1)


# ===== Client-facing endpoints =====
@lfm_blueprint.get("")
async def get_all_lfms(request: Request):
    """
    Method: GET

    Route: /lfms

    Description: Get all LFM posts from all servers from the Redis cache.
    """
    try:
        return json({"data": redis_client.get_all_lfms_as_dict()})
    except Exception as e:
        return json({"message": str(e)}, status=500)


@lfm_blueprint.get("/summary")
async def get_lfm_summary(request: Request):
    """
    Method: GET

    Route: /lfms/summary

    Description: Get the number of LFMs for each server from the Redis cache.
    """
    try:
        return json({"data": redis_client.get_all_lfm_counts()})
    except Exception as e:
        return json({"message": str(e)}, status=500)


@lfm_blueprint.get("/<server_name:str>")
async def get_lfms_by_server(request: Request, server_name: str):
    """
    Method: GET

    Route: /lfms/<server_name:str>

    Description: Get all LFM posts from a specific server from the Redis cache.
    """
    if not is_server_name_valid(server_name):
        return json({"message": "Invalid server name"}, status=400)

    try:
        return json({"data": redis_client.get_lfms_by_server_name_as_dict(server_name)})
    except Exception as e:
        return json({"message": str(e)}, status=500)


# ===================================


# ======= Internal endpoints ========
@lfm_blueprint.post("")
async def set_lfms(request: Request):
    """
    Method: POST

    Route: /lfms

    Description: Set LFM posts in the Redis cache. Should only be called by DDO Audit Collections. Keyframes.
    """
    # validate request body
    try:
        request_body = LfmRequestApiModel(**request.json)
    except Exception:
        return json({"message": "Invalid request body"}, status=400)

    # update in redis cache
    try:
        handle_incoming_lfms(request_body, LfmRequestType.set)
    except Exception as e:
        logMessage(
            message="Error handling incoming LFMs",
            level="error",
            action="set_lfms",
            metadata={
                "error": str(e),
            },
        )
        print(f"Error handling incoming LFMs: {e}")
        return json({"message": str(e)}, status=500)

    return json({"message": "success"})


@lfm_blueprint.patch("")
async def update_lfms(request: Request):
    """
    Method: PATCH

    Route: /lfms

    Description: Update LFM posts in the Redis cache. Should only be called by DDO Audit Collections. Delta updates.
    """
    # validate request body
    try:
        request_body = LfmRequestApiModel(**request.json)
    except Exception:
        return json({"message": "Invalid request body"}, status=400)

    # update in redis cache
    try:
        handle_incoming_lfms(request_body, LfmRequestType.update)
    except Exception as e:
        logMessage(
            message="Error handling incoming LFMs",
            level="error",
            action="update_lfms",
            metadata={
                "error": str(e),
                "request_body": request_body.model_dump() if request_body else None,
            },
        )
        print(f"Error handling incoming LFMs: {e}")
        return json({"message": str(e)}, status=500)

    return json({"message": "success"})


# ===================================
