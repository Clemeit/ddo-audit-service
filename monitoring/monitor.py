#!/usr/bin/env python3

import os
from core import MonitoringService
from checks import ServerInfoCheck


def main():
    """Main entry point for the monitoring service."""

    # Create monitoring service
    monitoring = MonitoringService()

    # Add server info check
    server_info_check = ServerInfoCheck(
        url="http://sanic:8000/v1/game/server-info",
        betterstack_key=os.getenv("BETTERSTACK_SERVER_INFO_API_KEY"),
        interval=60,
    )
    monitoring.add_check(server_info_check)

    monitoring.start()


if __name__ == "__main__":
    main()
