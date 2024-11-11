from typing import Optional

from models.character import Character
from pydantic import BaseModel


class QuestLevel(BaseModel):
    heroic_normal: Optional[int] = None
    heroic_hard: Optional[int] = None
    heroic_elite: Optional[int] = None
    epic_normal: Optional[int] = None
    epic_hard: Optional[int] = None
    epic_elite: Optional[int] = None


class QuestXP(BaseModel):
    heroic_normal: Optional[int] = None
    heroic_hard: Optional[int] = None
    heroic_elite: Optional[int] = None
    epic_normal: Optional[int] = None
    epic_hard: Optional[int] = None
    epic_elite: Optional[int] = None


class Quest(BaseModel):
    id: str
    alt_id: Optional[str] = None
    area_id: Optional[str] = None
    name: Optional[str] = None
    level: Optional[QuestLevel] = None
    xp: Optional[QuestXP] = None
    is_free_to_play: Optional[bool] = None
    is_free_to_vip: Optional[bool] = None
    required_adventure_pack: Optional[str] = None
    adventure_area: Optional[str] = None
    quest_journal_group: Optional[str] = None
    group_size: Optional[str] = None
    patron: Optional[str] = None
    average_time: Optional[float] = None
    tip: Optional[str] = None


class LFM(BaseModel):
    id: str
    comment: Optional[str] = None
    quest: Optional[Quest] = None
    is_quest_guess: Optional[bool] = None
    difficulty: Optional[str] = None
    accepted_classes: Optional[list[str]] = None
    accepted_classes_count: Optional[int] = None
    minimum_level: Optional[int] = None
    maximum_level: Optional[int] = None
    adventure_active_time: Optional[int] = None
    leader: Optional[Character] = None
    members: Optional[list[Character]] = None
