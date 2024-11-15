from typing import Optional

from constants.server import SERVER_NAMES_LOWERCASE
from models.character import Character
from models.lfm import Lfm
from pydantic import BaseModel


class ServerInfo(BaseModel):
    """
    This model will be used to store information about each server in the redis database using reJSON.
    """

    index: Optional[int] = None
    created: Optional[float] = None
    last_status_check: Optional[float] = None
    last_data_fetch: Optional[float] = None
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


class ServerCharactersData(BaseModel):
    """
    This model will be used to store information about each server's characters in the redis database using reJSON.
    """

    characters: dict[str, Character] = {}
    last_updated: Optional[float] = None


class ServerLFMsData(BaseModel):
    """
    This model will be used to store information about each server's LFMs in the redis database using reJSON.
    """

    lfms: dict[str, Lfm] = {}
    last_updated: Optional[float] = None


CACHE_MODEL = {
    "game_info": GameInfo(),
    **{
        f"{server}:characters": ServerCharactersData()
        for server in SERVER_NAMES_LOWERCASE
    },
    **{f"{server}:lfms": ServerLFMsData() for server in SERVER_NAMES_LOWERCASE},
}
