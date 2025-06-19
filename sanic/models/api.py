from enum import Enum
from typing import Optional

from models.character import Character
from models.lfm import Lfm
from pydantic import BaseModel


class CharacterRequestApiModel(BaseModel):
    characters: Optional[list[Character]] = None
    deleted_ids: Optional[list[int]] = []  # characters logging off


class LfmRequestApiModel(BaseModel):
    lfms: Optional[list[Lfm]] = None
    deleted_ids: Optional[list[str]] = []  # lfms taken down


class CharacterRequestType(str, Enum):
    set = "set"
    update = "update"


class LfmRequestType(str, Enum):
    set = "set"
    update = "update"
