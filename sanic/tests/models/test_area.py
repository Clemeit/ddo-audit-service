import pytest
from pydantic import ValidationError

from models.area import Area


def test_area_model_defaults_and_dump():
    area = Area(id=1, name="Korthos")

    assert area.model_dump() == {
        "id": 1,
        "name": "Korthos",
        "is_public": True,
        "is_wilderness": False,
        "region": None,
    }


def test_area_required_fields_and_optional_none():
    with pytest.raises(ValidationError):
        Area(name="Missing Id")

    with pytest.raises(ValidationError):
        Area(id=10)

    area = Area(id=2, name="The Twelve", region=None)
    assert area.model_dump()["region"] is None
