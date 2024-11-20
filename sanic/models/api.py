from enum import Enum
from typing import Optional

from models.character import Character
from models.lfm import Lfm
from models.base_model import ConfiguredBaseModel as BaseModel


class CharacterRequestApiModel(BaseModel):
    characters: Optional[list[Character]] = None


class LfmRequestApiModel(BaseModel):
    lfms: Optional[list[Lfm]] = None


class CharacterRequestType(str, Enum):
    set = "set"
    update = "update"


class LfmRequestType(str, Enum):
    set = "set"
    update = "update"
