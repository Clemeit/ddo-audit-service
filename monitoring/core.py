import os
import time
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any
import requests

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)

logger = logging.getLogger(__name__)


class Check(ABC):
    """Base class for all monitoring checks."""

    def __init__(self, name: str, interval: int = 60):
        self.name = name
        self.interval = interval
        self.last_run = 0

    @abstractmethod
    def execute(self) -> Dict[str, Any]:
        """Execute the check and return result."""
        pass

    def should_run(self) -> bool:
        """Check if enough time has passed since last run."""
        current_time = int(time.time())
        return current_time - self.last_run >= self.interval

    def run(self) -> Dict[str, Any]:
        """Run the check if it's time."""
        if not self.should_run():
            return {"skipped": True, "reason": "interval not reached"}

        self.last_run = int(time.time())
        logger.info(f"Executing check: {self.name}")

        try:
            result = self.execute()
            logger.info(f"Check {self.name} completed: {result}")
            return result
        except Exception as e:
            logger.error(f"Check {self.name} failed: {str(e)}")
            return {"success": False, "error": str(e)}


class BetterStackNotifier:
    """Handles sending notifications to BetterStack."""

    def __init__(self):
        self.api_url = "https://uptime.betterstack.com/api/v1/heartbeat/"

    def send_heartbeat(self, heartbeat_key: str, check_name: str) -> bool:
        """Send a heartbeat to BetterStack."""
        if not heartbeat_key:
            logger.warning(f"No BetterStack key configured for {check_name}")
            return False

        try:
            response = requests.post(f"{self.api_url}{heartbeat_key}", timeout=10)
            if response.status_code == 200:
                logger.info(f"BetterStack heartbeat sent successfully for {check_name}")
                return True
            else:
                logger.error(
                    f"BetterStack heartbeat failed for {check_name}: {response.status_code}"
                )
                return False
        except Exception as e:
            logger.error(
                f"Failed to send BetterStack heartbeat for {check_name}: {str(e)}"
            )
            return False


class MonitoringService:
    """Main monitoring service that manages all checks."""

    def __init__(self):
        self.checks = []
        self.betterstack = BetterStackNotifier()

    def add_check(self, check: Check):
        """Add a check to the monitoring service."""
        self.checks.append(check)
        logger.info(f"Added check: {check.name}")

    def run_checks(self):
        """Run all registered checks."""
        for check in self.checks:
            result = check.run()

            # If check was successful and has a BetterStack key, send heartbeat
            if result.get("success") and result.get("betterstack_key"):
                self.betterstack.send_heartbeat(result["betterstack_key"], check.name)

    def start(self):
        """Start the monitoring service."""
        logger.info("Starting monitoring service...")
        logger.info(f"Registered {len(self.checks)} checks")

        while True:
            try:
                self.run_checks()
                time.sleep(5)  # Check every 5 seconds if any check needs to run
            except KeyboardInterrupt:
                logger.info("Monitoring service stopped")
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {str(e)}")
                time.sleep(10)  # Wait longer on error
