import services.postgres as postgres_client
import services.redis as redis_client
from utils.scheduler import run_on_schedule


class ServerStatusUpdater:
    def save_game_info(self):
        try:
            game_info = redis_client.get_server_info_as_dict()
            postgres_client.add_game_info(game_info)
        except Exception as e:
            print(f"Failed to save game info: {e}")


def get_game_info_scheduler(
    save_game_info_interval: int = 300,
) -> tuple[callable, callable]:
    game_info_updater = ServerStatusUpdater()
    return run_on_schedule(game_info_updater.save_game_info, save_game_info_interval)
