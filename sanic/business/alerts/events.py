"""Putting alert events on the queue the dispatch worker reads.

Producers call these from hot paths (LFM ingestion runs every second), so they only RPUSH and never raise.
"""

import logging
from datetime import datetime, timezone

import orjson
import services.redis as redis_client
from models.redis import RedisKeys

logger = logging.getLogger(__name__)

EVENTS_KEY = RedisKeys.ALERT_EVENTS.value
# If the worker is down, keep only the newest events so the list cannot grow without bound.
MAX_QUEUED_EVENTS = 10_000


def build_event(alert_type: str, data: dict) -> bytes:
    return orjson.dumps(
        {
            "type": alert_type,
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
    )


def emit_alert_events(alert_type: str, items: list[dict]) -> int:
    """Queue one event per item. Returns how many were queued (0 on failure)."""
    if not items:
        return 0
    try:
        payloads = [build_event(alert_type, item) for item in items]
        with redis_client.get_redis_client() as client:
            pipe = client.pipeline(transaction=False)
            pipe.rpush(EVENTS_KEY, *payloads)
            pipe.ltrim(EVENTS_KEY, -MAX_QUEUED_EVENTS, -1)
            pipe.execute()
        return len(payloads)
    except Exception as e:
        logger.error(f"Failed to emit {alert_type} alert events: {e}")
        return 0


async def async_emit_alert_event(alert_type: str, data: dict) -> bool:
    try:
        client = await redis_client.get_async_redis_client()
        await client.rpush(EVENTS_KEY, build_event(alert_type, data))
        return True
    except Exception as e:
        logger.error(f"Failed to emit {alert_type} alert event: {e}")
        return False
