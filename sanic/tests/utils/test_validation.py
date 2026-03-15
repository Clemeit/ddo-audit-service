import pytest

from utils.validation import is_character_name_valid, is_server_name_valid


class TestIsServerNameValid:
    def test_accepts_known_server_name(self):
        assert is_server_name_valid("Argonnessen") is True

    def test_accepts_case_insensitive_server_name(self):
        assert is_server_name_valid("aRgOnNeSsEn") is True

    def test_rejects_unknown_server_name(self):
        assert is_server_name_valid("NotARealServer") is False

    def test_rejects_empty_string(self):
        assert is_server_name_valid("") is False

    def test_non_string_raises(self):
        with pytest.raises(AttributeError):
            is_server_name_valid(None)


class TestIsCharacterNameValid:
    def test_accepts_alphanumeric_name(self):
        assert is_character_name_valid("Player42") is True

    def test_accepts_hyphenated_name(self):
        assert is_character_name_valid("Player-42") is True

    @pytest.mark.parametrize("name", ["", "-", "--", "Bad Name", "Bad_Name", "*"])
    def test_rejects_invalid_characters_or_empty(self, name):
        assert is_character_name_valid(name) is False

    def test_non_string_raises(self):
        with pytest.raises(AttributeError):
            is_character_name_valid(None)
