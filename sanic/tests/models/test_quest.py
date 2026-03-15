import pytest
from pydantic import ValidationError

from models.quest import Quest, QuestV2


def test_quest_valid_construction_defaults_and_model_dump():
    quest = Quest(id=100, name="The Pit")
    dumped = quest.model_dump()

    assert dumped["id"] == 100
    assert dumped["name"] == "The Pit"
    assert dumped["is_free_to_vip"] is False
    assert dumped["xp"] is None


def test_quest_missing_required_fields_and_invalid_type():
    with pytest.raises(ValidationError):
        Quest(id=1)

    with pytest.raises(ValidationError):
        Quest(id=1, name="Bad Quest", length="not-an-int")


def test_quest_optional_fields_accept_none():
    quest = Quest(
        id=2,
        name="Optional Quest",
        required_adventure_pack=None,
        adventure_area=None,
        xp=None,
        tip=None,
    )

    dumped = quest.model_dump()
    assert dumped["required_adventure_pack"] is None
    assert dumped["adventure_area"] is None
    assert dumped["xp"] is None
    assert dumped["tip"] is None


def test_quest_v2_flattened_metrics_fields():
    quest_v2 = QuestV2(
        id=3,
        name="Relative Quest",
        heroic_xp_per_minute_relative=0.75,
        epic_popularity_relative=0.42,
    )

    dumped = quest_v2.model_dump()
    assert dumped["heroic_xp_per_minute_relative"] == 0.75
    assert dumped["epic_xp_per_minute_relative"] is None
    assert dumped["heroic_popularity_relative"] is None
    assert dumped["epic_popularity_relative"] == 0.42
