SERVER_NAMES: list[str] = [
    "Argonnessen",
    "Cannith",
    "Ghallanda",
    "Khyber",
    "Orien",
    "Sarlona",
    "Thelanis",
    "Wayfinder",
    "Hardcore",
    "Cormyr",
    "Shadowdale",
    "Thrane",
    "Moonsea",
]

SERVER_NAMES_LOWERCASE: list[str] = [
    server_name.lower() for server_name in SERVER_NAMES
]

SSE_SERVER_NAMES: list[str] = ["Cormyr", "Shadowdale", "Thrane", "Moonsea"]
SSE_SERVER_NAMES_LOWERCASE: list[str] = [s.lower() for s in SSE_SERVER_NAMES]

MAX_CHARACTER_LOOKUP_IDS = 100
