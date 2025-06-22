from enum import Enum


class CharacterActivityType(str, Enum):
    TOTAL_LEVEL = "total_level"
    LOCATION = "location"
    GUILD_NAME = "guild_name"
    SERVER_NAME = "server_name"
    STATUS = "status"


MAX_CHARACTER_ACTIVITY_READ_LENGTH = 500  # 500 activity events
MAX_CHARACTER_ACTIVITY_READ_HISTORY = 90  # 90 days
