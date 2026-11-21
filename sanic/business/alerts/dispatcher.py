"""Matching queued alert events against users' rules and handing the results to the channels.

Used by ``workers/alert_dispatch_worker.py``. Kept free of process setup so it can be tested with fakes.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import orjson
from business.alerts import registry
from business.alerts.base import AlertChannel, AlertRule
from models.redis import RedisKeys
from pydantic import ValidationError

logger = logging.getLogger(__name__)

USER_CAP = 10  # alerts per user per window
USER_CAP_WINDOW_SECONDS = 10 * 60
METRICS_KEY = RedisKeys.ALERT_METRICS.value

RuleLoader = Callable[[str], list[dict]]
QuietHoursLoader = Callable[[], list[dict]]
# Alert types that ignore quiet hours: the user pressed "send test" and expects to see it.
QUIET_HOURS_EXEMPT_TYPES = frozenset({"test"})
RULES_VERSION_KEY = RedisKeys.ALERT_RULES_VERSION.value


def parse_rule(row: dict) -> AlertRule | None:
    """Turn a DB row into an AlertRule, or None if its type is unknown or its criteria no longer validate."""
    alert_type = registry.get_type(row.get("type"))
    if alert_type is None or alert_type.Criteria is None:
        return None
    try:
        criteria = alert_type.Criteria.model_validate(row.get("criteria") or {})
    except ValidationError as e:
        logger.warning(f"Skipping alert rule {row.get('id')}: invalid criteria ({e})")
        return None
    return AlertRule(
        id=row["id"],
        user_id=row["user_id"],
        type=row["type"],
        criteria=criteria,
        expires_at=row.get("expires_at"),
    )


def is_quiet(window: dict, now_utc: datetime) -> bool:
    """Whether ``now_utc`` falls inside a quiet-hours window.

    ``window`` has ``quiet_start_minute``, ``quiet_end_minute`` (minutes after local midnight) and
    ``timezone`` (IANA). The window starts inclusive and ends exclusive, and may cross midnight
    (start > end). Anything unusable counts as not quiet: a bad row should never silence alerts.
    """
    try:
        start = int(window["quiet_start_minute"])
        end = int(window["quiet_end_minute"])
        local = now_utc.astimezone(ZoneInfo(window["timezone"]))
    except Exception:
        return False
    if start == end:
        return False
    minute = local.hour * 60 + local.minute
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end


class AlertDispatcher:
    def __init__(
        self,
        load_rules: RuleLoader,
        channels: list[AlertChannel],
        *,
        refresh_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        rules_version: Callable[[], Any] | None = None,
        load_quiet_hours: QuietHoursLoader | None = None,
    ) -> None:
        self._load_rules = load_rules
        self._load_quiet_hours = load_quiet_hours
        self._quiet_hours: dict[int, dict] = {}
        # Bumped by the API when rules change, so new rules apply right away instead of at the next
        # periodic refresh (which still runs, e.g. to drop rules that have expired).
        self._get_rules_version = rules_version
        self._rules_version: Any = None
        self.channels = channels
        self._refresh_seconds = refresh_seconds
        self._clock = clock
        self._now = now
        self._indexes: dict[str, Any] = {}
        self._last_refresh: float | None = None

    # ----- rule indexes -----

    def refresh_if_due(self) -> None:
        version = self._read_rules_version()
        if (
            self._last_refresh is None
            or self._clock() - self._last_refresh >= self._refresh_seconds
            or version != self._rules_version
        ):
            self.refresh()
            self._rules_version = version

    def _read_rules_version(self) -> Any:
        if self._get_rules_version is None:
            return None
        try:
            return self._get_rules_version()
        except Exception as e:
            logger.warning(f"Could not read the alert rules version: {e}")
            return self._rules_version

    def refresh(self) -> None:
        now = self._now()
        indexes: dict[str, Any] = {}
        for alert_type in registry.all_types():
            alert_type.refresh()
            if not alert_type.user_creatable:
                indexes[alert_type.name] = alert_type.build_index([])
                continue
            try:
                rows = self._load_rules(alert_type.name)
            except Exception as e:
                logger.error(f"Could not load {alert_type.name} rules: {e}")
                if alert_type.name in self._indexes:
                    indexes[alert_type.name] = self._indexes[alert_type.name]
                continue
            rules = [r for r in (parse_rule(row) for row in rows) if r]
            rules = [r for r in rules if r.is_active(now)]
            indexes[alert_type.name] = alert_type.build_index(rules)
        self._indexes = indexes
        self._refresh_quiet_hours()
        self._last_refresh = self._clock()

    def _refresh_quiet_hours(self) -> None:
        if self._load_quiet_hours is None:
            return
        try:
            rows = self._load_quiet_hours()
        except Exception as e:
            # Keep the last good windows rather than suddenly alerting people mid-night.
            logger.error(f"Could not load alert quiet hours: {e}")
            return
        self._quiet_hours = {int(row["user_id"]): row for row in rows}

    # ----- events -----

    def process_raw(self, client, raw: bytes | str) -> Counter:
        try:
            event = orjson.loads(raw)
        except orjson.JSONDecodeError:
            logger.warning("Dropping malformed alert event")
            return Counter(malformed=1)
        return self.process_event(client, event)

    def process_event(self, client, event: dict) -> Counter:
        stats: Counter = Counter(events=1)
        alert_type = registry.get_type(event.get("type"))
        if alert_type is None:
            stats["unknown_type"] += 1
            return stats
        data = event.get("data") or {}

        try:
            matches = alert_type.match(data, self._indexes.get(alert_type.name))
        except Exception as e:
            logger.error(f"{alert_type.name} match failed: {e}")
            stats["errors"] += 1
            return stats

        now = self._now()
        # One alert per user per event, however many of their rules matched.
        by_user: dict[int, AlertRule] = {}
        for rule in matches:
            if rule.is_active(now) and rule.user_id not in by_user:
                by_user[rule.user_id] = rule
        stats["matched"] += len(by_user)

        if by_user:
            dedupe_key = alert_type.dedupe_key(data)
        for user_id, rule in by_user.items():
            sent_key = RedisKeys.ALERT_SENT.value.format(
                user_id=user_id, dedupe_key=dedupe_key
            )
            if not client.set(sent_key, 1, nx=True, ex=alert_type.dedupe_ttl_secs):
                stats["deduped"] += 1
                continue
            if alert_type.name not in QUIET_HOURS_EXEMPT_TYPES:
                window = self._quiet_hours.get(user_id)
                if window and is_quiet(window, now):
                    # After the dedupe marker so the same event is not sent when quiet hours end, and
                    # before the cap so skipped alerts do not use up the user's allowance.
                    stats["quiet_hours"] += 1
                    continue
            if self._over_cap(client, user_id):
                stats["capped"] += 1
                continue
            try:
                alert = alert_type.render(data, rule)
            except Exception as e:
                logger.error(f"{alert_type.name} render failed: {e}")
                stats["errors"] += 1
                continue
            for channel in self.channels:
                try:
                    if channel.deliver(client, user_id, alert):
                        stats[f"delivered_{channel.name}"] += 1
                except Exception as e:
                    logger.error(f"{channel.name} delivery to user {user_id} failed: {e}")
                    stats[f"failed_{channel.name}"] += 1
        return stats

    @staticmethod
    def _over_cap(client, user_id: int) -> bool:
        key = RedisKeys.ALERT_USER_CAP.value.format(user_id=user_id)
        count = client.incr(key)
        if count == 1:
            client.expire(key, USER_CAP_WINDOW_SECONDS)
        return count > USER_CAP


def record_metrics(client, stats: Counter) -> None:
    if not stats:
        return
    try:
        pipe = client.pipeline(transaction=False)
        for field, value in stats.items():
            if value:
                pipe.hincrby(METRICS_KEY, field, value)
        pipe.execute()
    except Exception as e:
        logger.warning(f"Could not record alert metrics: {e}")


async def async_bump_rules_version() -> None:
    """Tell the worker to reload rules now. Failure only delays new rules until the next refresh."""
    import services.redis as redis_client

    try:
        client = await redis_client.get_async_redis_client()
        await client.incr(RULES_VERSION_KEY)
    except Exception as e:
        logger.warning(f"Could not bump the alert rules version: {e}")


async def async_get_metrics() -> dict[str, int]:
    """Counters since the metrics hash was created (for GET /v1/service/metrics)."""
    import services.redis as redis_client

    client = await redis_client.get_async_redis_client()
    raw = await client.hgetall(METRICS_KEY)
    queued = await client.llen(RedisKeys.ALERT_EVENTS.value)
    metrics = {
        (k.decode() if isinstance(k, bytes) else k): int(v) for k, v in raw.items()
    }
    metrics["queued"] = int(queued)
    return metrics
