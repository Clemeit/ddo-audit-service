from typing import Optional

from models.character import Character
from models.lfm import Lfm
from pydantic import BaseModel


class ServerCharacterDataApiModel(BaseModel):
    data: Optional[list[Character]] = None
    updated: Optional[list[str]] = None
    deleted: Optional[list[str]] = None


class ServerLfmDataApiModel(BaseModel):
    data: Optional[list[Lfm]] = None
    updated: Optional[list[str]] = None
    deleted: Optional[list[str]] = None


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
