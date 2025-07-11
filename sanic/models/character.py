from typing import Optional

from constants.activity import CharacterActivityType
from pydantic import BaseModel, field_serializer

from models.quest import Quest


class CharacterClass(BaseModel):
    name: str
    level: int


class CharacterLocation(BaseModel):
    id: int
    name: Optional[str] = None
    region: Optional[str] = None
    is_public_space: Optional[bool] = None


class Character(BaseModel):
    id: int
    name: Optional[str] = None
    gender: Optional[str] = None
    race: Optional[str] = None
    total_level: Optional[int] = None
    classes: Optional[list[CharacterClass]] = None
    location_id: Optional[int] = None
    guild_name: Optional[str] = None
    server_name: Optional[str] = None
    home_server_name: Optional[str] = None
    group_id: Optional[int] = None
    is_online: Optional[bool] = True
    is_in_party: Optional[bool] = None
    is_anonymous: Optional[bool] = None
    is_recruiting: Optional[bool] = None
    public_comment: Optional[str] = None
    last_update: Optional[str] = (
        None  # the last time the character was updated from Collections
    )
    last_save: Optional[str] = (
        None  # the last time the character was persisted in the database
    )


class CharacterActivity(BaseModel):
    """
    This model will be used to store information about each character's activity in the postgres database.
    """

    character_id: int
    activity_type: CharacterActivityType
    data: dict


class CharacterActivitySummary(BaseModel):
    level_event_count: Optional[int] = None
    location_event_count: Optional[int] = None
    guild_name_event_count: Optional[int] = None
    server_name_event_count: Optional[int] = None
    status_event_count: Optional[int] = None


class CharacterQuestActivity(BaseModel):
    timestamp: Optional[str] = None
    quest_id: Optional[int] = None


CHARACTER_ACTIVITY_TYPES = [item.value for item in CharacterActivityType]


class QuestTimer(BaseModel):
    quest: Quest
    instances: list[str]
