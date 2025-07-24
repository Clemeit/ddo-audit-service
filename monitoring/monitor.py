#!/usr/bin/env python3

import os
from core import MonitoringService
from checks import ServerInfoCheck, CharacterCheck


def main():
    """Main entry point for the monitoring service."""

    # Create monitoring service
    monitoring = MonitoringService()

    # Add server info check
    server_info_check = ServerInfoCheck(
        betterstack_key=os.getenv("BETTERSTACK_SERVER_INFO_API_KEY"),
        interval=60,
    )
    monitoring.add_check(server_info_check)

    # Add character check
    character_check = CharacterCheck(
        betterstack_key=os.getenv("BETTERSTACK_CHARACTER_API_KEY"),
        interval=60,
        character_update_threshold_minutes=5,
        request_timeout=10,
    )
    monitoring.add_check(character_check)

    monitoring.start()


if __name__ == "__main__":
    main()
