from enum import Enum


class CharacterActivityType(str, Enum):
    total_level = "total_level"
    location = "location"
    guild_name = "guild_name"
    server_name = "server_name"
    status = "status"
