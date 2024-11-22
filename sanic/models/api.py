from enum import Enum
from typing import Optional
from pydantic import BaseModel

from models.character import Character
from models.lfm import Lfm


class LastUpdateTimestamps(BaseModel):
    argonnessen: Optional[str] = None
    cannith: Optional[str] = None
    ghallanda: Optional[str] = None
    khyber: Optional[str] = None
    orien: Optional[str] = None
    sarlona: Optional[str] = None
    thelanis: Optional[str] = None
    wayfinder: Optional[str] = None
    hardcore: Optional[str] = None
    cormyr: Optional[str] = None


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
