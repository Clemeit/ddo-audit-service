import csv
from typing import Optional
from pydantic import BaseModel
import json

import os

# set working directory to the root of the project so that the script can use pydantic
os.chdir(os.path.join(os.path.dirname(__file__), ".."))


class Quest(BaseModel):
    id: int
    alt_id: Optional[int] = None
    area_id: Optional[int] = None
    name: str
    heroic_normal_cr: Optional[int] = None
    epic_normal_cr: Optional[int] = None
    is_free_to_vip: Optional[bool] = False
    required_adventure_pack: Optional[str] = None
    adventure_area: Optional[str] = None
    quest_journal_group: Optional[str] = None
    group_size: Optional[str] = None
    patron: Optional[str] = None
    xp: Optional[dict] = None
    length: Optional[int] = None
    tip: Optional[str] = None


def __main__():
    fields = [
        "id",
        "area_id",
        "name",
        "heroic_normal_cr",
        "epic_normal_cr",
        "is_free_to_vip",
        "required_adventure_pack",
        "adventure_area",
        "quest_journal_group",
        "group_size",
        "patron",
        "heroic_casual_xp",
        "heroic_normal_xp",
        "heroic_hard_xp",
        "heroic_elite_xp",
        "epic_casual_xp",
        "epic_normal_xp",
        "epic_hard_xp",
        "epic_elite_xp",
        "length",
        "tip",
        "alt_id",
    ]
    quests: list[dict] = []
    with open("./quests.csv", "r") as csvfile:
        reader = csv.DictReader(csvfile, fieldnames=fields)
        for row in reader:
            quest = Quest(
                id=row.get("id"),
                alt_id=int(row.get("alt_id")) if row.get("alt_id") != "null" else None,
                area_id=(
                    int(row.get("area_id")) if row.get("area_id") != "null" else None
                ),
                name=row.get("name"),
                heroic_normal_cr=(
                    int(row.get("heroic_normal_cr"))
                    if row.get("heroic_normal_cr") != "null"
                    else None
                ),
                epic_normal_cr=(
                    int(row.get("epic_normal_cr"))
                    if row.get("epic_normal_cr") != "null"
                    else None
                ),
                xp={
                    "heroic_casual": (
                        int(row.get("heroic_casual_xp"))
                        if row.get("heroic_casual_xp") != "null"
                        else None
                    ),
                    "heroic_normal": (
                        int(row.get("heroic_normal_xp"))
                        if row.get("heroic_normal_xp") != "null"
                        else None
                    ),
                    "heroic_hard": (
                        int(row.get("heroic_hard_xp"))
                        if row.get("heroic_hard_xp") != "null"
                        else None
                    ),
                    "heroic_elite": (
                        int(row.get("heroic_elite_xp"))
                        if row.get("heroic_elite_xp") != "null"
                        else None
                    ),
                    "epic_casual": (
                        int(row.get("epic_casual_xp"))
                        if row.get("epic_casual_xp") != "null"
                        else None
                    ),
                    "epic_normal": (
                        int(row.get("epic_normal_xp"))
                        if row.get("epic_normal_xp") != "null"
                        else None
                    ),
                    "epic_hard": (
                        int(row.get("epic_hard_xp"))
                        if row.get("epic_hard_xp") != "null"
                        else None
                    ),
                    "epic_elite": (
                        int(row.get("epic_elite_xp"))
                        if row.get("epic_elite_xp") != "null"
                        else None
                    ),
                },
                is_free_to_vip=True if row.get("is_free_to_vip") == "1" else False,
                required_adventure_pack=(
                    row.get("required_adventure_pack")
                    if row.get("required_adventure_pack") != "null"
                    else None
                ),
                adventure_area=(
                    row.get("adventure_area")
                    if row.get("adventure_area") != "null"
                    else None
                ),
                quest_journal_group=(
                    row.get("quest_journal_group")
                    if row.get("quest_journal_group") != "null"
                    else None
                ),
                group_size=(
                    row.get("group_size") if row.get("group_size") != "null" else None
                ),
                patron=row.get("patron") if row.get("patron") != "null" else None,
                length=int(row.get("length")) if row.get("length") else None,
                tip=row.get("tip") if row.get("tip") != "null" else None,
            )
            quests.append(quest.model_dump())
    output = json.dumps(quests, indent=2)
    # save output to file
    with open("./quests.json", "w") as f:
        f.write(output)


if __name__ == "__main__":
    __main__()
