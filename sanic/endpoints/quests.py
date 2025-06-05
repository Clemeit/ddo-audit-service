"""
Quest endpoints.
"""

import services.postgres as postgres_client

from sanic import Blueprint
from sanic.response import json
from sanic.request import Request
from utils.areas import get_valid_area_ids
from utils.quests import get_quests

from models.quest import Quest

quest_blueprint = Blueprint("quests", url_prefix="/quests", version=1)


class AuthorizationError(Exception):
    pass


class VerificationError(Exception):
    pass


# @quest_blueprint.get("/names")
# async def quest_all_quest_names(
#     request: Request,
# ):
#     """
#     Method: GET

#     Route: /quests/names

#     Description: Get all quest names.
#     """

#     try:
#         quest_name_list = postgres_client.get_all_quest_names()
#     except Exception as e:
#         return json({"message": str(e)}, status=500)
#     return json({"data": quest_name_list})


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
async def get_quests_endpoint(request: Request):
    """
    Method: GET

    Route: /quests

    Description: Get all quests.
    """

    try:
        quest_list, source, timestamp = get_quests()
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

    all_area_ids: list[int] = get_valid_area_ids()

    try:
        raw_quest_list = request.json
        if not raw_quest_list:
            return json({"message": "no quests provided"}, status=400)
        quest_list: list[Quest] = []
        for quest in raw_quest_list:
            quest: dict
            if "DNT" in quest["Name"]:
                continue
            quest_area: dict = quest.get("QuestArea")
            if int(quest_area["Id"], 16) not in all_area_ids:
                continue
            xp_object = {
                "heroic_casual": quest.get("HeroicCasualXp"),
                "heroic_normal": quest.get("HeroicNormalXp"),
                "heroic_hard": quest.get("HeroicHardXp"),
                "heroic_elite": quest.get("HeroicEliteXp"),
                "epic_casual": quest.get("EpicCasualXp"),
                "epic_normal": quest.get("EpicNormalXp"),
                "epic_hard": quest.get("EpicHardXp"),
                "epic_elite": quest.get("EpicEliteXp"),
            }
            quest_list.append(
                Quest(
                    id=int(quest["Id"], 16),
                    area_id=int(quest_area["Id"], 16) if quest_area else None,
                    name=quest["Name"],
                    heroic_normal_cr=quest.get("ChallengeRatingNormal"),
                    epic_normal_cr=quest.get("ChallengeRatingEpic"),
                    required_adventure_pack=quest.get("RequiredAdventurePack"),
                    adventure_area=quest_area.get("AreaName") if quest_area else None,
                    quest_journal_area=quest.get("QuestJournalArea"),
                    group_size=quest.get("GroupSize"),
                    patron=quest.get("Patron"),
                    xp=xp_object,
                    length=quest.get("QuestLength"),
                )
            )

        postgres_client.update_quests(quest_list)
    except Exception as e:
        return json({"message": str(e)}, status=500)
    return json({"message": "quest updated"})
