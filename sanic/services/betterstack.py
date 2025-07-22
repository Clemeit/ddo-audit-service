import requests
import os
import logging
from datetime import datetime
from typing import Dict, Optional
from enum import Enum

logger = logging.getLogger(__name__)


class HeartbeatType(Enum):
    """Enum for different heartbeat types."""

    SERVER_INFO = "server_info"
    CHARACTER_COLLECTIONS = "character_collections"
    LFM_COLLECTIONS = "lfm_collections"


class BetterStackService:
    """
    Service for managing BetterStack heartbeat monitoring.
    Centralizes heartbeat logic and eliminates code duplication.
    """

    def __init__(self):
        self.api_url = "https://uptime.betterstack.com/api/v1/heartbeat/"
        self.default_interval = 60  # seconds

        # Configuration for each heartbeat type
        self.heartbeat_config = {
            HeartbeatType.SERVER_INFO: {
                "key": os.getenv("BETTERSTACK_SERVER_INFO_KEY"),
                "interval": self.default_interval,
                "last_heartbeat": 0,
                "description": "Server Info endpoint monitoring",
            },
            HeartbeatType.CHARACTER_COLLECTIONS: {
                "key": os.getenv("BETTERSTACK_CHARACTER_COLLECTIONS_KEY"),
                "interval": self.default_interval,
                "last_heartbeat": 0,
                "description": "Character Collections endpoint monitoring",
            },
            HeartbeatType.LFM_COLLECTIONS: {
                "key": os.getenv("BETTERSTACK_LFM_COLLECTIONS_KEY"),
                "interval": self.default_interval,
                "last_heartbeat": 0,
                "description": "LFM Collections endpoint monitoring",
            },
        }

    def _should_send_heartbeat(self, heartbeat_type: HeartbeatType) -> bool:
        """
        Check if enough time has passed since the last heartbeat.

        Args:
            heartbeat_type: The type of heartbeat to check

        Returns:
            bool: True if heartbeat should be sent, False otherwise
        """
        config = self.heartbeat_config[heartbeat_type]
        current_time = int(datetime.now().timestamp())
        time_since_last = current_time - config["last_heartbeat"]

        return time_since_last >= config["interval"]

    def _send_heartbeat_request(
        self, api_key: str, heartbeat_type: HeartbeatType
    ) -> bool:
        """
        Send the actual heartbeat request to BetterStack.

        Args:
            api_key: The API key for the heartbeat
            heartbeat_type: The type of heartbeat being sent

        Returns:
            bool: True if request was successful, False otherwise
        """
        try:
            url = f"{self.api_url}{api_key}"
            response = requests.post(url, timeout=10)
            response.raise_for_status()

            logger.debug(f"Heartbeat sent successfully for {heartbeat_type.value}")
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send heartbeat for {heartbeat_type.value}: {e}")
            return False

    def send_heartbeat(self, heartbeat_type: HeartbeatType) -> bool:
        """
        Send a heartbeat signal if the interval has passed and API key is configured.

        Args:
            heartbeat_type: The type of heartbeat to send

        Returns:
            bool: True if heartbeat was sent successfully, False otherwise
        """
        config = self.heartbeat_config[heartbeat_type]

        # Check if API key is configured
        if not config["key"]:
            logger.warning(f"No API key configured for {heartbeat_type.value}")
            return False

        # Check if enough time has passed
        if not self._should_send_heartbeat(heartbeat_type):
            logger.debug(
                f"Skipping heartbeat for {heartbeat_type.value} - interval not reached"
            )
            return False

        # Send the heartbeat
        success = self._send_heartbeat_request(config["key"], heartbeat_type)

        if success:
            # Update last heartbeat timestamp
            config["last_heartbeat"] = int(datetime.now().timestamp())

        return success


# Global service instance
_betterstack_service = BetterStackService()


def server_info_heartbeat() -> bool:
    """
    Send a heartbeat signal for the server info endpoint.
    Called when the /server-info endpoint receives a request.

    Returns:
        bool: True if heartbeat was sent successfully, False otherwise
    """
    return _betterstack_service.send_heartbeat(HeartbeatType.SERVER_INFO)


def character_collections_heartbeat() -> bool:
    """
    Send a heartbeat signal for Character Collections.
    Called when the /characters endpoint receives a request.

    Returns:
        bool: True if heartbeat was sent successfully, False otherwise
    """
    return _betterstack_service.send_heartbeat(HeartbeatType.CHARACTER_COLLECTIONS)


def lfm_collections_heartbeat() -> bool:
    """
    Send a heartbeat signal for LFM Collections.
    Called when the /lfms endpoint receives a request.

    Returns:
        bool: True if heartbeat was sent successfully, False otherwise
    """
    return _betterstack_service.send_heartbeat(HeartbeatType.LFM_COLLECTIONS)
