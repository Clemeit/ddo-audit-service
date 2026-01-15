"""
Quest endpoints.
"""

import services.postgres as postgres_client

from datetime import datetime, timezone
from sanic import Blueprint
from sanic.response import json
from sanic.request import Request
from utils.areas import get_valid_area_ids
from utils.quests import get_quests
from utils.quest_metrics_calc import get_quest_metrics_single

from models.quest import Quest
from models.quest_session import QuestAnalytics

quest_blueprint = Blueprint("quests", url_prefix="/quests", version=1)


@quest_blueprint.get("/<quest_name:str>")
async def get_quest_by_name(request: Request, quest_name: str):
    """
    Method: GET

    Route: /quests/<quest_name:str>

    Description: Get quest by name.
    """

    try:
        quest: Quest = postgres_client.get_quest_by_name(quest_name)
        if not quest:
            return json({"message": "quest not found"}, status=404)
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"data": quest.model_dump()})


@quest_blueprint.get("/<quest_id:int>/analytics")
async def get_quest_analytics(request: Request, quest_id: int):
    """
    Method: GET

    Route: /quests/<quest_id:int>/analytics

    Description: Get comprehensive analytics for a quest including duration statistics,
    activity patterns, and time series data. Data is cached with 90-day lookback.

    Query Parameters:
    - refresh=true: Force recalculation and update of metrics (optional)
    """

    try:
        # Verify quest exists
        quest: Quest = postgres_client.get_quest_by_id(quest_id)
        if not quest:
            return json({"message": "quest not found"}, status=404)

        # Check if refresh is requested
        refresh = request.args.get("refresh", "false").lower() == "true"

        # Try to get cached metrics (always used unless refresh=true)
        cached_metrics = (
            None if refresh else postgres_client.get_quest_metrics(quest_id)
        )

        if cached_metrics and not refresh:
            result = {
                "data": cached_metrics["analytics_data"],
                "cached": True,
                "updated_at": cached_metrics["updated_at"].isoformat(),
                "heroic_xp_per_minute_relative": cached_metrics[
                    "heroic_xp_per_minute_relative"
                ],
                "epic_xp_per_minute_relative": cached_metrics[
                    "epic_xp_per_minute_relative"
                ],
                "heroic_popularity_relative": cached_metrics[
                    "heroic_popularity_relative"
                ],
                "epic_popularity_relative": cached_metrics["epic_popularity_relative"],
            }
            return json(result)

        # Cache miss or refresh requested: calculate metrics for this quest only
        quest_metrics = get_quest_metrics_single(
            quest_id, force_refresh=refresh, cached_metrics=cached_metrics
        )

        if not quest_metrics:
            return json({"message": "insufficient data for metrics"}, status=404)

        # Upsert to database
        postgres_client.upsert_quest_metrics(
            quest_id,
            quest_metrics["heroic_xp_per_minute_relative"],
            quest_metrics["epic_xp_per_minute_relative"],
            quest_metrics["heroic_popularity_relative"],
            quest_metrics["epic_popularity_relative"],
            quest_metrics["analytics_data"],
        )

        result = {
            "data": quest_metrics["analytics_data"],
            "cached": False,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "heroic_xp_per_minute_relative": quest_metrics[
                "heroic_xp_per_minute_relative"
            ],
            "epic_xp_per_minute_relative": quest_metrics["epic_xp_per_minute_relative"],
            "heroic_popularity_relative": quest_metrics["heroic_popularity_relative"],
            "epic_popularity_relative": quest_metrics["epic_popularity_relative"],
        }
        return json(result)

    except Exception as e:
        return json({"message": str(e)}, status=500)


@quest_blueprint.get("/<quest_id:int>")
async def get_quest_by_id(request: Request, quest_id: int):
    """
    Method: GET

    Route: /quests/<quest_id:int>

    Description: Get quest by id.
    """

    try:
        quest: Quest = postgres_client.get_quest_by_id(quest_id)
        if not quest:
            return json({"message": "quest not found"}, status=404)
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"data": quest.model_dump()})


@quest_blueprint.get("")
async def get_all_quests(request: Request):
    """
    Method: GET

    Route: /quests

    Description: Get all quests.
    """

    try:
        force = request.args.get("force", "false").lower() == "true"
        quest_list, source, timestamp = get_quests(skip_cache=force)
        if not quest_list:
            return json({"message": "no quests found"}, status=404)
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"data": quest_list, "source": source, "timestamp": timestamp})


@quest_blueprint.post("")
async def update_quests(request: Request):
    """
    Method: POST

    Route: /quests

    Description: Update quests.
    """

    all_area_ids, _, _ = get_valid_area_ids()

    try:
        raw_quest_list = request.json
        if not raw_quest_list:
            return json({"message": "no quests provided"}, status=400)
        quest_list: list[Quest] = []
        for quest in raw_quest_list:
            quest: dict
            if "DNT" in quest.get("name", ""):
                continue
            if int(quest.get("area")) not in all_area_ids:
                print("Skipping quest with invalid area ID:", int(quest.get("area")))
                continue
            xp_object = {
                "heroic_casual": quest.get("heroiccasualxp"),
                "heroic_normal": quest.get("heroicnormalxp"),
                "heroic_hard": quest.get("heroichardxp"),
                "heroic_elite": quest.get("heroicelitexp"),
                "epic_casual": quest.get("epiccasualxp"),
                "epic_normal": quest.get("epicnormalxp"),
                "epic_hard": quest.get("epichardxp"),
                "epic_elite": quest.get("epicelitexp"),
            }
            quest_list.append(
                Quest(
                    id=int(quest.get("questid") if quest.get("questid") else 0),
                    alt_id=int(quest.get("altid") if quest.get("altid") else 0),
                    area_id=int(quest.get("area") if quest.get("area") else 0),
                    name=quest.get("name", ""),
                    heroic_normal_cr=quest.get("heroicnormalcr"),
                    epic_normal_cr=quest.get("epicnormalcr"),
                    required_adventure_pack=quest.get("requiredadventurepack"),
                    adventure_area=(
                        quest.get("adventurearea")
                        if quest.get("adventurearea")
                        else None
                    ),
                    quest_journal_area=quest.get("questjournalgroup"),
                    group_size=quest.get("groupsize"),
                    patron=quest.get("patron"),
                    xp=xp_object,
                    length=int(quest.get("length") if quest.get("length") else 0),
                    tip=quest.get("tip"),
                    is_free_to_vip=True if quest.get("isfreetovip") == "1" else False,
                )
            )

        postgres_client.update_quests(quest_list)
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"message": "quest updated"})
