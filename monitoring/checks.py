import os
import requests
from typing import Dict, Any
from core import Check
from datetime import datetime, timedelta
import random


class ServerInfoCheck(Check):
    """Server info check."""

    def __init__(
        self,
        betterstack_key: str = None,
        interval: int = 60,
    ):
        super().__init__("Server Info Check", interval)
        self.url = "http://sanic:8000/v1/game/server-info"
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
                    is_online = server_info.get("is_online", False)

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
                        last_data_fetch = (
                            datetime.fromisoformat(last_data_fetch_iso_string)
                            if is_online
                            else datetime.min
                        )

                        # Use UTC for comparison to avoid timezone issues
                        now = datetime.now(tz=last_status_check.tzinfo)

                        # Check if both timestamps are within the last minute
                        status_check_fresh = now - last_status_check < timedelta(
                            minutes=1
                        )
                        data_fetch_fresh = (
                            now - last_data_fetch < timedelta(minutes=1)
                            if is_online
                            else True
                        )  # If server is offline, we don't check data fetch freshness

                        has_online_characters = (
                            server_info.get("character_count", 0) > 0
                            if is_online
                            else True
                        )

                        if not has_online_characters:
                            unhealthy_servers.append(
                                {
                                    "server": server_name,
                                    "reason": "No online characters",
                                }
                            )
                            continue

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


class CharacterCheck(Check):
    """Character check."""

    def __init__(
        self,
        betterstack_key: str = None,
        interval: int = 60,
        character_update_threshold_minutes: int = 5,
        request_timeout: int = 10,
    ):
        super().__init__("Character Check", interval)
        self.server_info_url = "http://sanic:8000/v1/game/server-info"
        self.character_ids_url = "http://sanic:8000/v1/characters/ids"
        self.character_by_id = "http://sanic:8000/v1/characters"
        self.betterstack_key = betterstack_key
        self.character_update_threshold_minutes = character_update_threshold_minutes
        self.request_timeout = request_timeout

    def execute(self) -> Dict[str, Any]:
        """Execute the character check."""
        # Step 1: Verify servers are online (prerequisite check)
        server_check_result = self._check_servers_online()
        if not server_check_result["can_proceed"]:
            return server_check_result["result"]

        # Step 2: Get character IDs
        character_ids_result = self._get_character_ids()
        if not character_ids_result["success"]:
            return character_ids_result

        # Step 3: Test a random character
        return self._check_random_character(character_ids_result["character_ids"])

    def _check_servers_online(self) -> Dict[str, Any]:
        """Check if any servers are online. Returns dict with 'can_proceed' and 'result'."""
        try:
            response = requests.get(self.server_info_url, timeout=self.request_timeout)

            if response.status_code != 200:
                return {
                    "can_proceed": False,
                    "result": {
                        "success": False,
                        "error": f"Server info endpoint returned HTTP {response.status_code}: {response.text}",
                    },
                }

            data = response.json()
            if not data:
                return {
                    "can_proceed": False,
                    "result": {
                        "success": None,  # Indeterminate
                        "error": "No server data available - cannot determine character health",
                    },
                }

            online_servers = [
                server for server, info in data.items() if info.get("is_online", False)
            ]

            if not online_servers:
                return {
                    "can_proceed": False,
                    "result": {
                        "success": None,  # Indeterminate
                        "error": "No servers are online - cannot determine character health",
                    },
                }

            return {"can_proceed": True, "result": None}

        except requests.exceptions.RequestException as e:
            return {
                "can_proceed": False,
                "result": {
                    "success": None,  # Indeterminate
                    "error": f"Failed to check server status: {str(e)}",
                },
            }

    def _get_character_ids(self) -> Dict[str, Any]:
        """Fetch and extract character IDs from the API."""
        try:
            response = requests.get(
                self.character_ids_url, timeout=self.request_timeout
            )

            if response.status_code != 200:
                return {
                    "success": False,
                    "error": f"Character IDs endpoint returned HTTP {response.status_code}: {response.text}",
                }

            json_response = response.json()
            if not json_response:
                return {
                    "success": False,
                    "error": "No data returned from character IDs endpoint",
                }

            # Extract character IDs from the nested structure
            character_ids = self._extract_character_ids(json_response)
            if not character_ids:
                return {
                    "success": False,
                    "error": "No character IDs found in API response",
                }

            return {
                "success": True,
                "character_ids": character_ids,
            }

        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"Failed to fetch character IDs: {str(e)}",
            }

    def _extract_character_ids(self, json_response: Dict[str, Any]) -> list:
        """Extract character IDs from the API response structure."""
        try:
            data = json_response.get("data", {})
            character_ids = []

            for server_character_list in data.values():
                if server_character_list and isinstance(server_character_list, list):
                    character_ids.extend(server_character_list)

            return character_ids

        except (ValueError, TypeError, AttributeError):
            return []

    def _check_random_character(self, character_ids: list) -> Dict[str, Any]:
        """Check a randomly selected character for recent updates."""
        if not character_ids:
            return {
                "success": False,
                "error": "No character IDs available for testing",
            }

        random_character_id = random.choice(character_ids)

        try:
            response = requests.get(
                f"{self.character_by_id}/{random_character_id}",
                timeout=self.request_timeout,
            )

            if response.status_code != 200:
                return {
                    "success": False,
                    "error": f"Character endpoint returned HTTP {response.status_code} for ID {random_character_id}: {response.text}",
                }

            character_data = self._extract_character_data(response.json())
            if not character_data:
                return {
                    "success": False,
                    "error": f"No character data found for ID {random_character_id}",
                }

            # Validate character freshness
            return self._validate_character_freshness(
                random_character_id, character_data
            )

        except requests.exceptions.RequestException as e:
            return {
                "success": False,
                "error": f"Failed to fetch character {random_character_id}: {str(e)}",
            }

    def _extract_character_data(self, json_response: Dict[str, Any]) -> Dict[str, Any]:
        """Extract character data from the API response."""
        if not json_response:
            return {}
        return json_response.get("data", {})

    def _validate_character_freshness(
        self, character_id: str, character_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Validate that the character has been updated recently."""
        last_update_str = character_data.get("last_update", "")
        if not last_update_str:
            return {
                "success": False,
                "error": f"Character {character_id} has no last_update timestamp",
            }

        try:
            last_update = datetime.fromisoformat(last_update_str)
            now = datetime.now(tz=last_update.tzinfo)
            age = now - last_update

            if age < timedelta(minutes=self.character_update_threshold_minutes):
                return {
                    "success": True,
                    "character_id": character_id,
                    "last_update": last_update_str,
                    "age_seconds": int(age.total_seconds()),
                    "betterstack_key": self.betterstack_key,
                }
            else:
                return {
                    "success": False,
                    "error": f"Character {character_id} not updated recently (age: {int(age.total_seconds())}s, threshold: {self.character_update_threshold_minutes * 60}s)",
                }

        except (ValueError, TypeError) as e:
            return {
                "success": False,
                "error": f"Invalid last_update timestamp format for character {character_id}: {str(e)}",
            }
