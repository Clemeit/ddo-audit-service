from typing import Optional

from models.character import Character
from models.lfm import LFM
from pydantic import BaseModel, Field


class ServerCharacterDataApiModel(BaseModel):
    data: list[Character]
    updated: list[str]
    deleted: list[str]


class ServerLfmDataApiModel(BaseModel):
    data: list[LFM]
    updated: list[str]
    deleted: list[str]


class CharacterRequestApiModel(BaseModel):
    argonnessen: Optional[ServerCharacterDataApiModel] = None
    cannith: Optional[ServerCharacterDataApiModel] = None
    ghallanda: Optional[ServerCharacterDataApiModel] = None
    khyber: Optional[ServerCharacterDataApiModel] = None
    orien: Optional[ServerCharacterDataApiModel] = None
    sarlona: Optional[ServerCharacterDataApiModel] = None
    thelanis: Optional[ServerCharacterDataApiModel] = None
    wayfinder: Optional[ServerCharacterDataApiModel] = None
    hardcore: Optional[ServerCharacterDataApiModel] = None
    cormyr: Optional[ServerCharacterDataApiModel] = None


class LfmRequestApiModel(BaseModel):
    argonnessen: Optional[ServerLfmDataApiModel] = None
    cannith: Optional[ServerLfmDataApiModel] = None
    ghallanda: Optional[ServerLfmDataApiModel] = None
    khyber: Optional[ServerLfmDataApiModel] = None
    orien: Optional[ServerLfmDataApiModel] = None
    sarlona: Optional[ServerLfmDataApiModel] = None
    thelanis: Optional[ServerLfmDataApiModel] = None
    wayfinder: Optional[ServerLfmDataApiModel] = None
    hardcore: Optional[ServerLfmDataApiModel] = None
    cormyr: Optional[ServerLfmDataApiModel] = None
