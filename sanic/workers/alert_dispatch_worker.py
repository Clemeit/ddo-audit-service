"""
Alert Dispatch Worker - matches queued alert events against users' rules and delivers the alerts.

Producers (LFM ingestion today; server status, shard exchange, character activity later) RPUSH events
onto the Redis list ``alerts:events``. This worker BLPOPs them, matches each against an in-memory index
of enabled rules (refreshed from Postgres every ALERT_RULE_REFRESH_SECS), dedupes and caps per user,
and hands each alert to every channel: the per-user inbox the open site polls, and Web Push (when
VAPID_PRIVATE_KEY and VAPID_SUBJECT are set) for browsers that turned push notifications on.

See business/alerts for the types, channels and dispatcher.
"""

import os
import sys
import time
import logging

# Ensure relative imports work when invoked as module or script
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import business.alerts  # noqa: F401  (registers the alert types)  # type: ignore
from business.alerts.base import AlertChannel  # type: ignore
from business.alerts.channels.inbox import InboxChannel  # type: ignore
from business.alerts.channels.web_push import WebPushChannel  # type: ignore
from business.alerts.dispatcher import (  # type: ignore
    RULES_VERSION_KEY,
    AlertDispatcher,
    record_metrics,
)
from business.alerts.events import EVENTS_KEY  # type: ignore
from services.alerts_db import get_enabled_rules_by_type  # type: ignore
from services.postgres import initialize_postgres  # type: ignore
from services.redis import get_redis_client, initialize_redis  # type: ignore

logger = logging.getLogger("alert_dispatch_worker")
logging.basicConfig(
    level=os.getenv("WORKER_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)


def env_int(name: str, default: int) -> int:
    """Parse environment variable as integer with fallback."""
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    """Parse environment variable as float with fallback."""
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def read_rules_version():
    with get_redis_client() as client:
        return client.get(RULES_VERSION_KEY)


def build_channels() -> list[AlertChannel]:
    channels: list[AlertChannel] = [InboxChannel()]
    vapid_private_key = os.getenv("VAPID_PRIVATE_KEY", "").strip()
    vapid_subject = os.getenv("VAPID_SUBJECT", "").strip()
    if vapid_private_key and vapid_subject:
        channels.append(WebPushChannel(vapid_private_key, vapid_subject))
    else:
        logger.warning(
            "VAPID_PRIVATE_KEY / VAPID_SUBJECT not set: Web Push alerts are disabled"
        )
    return channels


def build_dispatcher() -> AlertDispatcher:
    return AlertDispatcher(
        load_rules=get_enabled_rules_by_type,
        channels=build_channels(),
        refresh_seconds=env_float("ALERT_RULE_REFRESH_SECS", 30.0),
        rules_version=read_rules_version,
    )


def run_worker():
    """Main worker loop."""
    blpop_timeout = env_int("ALERT_WORKER_BLPOP_TIMEOUT_SECS", 2)  # below REDIS_SOCKET_TIMEOUT
    error_sleep = env_float("ALERT_WORKER_ERROR_SLEEP_SECS", 5.0)

    logger.info("Alert Dispatch Worker starting")
    initialize_postgres()
    initialize_redis()

    dispatcher = build_dispatcher()

    while True:
        try:
            dispatcher.refresh_if_due()
            with get_redis_client() as client:
                item = client.blpop([EVENTS_KEY], timeout=blpop_timeout)
                if not item:
                    continue
                _, raw = item
                stats = dispatcher.process_raw(client, raw)
                record_metrics(client, stats)
        except KeyboardInterrupt:
            logger.info("Alert Dispatch Worker stopping")
            break
        except Exception as e:
            logger.error(f"Alert worker loop error: {e}", exc_info=True)
            time.sleep(error_sleep)


if __name__ == "__main__":
    run_worker()
