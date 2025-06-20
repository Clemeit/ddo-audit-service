from services.redis import (
    get_challenge_for_character_by_character_id,
    set_challenge_for_character_by_character_id,
)

import random

challenge_words = [
    "kobold",
    "goblin",
    "dwarf",
    "elf",
    "halfling",
    "aasimar",
    "dragonborn",
    "gnome",
    "tiefling",
    "orc",
    "bugbear",
    "eladrin",
    "tabaxi",
]


def get_challenge_word_for_character_by_character_id(character_id: int) -> str:
    """Get the existing challenge word for the character, or select and save a new one."""
    existing_challenge_word = get_challenge_for_character_by_character_id(character_id)
    if existing_challenge_word:
        return existing_challenge_word
    new_challenge_word = random.choice(challenge_words)
    set_challenge_for_character_by_character_id(character_id)
    return new_challenge_word
