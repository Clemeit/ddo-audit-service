import pytest
from pydantic import ValidationError

from models.game import GameWorld, PopulationDataPoint, PopulationPointInTime


def test_population_data_point_model_dump_and_type_coercion():
    point = PopulationDataPoint(character_count=10, lfm_count=2)

    assert point.model_dump() == {
        "character_count": 10.0,
        "lfm_count": 2.0,
    }


def test_population_data_point_validation_errors():
    with pytest.raises(ValidationError):
        PopulationDataPoint(character_count="not-a-number", lfm_count=2)

    with pytest.raises(ValidationError):
        PopulationDataPoint(character_count=10)


def test_population_point_in_time_with_nested_data_and_optional_none():
    point_in_time = PopulationPointInTime(
        timestamp=None,
        data={"argonnessen": PopulationDataPoint(character_count=100, lfm_count=20)},
    )

    dumped = point_in_time.model_dump()
    assert dumped["timestamp"] is None
    assert dumped["data"]["argonnessen"]["character_count"] == 100.0
    assert dumped["data"]["argonnessen"]["lfm_count"] == 20.0


def test_population_point_in_time_requires_timestamp_field():
    with pytest.raises(ValidationError):
        PopulationPointInTime(data={})


def test_game_world_defaults_and_model_dump():
    world = GameWorld(name="Argonnessen", status_server="up", order=1)

    assert world.model_dump() == {
        "name": "Argonnessen",
        "status_server": "up",
        "order": 1,
        "allow_billing_role": "",
        "queue_number": 0,
    }


def test_game_world_invalid_type():
    with pytest.raises(ValidationError):
        GameWorld(name="Argonnessen", status_server="up", order=1, queue_number="bad")
