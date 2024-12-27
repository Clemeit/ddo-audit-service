from enum import Enum


class CharacterActivityType(str, Enum):
    total_level = "total_level"
    location = "location"
    guild_name = "guild_name"
    server_name = "server_name"
    status = "status"


MAX_CHARACTER_ACTIVITY_READ_LENGTH = 500  # 500 activity events
MAX_CHARACTER_ACTIVITY_READ_HISTORY = 90  # 90 days
