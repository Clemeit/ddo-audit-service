import os

from endpoints.activity import activity_blueprint
from endpoints.characters import character_blueprint
from endpoints.game import game_blueprint
from endpoints.health import health_blueprint
from endpoints.lfms import lfm_blueprint
from endpoints.service import service_blueprint
from endpoints.verification import verification_blueprint
from endpoints.quests import quest_blueprint
from endpoints.areas import area_blueprint
from reports.server_status import get_game_info_scheduler
from services.redis import close_redis, initialize_redis
from utils.route import is_method_open, is_route_open

from sanic import Sanic, json
from sanic.request import Request

API_KEY = os.getenv("API_KEY")
APP_HOST = os.getenv("APP_HOST")
APP_PORT = int(os.getenv("APP_PORT"))

app = Sanic("ddo-audit-server")
app.blueprint(
    [
        character_blueprint,
        lfm_blueprint,
        activity_blueprint,
        health_blueprint,
        game_blueprint,
        service_blueprint,
        verification_blueprint,
        quest_blueprint,
        area_blueprint,
    ]
)

# Set up all of the updaters
# This is now handled by Collections
# start_game_info_polling, stop_game_info_polling = get_game_info_scheduler()


@app.listener("before_server_start")
async def set_up_connections(app, loop):
    initialize_redis()
    # start_game_info_polling()


@app.listener("after_server_stop")
async def close_connections(app, loop):
    close_redis()
    # stop_game_info_polling()


# Middleware to check API key for protected endpoints
@app.middleware("request")
async def check_api_key(request: Request):
    if is_method_open(request):
        return
    if is_route_open(request):
        return

    api_key = request.headers.get("Authorization")
    if not api_key:
        return json({"error": "API key required"}, status=401)
    if not api_key.startswith("Bearer "):
        return json({"error": "Invalid API key format"}, status=401)
    api_key = api_key[7:]
    if api_key != API_KEY:
        return json({"error": "Invalid API key"}, status=403)


if __name__ == "__main__":
    app.run(host=APP_HOST, port=APP_PORT)
