from constants.server import SERVER_NAMES_LOWERCASE


def is_server_name_valid(server_name: str) -> bool:
    return server_name.lower() in SERVER_NAMES_LOWERCASE


def is_character_name_valid(character_name: str) -> bool:
    # can be alphanumeric or hyphen only, not empty
    return character_name.replace("-", "").isalnum()


def is_character_id_valid(character_id: str) -> bool:
    """Returns true if the character_id is a valid int."""

    try:
        int(character_id)
    except ValueError:
        return False
    return True
