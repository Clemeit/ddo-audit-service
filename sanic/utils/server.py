from constants.server import SERVER_NAMES_LOWERCASE


def is_server_name_valid(server_name: str) -> bool:
    return server_name.lower() in SERVER_NAMES_LOWERCASE
