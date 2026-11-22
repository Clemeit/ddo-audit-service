"""
Population Rollup Worker - keeps the population_hourly table in step with game_info.

game_info receives one snapshot every 5 minutes. Every POPULATION_ROLLUP_INTERVAL_SECS
(default 600) this worker re-rolls from the hour of its last successful tick through the
current hour, so the current hour is a running partial, the previous hour is finalized once its
last snapshots land, and a database outage that outlasts a tick is filled in by the next success.

On startup it syncs, retrying until that succeeds:
- catches up from the newest rollup bucket (or backfills all of game_info, one month at a time,
  when the table is empty), then
- repairs any hour that has game_info snapshots but no rollup rows (e.g. a backfill that died
  partway, or an outage while the worker was down).
Whenever it fills in history it clears the long-TTL report caches built from this table.

To force a rebuild (e.g. after changing the rollup logic), run a one-off inside any sanic-image
container; rows are upserted, so this is safe alongside the running worker:
    docker exec <container> python -m workers.population_rollup_worker --rebuild [--since YYYY-MM-DD]

Environment variables:
- POPULATION_ROLLUP_INTERVAL_SECS: seconds between refreshes (default: 600)
- WORKER_LOG_LEVEL: logging level (default: INFO)
"""

import argparse
import os
import sys
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

# Ensure relative imports work when invoked as module or script
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from services.postgres import initialize_postgres  # type: ignore
from services.redis import initialize_redis  # type: ignore
import services.population_db as population_db  # type: ignore
from utils.population import invalidate_long_range_population_caches  # type: ignore


logger = logging.getLogger("population_rollup_worker")
logging.basicConfig(
    level=os.getenv("WORKER_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)

BACKFILL_CHUNK = timedelta(days=31)
MAX_STARTUP_RETRY_SECS = 300


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def start_of_hour(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def rollup_range(start: datetime, end: datetime, chunk: timedelta = BACKFILL_CHUNK) -> int:
    """Roll up [start, end) in chunks so a long backfill never holds one huge transaction."""
    written = 0
    cursor = start_of_hour(start)
    while cursor < end:
        chunk_end = min(cursor + chunk, end)
        rows = population_db.rollup_population_hours(cursor, chunk_end)
        written += rows
        logger.info(f"Rolled up {cursor.isoformat()} -> {chunk_end.isoformat()}: {rows} rows")
        cursor = chunk_end
    return written


def catch_up(now: Optional[datetime] = None) -> int:
    """Bring population_hourly up to date from wherever it left off (or from the beginning)."""
    now = now or datetime.now(timezone.utc)
    end = start_of_hour(now) + timedelta(hours=1)
    latest = population_db.get_latest_rollup_bucket()
    if latest is not None:
        # Redo the newest stored hour: it may have been a partial when it was written.
        start = latest
    else:
        start = population_db.get_first_game_info_timestamp()
        if start is None:
            logger.info("game_info is empty; nothing to roll up yet")
            return 0
        logger.info(f"population_hourly is empty; backfilling from {start.isoformat()}")
    return rollup_range(start, end)


def hour_ranges(hours: list[datetime]) -> list[tuple[datetime, datetime]]:
    """Collapse sorted hour starts into contiguous [start, end) ranges."""
    ranges: list[tuple[datetime, datetime]] = []
    for hour in hours:
        hour = start_of_hour(hour)
        if ranges and ranges[-1][1] == hour:
            ranges[-1] = (ranges[-1][0], hour + timedelta(hours=1))
        else:
            ranges.append((hour, hour + timedelta(hours=1)))
    return ranges


def repair_gaps(now: Optional[datetime] = None) -> int:
    """Roll up every past hour that has game_info snapshots but no rollup rows."""
    now = now or datetime.now(timezone.utc)
    missing = population_db.get_missing_rollup_hours(start_of_hour(now))
    if not missing:
        return 0
    ranges = hour_ranges(missing)
    logger.warning(f"Repairing {len(missing)} missing hours in {len(ranges)} ranges")
    return sum(rollup_range(start, end) for start, end in ranges)


def sync(now: Optional[datetime] = None) -> int:
    """Startup: catch up, then repair holes anywhere in history."""
    now = now or datetime.now(timezone.utc)
    return catch_up(now) + repair_gaps(now)


def refresh_since(last_success: Optional[datetime], now: Optional[datetime] = None) -> int:
    """Re-roll from the hour of the last successful tick (which may have been partial then)
    through the current hour; in steady state that's the previous and current hour."""
    now = now or datetime.now(timezone.utc)
    current_hour = start_of_hour(now)
    start = current_hour - timedelta(hours=1)
    if last_success is not None:
        start = min(start, start_of_hour(last_success))
    return rollup_range(start, current_hour + timedelta(hours=1))


def clear_caches() -> None:
    try:
        removed = invalidate_long_range_population_caches()
        logger.info(f"Cleared {removed} cached population reports")
    except Exception as e:
        logger.error(f"Failed to clear population caches: {e}", exc_info=True)


def rebuild(since: Optional[str]) -> None:
    """One-off: re-roll everything from ``since`` (UTC date) or the first game_info row."""
    initialize_postgres()
    if since:
        start = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    else:
        start = population_db.get_first_game_info_timestamp()
        if start is None:
            logger.info("game_info is empty; nothing to rebuild")
            return
    end = start_of_hour(datetime.now(timezone.utc)) + timedelta(hours=1)
    written = rollup_range(start, end)
    logger.info(f"Rebuild done: {written} rows for {start.isoformat()} -> {end.isoformat()}")
    initialize_redis()
    clear_caches()


def main():
    parser = argparse.ArgumentParser(description="Population rollup worker")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="re-roll history once and exit instead of running the worker loop",
    )
    parser.add_argument("--since", help="with --rebuild: UTC date (YYYY-MM-DD) to start from")
    args = parser.parse_args()
    if args.rebuild:
        rebuild(args.since)
        return

    interval = max(60, env_int("POPULATION_ROLLUP_INTERVAL_SECS", 600))
    logger.info(f"Population Rollup Worker starting (interval={interval}s)")

    initialize_postgres()
    initialize_redis()

    # Don't start ticking until history is in place: ticks advance MAX(bucket), so a backfill
    # abandoned here would otherwise only be found by the gap repair on the next restart.
    retry = 30
    while True:
        try:
            if sync() > 0:
                clear_caches()
            break
        except Exception as e:
            logger.error(f"Startup sync failed, retrying in {retry}s: {e}", exc_info=True)
            time.sleep(retry)
            retry = min(retry * 2, MAX_STARTUP_RETRY_SECS)

    last_success = datetime.now(timezone.utc)
    while True:
        time.sleep(interval)
        now = datetime.now(timezone.utc)
        try:
            caught_up = start_of_hour(last_success) < start_of_hour(now) - timedelta(hours=1)
            rows = refresh_since(last_success, now)
            logger.debug(f"Refreshed recent hours: {rows} rows")
            if caught_up:
                logger.info(f"Caught up after missed ticks since {last_success.isoformat()}")
                clear_caches()
            last_success = now
        except Exception as e:
            logger.error(f"Rollup refresh failed: {e}", exc_info=True)


if __name__ == "__main__":
    main()
