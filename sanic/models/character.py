from typing import Optional

from pydantic import BaseModel


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
    is_in_party: Optional[bool] = None
    is_anonymous: Optional[bool] = None
    is_recruiting: Optional[bool] = None
    public_comment: Optional[str] = None
