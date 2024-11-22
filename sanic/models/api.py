from enum import Enum
from typing import Optional
from pydantic import BaseModel

from models.character import Character
from models.lfm import Lfm


class LastUpdateTimestamps(BaseModel):
    argonnessen: Optional[float] = None
    cannith: Optional[float] = None
    ghallanda: Optional[float] = None
    khyber: Optional[float] = None
    orien: Optional[float] = None
    sarlona: Optional[float] = None
    thelanis: Optional[float] = None
    wayfinder: Optional[float] = None
    hardcore: Optional[float] = None
    cormyr: Optional[float] = None


class CharacterRequestApiModel(BaseModel):
    last_update_timestamps: Optional[LastUpdateTimestamps] = None
    characters: Optional[list[Character]] = None


class LfmRequestApiModel(BaseModel):
    last_update_timestamps: Optional[LastUpdateTimestamps] = None
    lfms: Optional[list[Lfm]] = None


class CharacterRequestType(str, Enum):
    set = "set"
    update = "update"


class LfmRequestType(str, Enum):
    set = "set"
    update = "update"
