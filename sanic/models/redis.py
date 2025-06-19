from typing import Optional, Dict

from constants.server import SERVER_NAMES_LOWERCASE
from models.character import Character
from models.lfm import Lfm
from pydantic import BaseModel


class ServerInfo(BaseModel):
    """
    This model will be used to store information about each server in the redis database using reJSON.
    """

    index: Optional[int] = None
    last_status_check: Optional[str] = None
    last_data_fetch: Optional[str] = None
    attached_timestamp: Optional[str] = None
    is_online: Optional[bool] = None
    character_count: Optional[int] = None
    lfm_count: Optional[int] = None
    queue_number: Optional[int] = None
    is_vip_only: Optional[bool] = None


class GameInfo(BaseModel):
    """
    This model will be used to store information about the game in the redis database using reJSON.
    """

    servers: dict[str, ServerInfo] = {}


ServerInfoDict = Dict[str, ServerInfo]


class ServerCharactersData(BaseModel):
    """
    This model will be used to store information about each server's characters in the redis database using reJSON.
    """

    characters: dict[int, Character] = {}
    last_update: Optional[str] = None


class ServerLFMsData(BaseModel):
    """
    This model will be used to store information about each server's LFMs in the redis database using reJSON.
    """

    lfms: dict[int, Lfm] = {}
    last_update: Optional[str] = None


CACHE_MODEL = {
    "game_info": GameInfo(),
    **{
        f"{server}:characters": ServerCharactersData()
        for server in SERVER_NAMES_LOWERCASE
    },
    **{f"{server}:lfms": ServerLFMsData() for server in SERVER_NAMES_LOWERCASE},
    "verification_challenges": {},
}
