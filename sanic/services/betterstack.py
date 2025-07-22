import requests
import os
from datetime import datetime

BETTERSTACK_API_URL = "https://uptime.betterstack.com/api/v1/heartbeat/"
SERVER_STATUS_KEY = os.getenv("BETTERSTACK_SERVER_STATUS_KEY")

SERVER_STATUS_HEARTBEAT_INTERVAL = 60  # seconds
last_server_status_heartbeat = 0

def server_status_heartbeat():
    """
    This function is used to send a heartbeat signal to the BetterStack service.
    It is called periodically to indicate that the server status check is running.
    """
    # TODO: this should only be called on production environment
    global last_server_status_heartbeat
    current_time = int(datetime.now().timestamp())
    if current_time - last_server_status_heartbeat < SERVER_STATUS_HEARTBEAT_INTERVAL:
        return
    last_server_status_heartbeat = current_time

    if SERVER_STATUS_KEY:
        print(f"Sending server status heartbeat to BetterStack")
        requests.post(
            BETTERSTACK_API_URL + SERVER_STATUS_KEY,
        )