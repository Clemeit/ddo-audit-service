from typing import Optional, Dict

from constants.server import SERVER_NAMES_LOWERCASE
from models.character import Character
from models.lfm import Lfm
from pydantic import BaseModel
from models.area import Area
from enum import Enum
from models.service import News, PageMessage
from models.quest import Quest


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


class KnownAreasModel(BaseModel):
    areas: Optional[list[Area]] = None
    timestamp: Optional[float] = None


class KnownQuestsModel(BaseModel):
    quests: Optional[list[Quest]] = None
    timestamp: Optional[float] = None


class NewsModel(BaseModel):
    news: Optional[list[News]] = None
    timestamp: Optional[float] = None


class PageMessagesModel(BaseModel):
    page_messages: Optional[list[PageMessage]] = None
    timestamp: Optional[float] = None


class RedisKeys(Enum):
    SERVER_INFO = "server_info"
    VERIFICATION_CHALLENGES = "verification_challenges"
    KNOWN_AREAS = "known_areas"
    KNOWN_QUESTS = "known_quests"
    CHARACTERS = "{server}:characters"
    LFMS = "{server}:lfms"
    NEWS = "news"
    PAGE_MESSAGES = "page_messages"


class VerificationChallengesModel(BaseModel):
    challenges: Optional[Dict[int, str]] = None


DictDict: dict[int, dict] = {}


REDIS_KEY_TYPE_MAPPING: Dict[RedisKeys, type] = {
    RedisKeys.SERVER_INFO: ServerInfo,
    RedisKeys.VERIFICATION_CHALLENGES: VerificationChallengesModel,
    RedisKeys.KNOWN_AREAS: KnownAreasModel,
    RedisKeys.KNOWN_QUESTS: KnownQuestsModel,
    RedisKeys.NEWS: NewsModel,
    RedisKeys.PAGE_MESSAGES: PageMessagesModel,
    **{
        RedisKeys.CHARACTERS.value.format(server=server): DictDict
        for server in SERVER_NAMES_LOWERCASE
    },
    **{
        RedisKeys.LFMS.value.format(server=server): DictDict
        for server in SERVER_NAMES_LOWERCASE
    },
}
