import os
import logging
import services.redis as redis_client

from endpoints.activity import activity_blueprint
from endpoints.characters import character_blueprint
from endpoints.game import game_blueprint
from endpoints.population import population_blueprint
from endpoints.health import health_blueprint
from endpoints.lfms import lfm_blueprint
from endpoints.service import service_blueprint
from endpoints.verification import verification_blueprint
from endpoints.quests import quest_blueprint
from endpoints.areas import area_blueprint
from endpoints.demographics import demographics_blueprint
from endpoints.guilds import guild_blueprint
from endpoints.user import user_blueprint
from reports.server_status import get_game_info_scheduler
from services.redis import close_redis_async, initialize_redis
from services.postgres import initialize_postgres, close_postgres_client
from utils.route import is_method_open, is_route_open
from utils.access_log import (
    build_access_event,
    dumps_event,
    get_client_ip,
    get_request_id,
    monotonic_duration_ms,
    monotonic_start_ns,
    response_size_bytes,
    should_log,
)

from sanic import Sanic, json
from sanic.request import Request

API_KEY = os.getenv("API_KEY", "")
APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8000"))

app = Sanic("ddo-audit-server")
app.config.REQUEST_MAX_SIZE = 500 * 1024 * 1024  # 500 MB

# Emit JSON access logs to stdout. (If the app is run under a process manager that
# already configures logging handlers, we won't override it.)
if not logging.getLogger().handlers:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(message)s")

access_logger = logging.getLogger("access")

app.blueprint(
    [
        character_blueprint,
        lfm_blueprint,
        activity_blueprint,
        health_blueprint,
        game_blueprint,
        population_blueprint,
        service_blueprint,
        verification_blueprint,
        quest_blueprint,
        area_blueprint,
        demographics_blueprint,
        guild_blueprint,
        user_blueprint,
    ]
)

start_game_info_polling, stop_game_info_polling = get_game_info_scheduler()


@app.middleware("request")
async def start_request_context(request: Request):
    request.ctx.start_ns = monotonic_start_ns()
    request.ctx.request_id = get_request_id(request)


@app.listener("before_server_start")
async def set_up_connections(app, loop):
    initialize_redis()
    initialize_postgres()
    start_game_info_polling()


@app.listener("after_server_stop")
async def close_connections(app, loop):
    await close_redis_async()
    close_postgres_client()
    stop_game_info_polling()


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


@app.middleware("response")
async def log_request_response(request: Request, response):
    # Ensure request id is always present, even for odd lifecycle cases.
    request_id = getattr(
        getattr(request, "ctx", object()), "request_id", None
    ) or get_request_id(request)
    response.headers["X-Request-ID"] = request_id

    start_ns = getattr(getattr(request, "ctx", object()), "start_ns", None)
    duration_ms = monotonic_duration_ms(start_ns) if isinstance(start_ns, int) else 0

    status = getattr(response, "status", 0) or 0

    # Lightweight Redis counters for investigation (fail-open if Redis is down).
    try:
        route_path = None
        try:
            if getattr(request, "route", None):
                route_path = getattr(request.route, "path", None)
        except Exception:
            route_path = None

        await redis_client.traffic_increment(
            ip=get_client_ip(request),
            route=route_path or getattr(request, "path", None),
            method=getattr(request, "method", None),
            status=int(status),
            bytes_out=response_size_bytes(response),
        )
    except Exception:
        pass

    if should_log(int(status), int(duration_ms)):
        event = build_access_event(
            request,
            response,
            request_id=request_id,
            duration_ms=duration_ms,
        )
        # access_logger.info(dumps_event(event))

    return response


if __name__ == "__main__":
    app.run(host=APP_HOST, port=APP_PORT)
