from typing import Optional

from pydantic import BaseModel
from enum import Enum


class CharacterClass(BaseModel):
    name: str
    level: int


class CharacterLocation(BaseModel):
    name: str
    region: Optional[str]
    is_public_space: bool


class Character(BaseModel):
    id: str
    name: Optional[str] = None
    gender: Optional[str] = None
    race: Optional[str] = None
    total_level: Optional[int] = None
    classes: Optional[list[CharacterClass]] = None
    location: Optional[CharacterLocation] = None
    guild_name: Optional[str] = None
    server_name: Optional[str] = None
    home_server_name: Optional[str] = None
    group_id: Optional[str] = None
    is_online: Optional[bool] = True
    is_in_party: Optional[bool] = None
    is_anonymous: Optional[bool] = None
    is_recruiting: Optional[bool] = None
    public_comment: Optional[str] = None


class CharacterActivityType(str, Enum):
    classes = "classes"
    location = "location"
    guild_name = "guild_name"
    server_name = "server_name"
    is_online = "is_online"


class CharacterActivity(BaseModel):
    """
    This model will be used to store information about each character's activity in the postgres database.
    """

    id: str
    activity_type: CharacterActivityType
    data: dict
    # classes: Optional[dict] = None
    # location: Optional[dict] = None
    # guild_name: Optional[dict] = None
    # server_name: Optional[dict] = None
    # is_online: Optional[dict] = None


CHARACTER_ACTIVITY_TYPES = [item.value for item in CharacterActivityType]
