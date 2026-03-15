import pytest
from pydantic import ValidationError

from constants.server import SERVER_NAMES_LOWERCASE
from models.area import Area
from models.character import Character
from models.lfm import Lfm
from models.quest import Quest, QuestV2
from models.redis import (
    DictDict,
    KnownAreasModel,
    KnownQuestsModel,
    KnownQuestsWithMetricsModel,
    NewsModel,
    PageMessagesModel,
    REDIS_KEY_TYPE_MAPPING,
    RedisKeys,
    ServerCharacterData,
    ServerInfo,
    ServerLfmData,
    ServerSpecificInfo,
    VerificationChallengesModel,
)
from models.service import News, PageMessage


def test_server_info_nested_model_and_dump():
    model = ServerInfo(
        servers={
            "argonnessen": ServerSpecificInfo(
                index=1, is_online=True, character_count=10
            )
        }
    )

    dumped = model.model_dump()
    assert dumped["servers"]["argonnessen"]["index"] == 1
    assert dumped["servers"]["argonnessen"]["is_online"] is True
    assert dumped["servers"]["argonnessen"]["character_count"] == 10


def test_server_info_default_dict_not_shared_between_instances():
    first = ServerInfo()
    second = ServerInfo()

    first.servers["argonnessen"] = ServerSpecificInfo(index=5)

    assert second.servers == {}


def test_server_character_data_validation_and_optional_none():
    model = ServerCharacterData(characters={1: Character(id=1, name="A")})
    dumped = model.model_dump()

    assert dumped["characters"][1]["id"] == 1
    assert dumped["characters"][1]["name"] == "A"

    with pytest.raises(ValidationError):
        ServerCharacterData(characters={"abc": {"id": 1}})


def test_server_lfm_data_model_dump():
    model = ServerLfmData(lfms={1: Lfm(id=1, comment="hello")})

    assert model.model_dump()["lfms"][1]["id"] == 1
    assert model.model_dump()["lfms"][1]["comment"] == "hello"


def test_known_models_accept_optional_none_and_dump():
    known_areas = KnownAreasModel(areas=[Area(id=1, name="Korthos")], timestamp=1.23)
    known_quests = KnownQuestsModel(
        quests=[Quest(id=10, name="The Collaborator")],
        timestamp=2.34,
    )
    known_with_metrics = KnownQuestsWithMetricsModel(
        quests=[QuestV2(id=11, name="The Pit", heroic_popularity_relative=0.5)],
        timestamp=3.45,
    )
    news_model = NewsModel(news=[News(message="News item")], timestamp=4.56)
    page_messages = PageMessagesModel(
        page_messages=[PageMessage(message="Maintenance")],
        timestamp=5.67,
    )

    assert known_areas.model_dump()["areas"][0]["name"] == "Korthos"
    assert known_quests.model_dump()["quests"][0]["name"] == "The Collaborator"
    assert (
        known_with_metrics.model_dump()["quests"][0]["heroic_popularity_relative"]
        == 0.5
    )
    assert news_model.model_dump()["news"][0]["message"] == "News item"
    assert page_messages.model_dump()["page_messages"][0]["message"] == "Maintenance"


def test_verification_challenges_defaults_and_not_shared():
    first = VerificationChallengesModel()
    second = VerificationChallengesModel()

    first.challenges[1] = "word"

    assert second.challenges == {}


def test_redis_keys_enum_values_resolve_correctly():
    assert RedisKeys.SERVER_INFO.value == "server_info"
    assert RedisKeys.KNOWN_AREAS.value == "known_areas"
    assert RedisKeys.KNOWN_QUESTS.value == "known_quests"
    assert RedisKeys.NEWS.value == "news"
    assert RedisKeys.PAGE_MESSAGES.value == "page_messages"
    assert RedisKeys.ACTIVE_QUEST_SESSIONS.value == "active_quest_sessions"
    assert (
        RedisKeys.CHARACTERS.value.format(server="argonnessen")
        == "argonnessen:characters"
    )
    assert RedisKeys.LFMS.value.format(server="argonnessen") == "argonnessen:lfms"


def test_redis_key_type_mapping_contains_expected_entries():
    assert REDIS_KEY_TYPE_MAPPING[RedisKeys.SERVER_INFO] is ServerInfo
    assert REDIS_KEY_TYPE_MAPPING[RedisKeys.KNOWN_AREAS] is KnownAreasModel
    assert REDIS_KEY_TYPE_MAPPING[RedisKeys.KNOWN_QUESTS] is KnownQuestsModel
    assert REDIS_KEY_TYPE_MAPPING[RedisKeys.NEWS] is NewsModel
    assert REDIS_KEY_TYPE_MAPPING[RedisKeys.PAGE_MESSAGES] is PageMessagesModel
    assert REDIS_KEY_TYPE_MAPPING[RedisKeys.ACTIVE_QUEST_SESSIONS] is DictDict

    for server in SERVER_NAMES_LOWERCASE:
        assert REDIS_KEY_TYPE_MAPPING[f"{server}:characters"] is DictDict
        assert REDIS_KEY_TYPE_MAPPING[f"{server}:lfms"] is DictDict
