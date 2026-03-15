import pytest
from pydantic import ValidationError

from models.character import Character
from models.lfm import (
    Lfm,
    LfmActivity,
    LfmActivityEvent,
    LfmActivityType,
    Quest as LfmQuest,
    QuestLevel,
    QuestXP,
)


def test_lfm_quest_level_and_xp_models():
    level = QuestLevel(heroic_normal=5, epic_elite=30)
    xp = QuestXP(heroic_normal=1200, epic_elite=25000)

    assert level.model_dump() == {
        "heroic_normal": 5,
        "heroic_hard": None,
        "heroic_elite": None,
        "epic_normal": None,
        "epic_hard": None,
        "epic_elite": 30,
    }
    assert xp.model_dump() == {
        "heroic_normal": 1200,
        "heroic_hard": None,
        "heroic_elite": None,
        "epic_normal": None,
        "epic_hard": None,
        "epic_elite": 25000,
    }


def test_lfm_quest_model_and_optional_none_values():
    quest = LfmQuest(id=10, name=None, level=None, xp=None)

    assert quest.model_dump()["id"] == 10
    assert quest.model_dump()["name"] is None


def test_lfm_activity_models_and_default_list_not_shared():
    activity_a = LfmActivity()
    activity_b = LfmActivity()
    activity_a.events.append(LfmActivityEvent(tag="comment", data="hello"))

    assert activity_a.model_dump()["events"] == [{"tag": "comment", "data": "hello"}]
    assert activity_b.model_dump()["events"] == []


def test_lfm_model_defaults_nested_and_dump():
    lfm = Lfm(
        id=1,
        comment="Running soon",
        leader=Character(id=9, name="Leader"),
        members=[Character(id=10, name="Member")],
    )

    dumped = lfm.model_dump()
    assert dumped["id"] == 1
    assert dumped["leader"]["id"] == 9
    assert dumped["members"][0]["id"] == 10
    assert dumped["accepted_classes"] == []
    assert dumped["activity"] == []


def test_lfm_default_mutable_lists_are_not_shared():
    first = Lfm(id=1)
    second = Lfm(id=2)

    first.accepted_classes.append("Cleric")

    assert second.accepted_classes == []


def test_lfm_requires_id_and_rejects_invalid_members():
    with pytest.raises(ValidationError):
        Lfm()

    with pytest.raises(ValidationError):
        Lfm(id=3, members="not-a-list")


def test_lfm_activity_type_enum_values():
    assert LfmActivityType.posted.value == "posted"
    assert LfmActivityType("comment") is LfmActivityType.comment

    with pytest.raises(ValueError):
        LfmActivityType("invalid")
