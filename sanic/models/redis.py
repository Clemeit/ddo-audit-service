from typing import Optional, Dict

from constants.server import SERVER_NAMES_LOWERCASE
from models.character import Character
from models.lfm import Lfm
from pydantic import BaseModel
from models.area import Area
from enum import Enum
from models.service import News, PageMessage


class ServerSpecificInfo(BaseModel):
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


class ServerInfo(BaseModel):
    """
    This model will be used to store information about the game in the redis database using reJSON.
    """

    servers: dict[str, ServerSpecificInfo] = {}


ServerInfoDict = Dict[str, ServerSpecificInfo]


class ServerCharacterData(BaseModel):
    """
    This model will be used to store information about each server's characters in the redis database using reJSON.
    """

    characters: dict[int, Character] = {}


class ServerLfmData(BaseModel):
    """
    This model will be used to store information about each server's LFMs in the redis database using reJSON.
    """

    lfms: dict[int, Lfm] = {}


# CACHE_MODEL = {
#     "game_info": GameInfo(),
#     **{
#         f"{server}:characters": ServerCharactersData()
#         for server in SERVER_NAMES_LOWERCASE
#     },
#     **{f"{server}:lfms": ServerLFMsData() for server in SERVER_NAMES_LOWERCASE},
#     "verification_challenges": {},
# }


class ValidAreaIdsModel(BaseModel):
    valid_area_ids: Optional[list[int]] = None
    timestamp: Optional[float] = None


class ValidAreasModel(BaseModel):
    valid_areas: Optional[list[Area]] = None
    timestamp: Optional[float] = None


class RedisKeys(Enum):
    SERVER_INFO = "server_info"
    VERIFICATION_CHALLENGES = "verification_challenges"
    VALID_AREA_IDS = "valid_area_ids"
    VALID_AREAS = "valid_areas"
    CHARACTERS = "{server}:characters"
    LFMS = "{server}:lfms"
    NEWS = "news"
    PAGE_MESSAGES = "page_messages"


class VerificationChallengesModel(BaseModel):
    challenges: Optional[Dict[int, str]] = None


REDIS_KEY_TYPE_MAPPING: Dict[RedisKeys, type] = {
    RedisKeys.SERVER_INFO: ServerInfo,
    RedisKeys.VERIFICATION_CHALLENGES: VerificationChallengesModel,
    RedisKeys.VALID_AREA_IDS: ValidAreaIdsModel,
    RedisKeys.VALID_AREAS: ValidAreasModel,
    RedisKeys.NEWS: list[News],
    RedisKeys.PAGE_MESSAGES: list[PageMessage],
    **{
        RedisKeys.CHARACTERS.value.format(server=server): ServerCharacterData
        for server in SERVER_NAMES_LOWERCASE
    },
    **{
        RedisKeys.LFMS.value.format(server=server): ServerLfmData
        for server in SERVER_NAMES_LOWERCASE
    },
}
