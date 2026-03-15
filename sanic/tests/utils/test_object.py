import pytest

from utils.object import get_nested_value


class TestGetNestedValue:
    def test_returns_nested_leaf_value(self):
        data = {"a": {"b": {"c": 7}}}
        assert get_nested_value(data, "a.b.c") == 7

    def test_returns_none_when_intermediate_key_missing(self):
        data = {"a": {"b": {"c": 7}}}
        assert get_nested_value(data, "a.x.c") is None

    def test_returns_none_when_leaf_key_missing(self):
        data = {"a": {"b": {"c": 7}}}
        assert get_nested_value(data, "a.b.z") is None

    def test_returns_none_when_leaf_value_is_none(self):
        data = {"a": {"b": None}}
        assert get_nested_value(data, "a.b") is None

    def test_empty_field_reads_empty_key(self):
        data = {"": "value"}
        assert get_nested_value(data, "") == "value"

    def test_raises_when_intermediate_value_is_not_a_dict(self):
        data = {"a": 3}
        with pytest.raises(AttributeError):
            get_nested_value(data, "a.b")

    def test_raises_when_root_is_none(self):
        with pytest.raises(AttributeError):
            get_nested_value(None, "a")
