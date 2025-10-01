import requests
from typing import Dict, Any
from core import Check
from datetime import datetime, timedelta, timezone
import random


class ServerInfoCheck(Check):
    """Server info check."""

    def __init__(
        self,
        betterstack_key: str = None,
        interval: int = 60,
        ignored_servers: list[str] = None,
    ):
        super().__init__("Server Info Check", interval)
        self.url = "http://sanic:8000/v1/game/server-info"
        self.betterstack_key = betterstack_key
        self.ignored_servers = ignored_servers if ignored_servers else []

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
                    if server_name.lower() in self.ignored_servers:
                        continue

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
                        # Parse timestamps and ensure all are timezone-aware (UTC)
                        last_status_check = datetime.fromisoformat(
                            last_status_check_iso_string
                        )
                        if last_status_check.tzinfo is None:
                            last_status_check = last_status_check.replace(
                                tzinfo=timezone.utc
                            )
                        last_data_fetch = (
                            datetime.fromisoformat(last_data_fetch_iso_string)
                            if is_online
                            else datetime.min.replace(tzinfo=timezone.utc)
                        )
                        if last_data_fetch.tzinfo is None:
                            last_data_fetch = last_data_fetch.replace(
                                tzinfo=timezone.utc
                            )

                        # Use UTC for comparison to avoid timezone issues
                        now = datetime.now(timezone.utc)

                        # Check if both timestamps are within the last minute
                        status_check_fresh = now - last_status_check < timedelta(
                            minutes=1
                        )
                        data_fetch_fresh = now - last_data_fetch < timedelta(minutes=1)

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

                        if status_check_fresh and (data_fetch_fresh or not is_online):
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
        percent_difference_threshold: int = 0.02,
        absolute_difference_threshold: int = 5,
        ignored_servers: list[str] = None,
    ):
        super().__init__("Character Check", interval)
        self.server_info_url = "http://sanic:8000/v1/game/server-info"
        self.character_ids_url = "http://sanic:8000/v1/characters/ids"
        self.character_by_id = "http://sanic:8000/v1/characters"
        self.betterstack_key = betterstack_key
        self.character_update_threshold_minutes = character_update_threshold_minutes
        self.request_timeout = request_timeout
        self.percent_difference_threshold = percent_difference_threshold
        self.absolute_difference_threshold = absolute_difference_threshold
        self.ignored_servers = ignored_servers if ignored_servers else []

    def execute(self) -> Dict[str, Any]:
        """Execute the character check."""
        server_info_data = self._get_server_info_data()

        # Step 1: Verify servers are online (prerequisite check)
        server_check_result = self._check_servers_online(server_info_data)
        if not server_check_result["can_proceed"]:
            return server_check_result["result"]

        # Step 2: Get character IDs
        character_ids_result = self._get_character_ids()
        if not character_ids_result["success"]:
            return character_ids_result

        # Step 3: Test a random character
        character_result = self._check_random_character(
            character_ids_result["character_ids_flat"]
        )
        if not character_result["success"]:
            return character_result

        # Step 4: Check if population and character count has diverged
        return self._check_population(
            server_info_data, character_ids_result["character_ids_by_server"]
        )

    def _get_server_info_data(self) -> Dict[str, Any]:
        try:
            response = requests.get(self.server_info_url, timeout=self.request_timeout)

            if response.status_code != 200:
                return None
            return response.json()
        except:
            return None

    def _check_servers_online(self, server_info_data: Dict[str, Any]) -> Dict[str, Any]:
        """Check if any servers are online. Returns dict with 'can_proceed' and 'result'."""
        try:
            if not server_info_data:
                return {
                    "can_proceed": False,
                    "result": {
                        "success": None,  # Indeterminate
                        "error": "No server data available - cannot determine character health",
                        "betterstack_key": self.betterstack_key,
                    },
                }

            online_servers = [
                server
                for server, info in server_info_data.items()
                if info.get("is_online", False)
            ]

            if not online_servers:
                return {
                    "can_proceed": False,
                    "result": {
                        "success": None,  # Indeterminate
                        "error": "No servers are online - cannot determine character health",
                        "betterstack_key": self.betterstack_key,
                    },
                }

            return {
                "can_proceed": True,
                "result": None,
            }

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
            character_ids_flat = self._extract_character_ids(json_response)
            if not character_ids_flat:
                return {
                    "success": False,
                    "error": "No character IDs found in API response",
                }

            return {
                "success": True,
                "character_ids_flat": character_ids_flat,
                "character_ids_by_server": json_response.get("data", {}),
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

    def _check_random_character(
        self, character_ids: Dict[str, list[int]]
    ) -> Dict[str, Any]:
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

    def _check_population(
        self,
        server_info_data: Dict[str, Any],
        character_ids_by_server: Dict[str, list[int]],
    ) -> Dict[str, Any]:
        """Validate that the population reported by the server-info endpoint aligns with the number of character IDs from the characters/ids endpoint."""
        issues: list[str] = []
        total_reported_character_count: int = (
            0  # The number of characters reported by the server-info endpoint
        )
        total_actual_character_count: int = (
            0  # The number of characters that would be returned by calling the character endpoints
        )

        try:
            for server_name, server_info in server_info_data.items():
                if server_name.lower() in self.ignored_servers:
                    continue

                is_online = server_info.get("is_online", False)
                if not is_online:
                    continue

                if not character_ids_by_server.get(server_name):
                    issues.append(f"{server_name} not found in the character IDs dict")
                    continue

                server_info_character_count = server_info["character_count"]
                character_id_count = len(character_ids_by_server.get(server_name, []))
                character_count_abs_difference = abs(
                    server_info_character_count - character_id_count
                )
                total_reported_character_count += server_info_character_count
                total_actual_character_count += character_id_count
                if (server_info_character_count + character_id_count) > 0:
                    server_percent_difference = character_count_abs_difference / (
                        (server_info_character_count + character_id_count) / 2
                    )
                    if (
                        server_percent_difference > self.percent_difference_threshold
                        and character_count_abs_difference
                        > self.absolute_difference_threshold
                    ):
                        issues.append(
                            f"{server_name} reports {server_info_character_count} characters online, but {character_id_count} were actually returned - {server_percent_difference * 100}% difference"
                        )

            total = total_reported_character_count + total_actual_character_count
            percent_difference = (
                abs(total_reported_character_count - total_actual_character_count)
                / ((total_reported_character_count + total_actual_character_count) / 2)
                if total > 0
                else 0
            )
            if len(issues) == 0:
                return {
                    "success": True,
                    "total_reported_character_count": total_reported_character_count,
                    "total_actual_character_count": total_actual_character_count,
                    "percent_difference": percent_difference,
                    "percent_difference_threshold": self.percent_difference_threshold,
                    "betterstack_key": self.betterstack_key,
                }
            else:
                return {
                    "success": False,
                    "total_reported_character_count": total_reported_character_count,
                    "total_actual_character_count": total_actual_character_count,
                    "percent_difference": percent_difference,
                    "percent_difference_threshold": self.percent_difference_threshold,
                    "errors": issues,
                }
        except Exception as e:
            return {
                "success": False,
                "error": f"Exception thrown while checking population: {str(e)}",
            }
