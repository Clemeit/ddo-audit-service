"""
LFM endpoints.
"""

import time

from constants.server import SERVER_NAMES_LOWERCASE
from models.api import LfmRequestApiModel, ServerLfmDataApiModel
from models.redis import ServerLFMsData
import services.redis as redis_client
from utils.server import is_server_name_valid
from models.lfm import LfmActivityEvent, LfmActivity, LfmActivityType, Lfm

from sanic import Blueprint
from sanic.request import Request
from sanic.response import json

lfm_blueprint = Blueprint("lfm", url_prefix="/lfms", version=1)


# ===== Client-facing endpoints =====
@lfm_blueprint.get("")
async def get_all_lfms(request):
    """
    Method: GET

    Route: /lfms

    Description: Get all LFM posts from all servers from the Redis cache.
    """
    try:
        response = {}
        for server_name in SERVER_NAMES_LOWERCASE:
            response[server_name] = redis_client.get_lfms_by_server_name(
                server_name
            ).model_dump()
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": response})


@lfm_blueprint.get("/summary")
async def get_online_characters(request):
    """
    Method: GET

    Route: /lfms/summary

    Description: Get the number of LFMs for each server from the Redis cache.
    """
    try:
        response = {}
        for server_name in SERVER_NAMES_LOWERCASE:
            server_data = redis_client.get_lfms_by_server_name(server_name)
            raid_count = sum(
                1
                for lfm in server_data.lfms.values()
                if lfm.quest and lfm.quest.group_size == "Raid"
            )
            response[server_name] = {
                "lfm_count": len(server_data.lfms),
                "raid_count": raid_count,
            }
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"data": response})


@lfm_blueprint.get("/<server_name:str>")
async def get_lfms_by_server(request, server_name):
    """
    Method: GET

    Route: /lfms/<server_name:str>

    Description: Get all LFM posts from a specific server from the Redis cache.
    """
    if not is_server_name_valid(server_name):
        return json({"message": "Invalid server name"}, status=400)

    server_lfms = redis_client.get_lfms_by_server_name(server_name)

    return json({"data": server_lfms.model_dump()})


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
        body = LfmRequestApiModel(**request.json)
    except Exception:
        return json({"message": "Invalid request body"}, status=400)

    # update in redis cache
    try:
        for server_name, current_lfms_data in body.model_dump().items():
            server_data = ServerLfmDataApiModel(**current_lfms_data)
            current_lfms_data = ServerLFMsData(
                lfms={lfm.id: lfm for lfm in server_data.data},
                last_updated=time.time(),
            )
            previous_lfms_data = redis_client.get_lfms_by_server_name(server_name)

            lfms_with_activity = add_activity_to_lfms_for_server(
                previous_lfms_data.lfms, current_lfms_data.lfms
            )
            current_lfms_data.lfms = lfms_with_activity

            redis_client.set_lfms_by_server_name(server_name, current_lfms_data)
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"message": "success"})


@lfm_blueprint.patch("")
async def update_lfms(request):
    """
    Method: PATCH

    Route: /lfms

    Description: Update LFM posts in the Redis cache. Should only be called by DDO Audit Collections. Delta updates.
    """
    # validate request body
    try:
        body = LfmRequestApiModel(**request.json)
    except Exception:
        return json({"message": "Invalid request body"}, status=400)

    # update in redis cache
    try:
        for server_name, current_lfms_data in body.model_dump(
            exclude_unset=True
        ).items():
            server_data = ServerLfmDataApiModel(**current_lfms_data)
            current_lfms_data = ServerLFMsData(
                lfms={lfm.id: lfm for lfm in server_data.data},
                last_updated=time.time(),
            )
            redis_client.update_lfms_by_server_name(server_name, current_lfms_data)
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json({"message": "success"})


def add_activity_to_lfms_for_server(
    previous_lfms: dict[str, Lfm], current_lfms: dict[str, Lfm]
) -> dict[str, Lfm]:
    previous_lfm_ids = set(previous_lfms.keys())
    current_lfm_ids = set(current_lfms.keys())
    current_lfms_with_activity: dict[str, Lfm] = {}
    for lfm_id in current_lfm_ids:

        previous_lfm = previous_lfms[lfm_id] if lfm_id in previous_lfms else None
        current_lfm = current_lfms[lfm_id]

        old_lfm_activity: list[LfmActivity] = []
        new_activity_events_list: list[LfmActivityEvent] = []

        is_lfm_new = False

        # lfms that were just posted:
        if lfm_id not in previous_lfm_ids:
            new_activity_events_list.append(
                LfmActivityEvent(tag=LfmActivityType.posted)
            )
            is_lfm_new = True  # no need to check for other updates

        if not is_lfm_new:
            # carry over activity from previous lfms data
            if previous_lfms[lfm_id].activity:
                old_lfm_activity = previous_lfms[lfm_id].activity

            # quest updated:
            old_quest_name = ""
            new_quest_name = ""
            if previous_lfm.quest:
                old_quest_name = previous_lfm.quest.name
            if current_lfm.quest:
                new_quest_name = current_lfm.quest.name
            if old_quest_name != new_quest_name:
                new_activity_events_list.append(
                    LfmActivityEvent(tag=LfmActivityType.quest, data=new_quest_name)
                )

            # comment updated:
            if previous_lfm.comment != current_lfm.comment:
                new_activity_events_list.append(
                    LfmActivityEvent(
                        tag=LfmActivityType.comment, data=current_lfm.comment
                    )
                )

            # members left or joined:
            old_member_ids = set([member.id for member in previous_lfm.members])
            new_member_ids = set([member.id for member in current_lfm.members])
            members_left = old_member_ids - new_member_ids
            members_joined = new_member_ids - old_member_ids
            # TODO: nested loops, should be optimized
            for member_id in members_left:
                # get the name of the member that left
                member_name = "Unknown"
                for member in previous_lfm.members:
                    if member.id == member_id:
                        member_name = member.name
                        break
                new_activity_events_list.append(
                    LfmActivityEvent(
                        tag=LfmActivityType.member_left,
                        data=member_name,
                    )
                )
            # TODO: nested loops, should be optimized
            for member_id in members_joined:
                # get the name of the member that left
                member_name = "Unknown"
                for member in current_lfm.members:
                    if member.id == member_id:
                        member_name = member.name
                        break
                new_activity_events_list.append(
                    LfmActivityEvent(
                        tag=LfmActivityType.member_joined,
                        data=member_name,
                    )
                )

        # comine the old and new activity
        new_lfm_activity = LfmActivity(
            timestamp=time.time(), events=new_activity_events_list
        )
        aggregate_activity = old_lfm_activity + (
            [new_lfm_activity] if new_activity_events_list else []
        )
        current_lfms_with_activity[lfm_id] = Lfm(
            **current_lfm.model_dump(exclude={"activity"}), activity=aggregate_activity
        )

    return current_lfms_with_activity


# ===================================
