"""Parsing the 64-bit IDs the game uses, as people write them (decimal or 0x hex)."""

INT64_MAX = 2**63 - 1


def parse_game_id(text: str | None) -> int | None:
    """An ID written as decimal ("12345") or hex ("0x3039"); None if it is not one or does not
    fit the database's signed 64-bit integer."""
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    try:
        value = int(text, 16) if text.lower().startswith("0x") else int(text, 10)
    except ValueError:
        return None
    return value if 0 <= value <= INT64_MAX else None
