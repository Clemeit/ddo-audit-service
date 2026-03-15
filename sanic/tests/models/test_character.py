import pytest
from pydantic import ValidationError

from constants.activity import CharacterActivityType
from models.character import (
    CHARACTER_ACTIVITY_TYPES,
    Character,
    CharacterActivity,
    CharacterClass,
    CharacterLocation,
    CharacterQuestActivity,
    QuestTimer,
)
from models.quest import Quest


def test_character_class_and_location_models():
    character_class = CharacterClass(name="Wizard", level=20)
    location = CharacterLocation(
        id=123,
        name="Marketplace",
        region="Stormreach",
        is_public_space=True,
    )

    assert character_class.model_dump() == {"name": "Wizard", "level": 20}
    assert location.model_dump() == {
        "id": 123,
        "name": "Marketplace",
        "region": "Stormreach",
        "is_public_space": True,
    }


def test_character_model_nested_and_defaults():
    character = Character(
        id=1,
        name="Aria",
        total_level=20,
        classes=[CharacterClass(name="Wizard", level=20)],
        location_id=42,
        is_online=False,
        last_update="2026-03-15T00:00:00Z",
    )

    dumped = character.model_dump()
    assert dumped["id"] == 1
    assert dumped["classes"] == [{"name": "Wizard", "level": 20}]
    assert dumped["is_online"] is False


def test_character_requires_id_and_accepts_optional_none():
    with pytest.raises(ValidationError):
        Character(name="NoId")

    character = Character(id=2, name=None, guild_name=None, group_id=None)
    assert character.model_dump()["name"] is None
    assert character.model_dump()["guild_name"] is None


def test_character_activity_enum_validation_and_dump():
    activity = CharacterActivity(
        character_id=1,
        activity_type="status",
        data={"is_online": True},
    )

    assert activity.activity_type == CharacterActivityType.STATUS
    assert activity.model_dump() == {
        "character_id": 1,
        "activity_type": CharacterActivityType.STATUS,
        "data": {"is_online": True},
    }

    with pytest.raises(ValidationError):
        CharacterActivity(character_id=1, activity_type="unknown", data={})


def test_character_activity_types_constant_matches_enum_values():
    assert CHARACTER_ACTIVITY_TYPES == [item.value for item in CharacterActivityType]


def test_character_quest_activity_optional_fields_accept_none():
    quest_activity = CharacterQuestActivity(timestamp=None, quest_id=None)

    assert quest_activity.model_dump() == {
        "timestamp": None,
        "quest_id": None,
    }


def test_quest_timer_model():
    quest = Quest(id=7, name="The Collaborator")
    timer = QuestTimer(quest=quest, instances=["2026-03-15T12:00:00Z"])

    assert timer.model_dump()["quest"]["id"] == 7
    assert timer.model_dump()["instances"] == ["2026-03-15T12:00:00Z"]

    with pytest.raises(ValidationError):
        QuestTimer(instances=[])
