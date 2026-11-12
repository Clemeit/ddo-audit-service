import pytest
from utils.game_ids import INT64_MAX, parse_game_id


@pytest.mark.parametrize(
    "text, expected",
    [
        ("0x3039", 0x3039),
        ("0X3039", 0x3039),
        ("12345", 12345),
        ("  12345  ", 12345),
        ("0", 0),
        (hex(INT64_MAX), INT64_MAX),
    ],
)
def test_parses_decimal_and_hex(text, expected):
    assert parse_game_id(text) == expected


@pytest.mark.parametrize(
    "text",
    [None, "", "  ", "abc", "0x", "0xZZ", "-1", "1.5", hex(INT64_MAX + 1), str(INT64_MAX + 1)],
)
def test_rejects_anything_else(text):
    assert parse_game_id(text) is None
