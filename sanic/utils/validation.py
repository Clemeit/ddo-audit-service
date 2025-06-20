from constants.server import SERVER_NAMES_LOWERCASE


def is_server_name_valid(server_name: str) -> bool:
    return server_name.lower() in SERVER_NAMES_LOWERCASE


def is_character_name_valid(character_name: str) -> bool:
    # can be alphanumeric or hyphen only, not empty
    return character_name.replace("-", "").isalnum()
