import pytest
from pydantic import ValidationError

from models.api import (
    CharacterRequestApiModel,
    CharacterRequestType,
    LfmRequestApiModel,
    LfmRequestType,
)
from models.character import Character
from models.lfm import Lfm


def test_character_request_api_model_defaults_and_dump():
    model = CharacterRequestApiModel()

    assert model.model_dump() == {
        "characters": None,
        "deleted_ids": [],
    }


def test_character_request_deleted_ids_default_not_shared():
    first = CharacterRequestApiModel()
    second = CharacterRequestApiModel()

    first.deleted_ids.append(1)

    assert second.deleted_ids == []


def test_character_request_with_data_and_validation():
    model = CharacterRequestApiModel(
        characters=[Character(id=1, name="Aria")],
        deleted_ids=[2, 3],
    )

    dumped = model.model_dump()
    assert dumped["characters"][0]["id"] == 1
    assert dumped["deleted_ids"] == [2, 3]

    with pytest.raises(ValidationError):
        CharacterRequestApiModel(deleted_ids=["bad-id"])


def test_lfm_request_api_model_defaults_and_validation():
    model = LfmRequestApiModel()
    assert model.model_dump() == {
        "lfms": None,
        "deleted_ids": [],
    }

    with_data = LfmRequestApiModel(lfms=[Lfm(id=7)], deleted_ids=[8])
    assert with_data.model_dump()["lfms"][0]["id"] == 7

    with pytest.raises(ValidationError):
        LfmRequestApiModel(deleted_ids=["x"])


def test_request_type_enums():
    assert CharacterRequestType.set.value == "set"
    assert CharacterRequestType("update") is CharacterRequestType.update

    assert LfmRequestType.set.value == "set"
    assert LfmRequestType("update") is LfmRequestType.update

    with pytest.raises(ValueError):
        CharacterRequestType("invalid")

    with pytest.raises(ValueError):
        LfmRequestType("invalid")
