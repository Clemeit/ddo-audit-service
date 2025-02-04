import xml.etree.ElementTree as ET
from datetime import datetime

import requests
import services.postgres as postgres_client
import services.redis as redis_client
from models.game import GameWorld
from models.redis import GameInfo, ServerInfo
from requests import HTTPError
from utils.scheduler import run_batch_on_schedule
from constants.server import SERVER_NAMES_LOWERCASE


class ServerStatusUpdater:
    class Constants:
        DATA_CENTER_SERVER_URL = "http://gls.ddo.com/GLS.DataCenterServer/Service.asmx"
        SOAP_ACTION = "http://www.turbine.com/SE/GLS/GetDatacenters"
        CONTENT_TYPE = "text/xml; charset=utf-8"
        BODY = '<?xml version="1.0" encoding="utf-8"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema"><soap:Body><GetDatacenters xmlns="http://www.turbine.com/SE/GLS"><game>DDO</game></GetDatacenters></soap:Body></soap:Envelope>'

    def __init__(self):
        self.redis_client = redis_client.get_redis_client()
        self.postgres_client = postgres_client.get_postgres_client()
        self.game_info = None

    def query_worlds(self) -> list[GameWorld]:
        """Query the data center server for the list of worlds."""
        worlds: list[GameWorld] = []
        response = requests.post(
            self.Constants.DATA_CENTER_SERVER_URL,
            headers={
                "SOAPAction": self.Constants.SOAP_ACTION,
                "Content-Type": self.Constants.CONTENT_TYPE,
            },
            data=self.Constants.BODY,
        )
        if response.status_code != 200:
            raise HTTPError(
                f"Failed to query data center server: {response.status_code}"
            )
        response_text = response.text
        root = ET.fromstring(response_text)
        for datacenter in root.iter("{http://www.turbine.com/SE/GLS}Datacenter"):
            for world in datacenter.iter("{http://www.turbine.com/SE/GLS}World"):
                name = world.find("{http://www.turbine.com/SE/GLS}Name").text
                status_server = world.find(
                    "{http://www.turbine.com/SE/GLS}StatusServerUrl"
                ).text
                if "http://198.252.160.21/GLS.STG.DataCenterServer" in status_server:
                    status_server = status_server.replace(
                        "http://198.252.160.21/GLS.STG.DataCenterServer",
                        "http://gls.ddo.com/GLS.DataCenterServer",
                    )
                order = int(world.find("{http://www.turbine.com/SE/GLS}Order").text)
                worlds.append(
                    GameWorld(name=name, status_server=status_server, order=order)
                )
        return worlds

    def update_worlds(self, worlds: list[GameWorld]) -> GameInfo:
        """Query the status server for each world and update the server status."""
        server_status: dict[str, ServerInfo] = {}
        for world in worlds:
            try:
                is_online = False
                response = requests.get(world.status_server)
                if response.status_code != 200:
                    raise HTTPError(
                        f"Failed to query status server for {world.name}: {response.status_code}"
                    )
                root = ET.fromstring(response.text)
                world.allow_billing_role = root.find("allow_billing_role").text
                world.queue_number = int(root.find("nowservingqueuenumber").text, 16)
                world.name = root.find("name").text
                if "Wayfinder" in world.name:
                    world.name = "Wayfinder"
                if (
                    "StormreachGuest" in world.allow_billing_role
                    or "StormreachStandard" in world.allow_billing_role
                    or "StormreachLimited" in world.allow_billing_role
                ):
                    is_online = True
                is_vip_only_element = root.find("world_requiresubscription")
                is_vip_only = (
                    is_vip_only_element is not None
                    and is_vip_only_element.text == "True"
                )
                server_info = ServerInfo(
                    index=world.order,
                    last_status_check=datetime.now().isoformat(),
                    is_online=is_online,
                    queue_number=world.queue_number,
                    is_vip_only=is_vip_only,
                )
                server_status[world.name.lower()] = server_info
            except Exception:
                server_status[world.name.lower()] = ServerInfo(
                    last_status_check=datetime.now().isoformat(), is_online=False
                )
        # for any server that is missing in server_status, add it with is_online=False
        for server_name in SERVER_NAMES_LOWERCASE:
            if server_name not in server_status:
                server_status[server_name] = ServerInfo(
                    last_status_check=datetime.now().isoformat(), is_online=False
                )
        game_info = GameInfo(servers=server_status)
        return game_info

    def update_game_info(self):
        try:
            worlds = self.query_worlds()
            game_info = self.update_worlds(worlds)

            redis_client.set_game_info(game_info)

        except Exception as e:
            print(f"Failed to update server status: {e}")

    def save_game_info(self):
        try:
            game_info = redis_client.get_all_server_info_as_class()
            postgres_client.add_game_info(game_info)
        except Exception as e:
            print(f"Failed to save game info: {e}")


def get_game_info_scheduler(
    query_game_info_interval: int = 10, save_game_info_interval: int = 300
) -> tuple[callable, callable]:
    game_info_updater = ServerStatusUpdater()
    return run_batch_on_schedule(
        (game_info_updater.update_game_info, query_game_info_interval),
        (game_info_updater.save_game_info, save_game_info_interval),
    )
