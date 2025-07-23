import os
import requests
from typing import Dict, Any
from core import Check
from datetime import datetime, timedelta


class ServerInfoCheck(Check):
    """Server info check."""

    def __init__(
        self,
        url: str = "http://sanic:8000/v1/game/server-info",
        betterstack_key: str = None,
        interval: int = 60,
    ):
        super().__init__("Server Info Check", interval)
        self.url = url
        self.betterstack_key = betterstack_key

    def execute(self) -> Dict[str, Any]:
        """Execute the server info check."""
        try:
            response = requests.get(self.url, timeout=10)

            if response.status_code == 200:
                data = response.json()

                if not data:
                    return {
                        "success": False,
                        "error": "No server data returned from API",
                    }

                healthy_servers = []
                unhealthy_servers = []

                for server_name, server_info in data.items():
                    last_status_check_iso_string = server_info.get(
                        "last_status_check", ""
                    )
                    last_data_fetch_iso_string = server_info.get("last_data_fetch", "")

                    if (
                        not last_status_check_iso_string
                        or not last_data_fetch_iso_string
                    ):
                        unhealthy_servers.append(
                            {
                                "server": server_name,
                                "reason": "Missing last_status_check or last_data_fetch",
                            }
                        )
                        continue

                    try:
                        # Parse timestamps with proper error handling
                        last_status_check = datetime.fromisoformat(
                            last_status_check_iso_string
                        )
                        last_data_fetch = datetime.fromisoformat(
                            last_data_fetch_iso_string
                        )

                        # Use UTC for comparison to avoid timezone issues
                        now = datetime.now(tz=last_status_check.tzinfo)

                        # Check if both timestamps are within the last minute
                        status_check_fresh = now - last_status_check < timedelta(
                            minutes=1
                        )
                        data_fetch_fresh = now - last_data_fetch < timedelta(minutes=1)

                        if status_check_fresh and data_fetch_fresh:
                            healthy_servers.append(
                                {
                                    "server": server_name,
                                    "last_status_check": last_status_check_iso_string,
                                    "last_data_fetch": last_data_fetch_iso_string,
                                }
                            )
                        else:
                            age_status = (now - last_status_check).total_seconds()
                            age_data = (now - last_data_fetch).total_seconds()
                            unhealthy_servers.append(
                                {
                                    "server": server_name,
                                    "reason": f"Data too old - status: {age_status:.0f}s, data: {age_data:.0f}s",
                                }
                            )

                    except (ValueError, TypeError) as e:
                        unhealthy_servers.append(
                            {
                                "server": server_name,
                                "reason": f"Invalid timestamp format: {str(e)}",
                            }
                        )

                # Determine overall health status
                total_servers = len(healthy_servers) + len(unhealthy_servers)

                if healthy_servers and not unhealthy_servers:
                    # All servers healthy
                    return {
                        "success": True,
                        "total_servers": total_servers,
                        "healthy_servers": len(healthy_servers),
                        "servers": healthy_servers,
                        "betterstack_key": self.betterstack_key,
                    }
                elif healthy_servers and unhealthy_servers:
                    # Some servers healthy, some not
                    return {
                        "success": False,
                        "error": f"{len(unhealthy_servers)} of {total_servers} servers unhealthy",
                        "total_servers": total_servers,
                        "healthy_servers": len(healthy_servers),
                        "unhealthy_servers": unhealthy_servers,
                    }
                else:
                    # No healthy servers
                    return {
                        "success": False,
                        "error": f"All {total_servers} servers are unhealthy",
                        "unhealthy_servers": unhealthy_servers,
                    }
            else:
                return {
                    "success": False,
                    "error": f"HTTP {response.status_code}: {response.text}",
                }

        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"Request failed: {str(e)}"}
