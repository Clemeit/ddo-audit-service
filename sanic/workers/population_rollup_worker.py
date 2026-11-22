"""
Population Rollup Worker - keeps the population_hourly table in step with game_info.

game_info receives one snapshot every 5 minutes. This worker re-rolls the previous and current
UTC hour every POPULATION_ROLLUP_INTERVAL_SECS (default 600), so the current hour is always a
running partial and the previous hour is finalized once its last snapshots land.

On startup it catches up from the newest rollup bucket. If the table is empty it backfills all of
game_info, one month at a time.

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
import services.population_db as population_db  # type: ignore


logger = logging.getLogger("population_rollup_worker")
logging.basicConfig(
    level=os.getenv("WORKER_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)

BACKFILL_CHUNK = timedelta(days=31)


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


def refresh_recent(now: Optional[datetime] = None) -> int:
    """Re-roll the previous and current UTC hour."""
    now = now or datetime.now(timezone.utc)
    current_hour = start_of_hour(now)
    return population_db.rollup_population_hours(
        current_hour - timedelta(hours=1), current_hour + timedelta(hours=1)
    )


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

    try:
        catch_up()
    except Exception as e:
        logger.error(f"Initial catch-up failed: {e}", exc_info=True)

    while True:
        time.sleep(interval)
        try:
            rows = refresh_recent()
            logger.debug(f"Refreshed recent hours: {rows} rows")
        except Exception as e:
            logger.error(f"Rollup refresh failed: {e}", exc_info=True)


if __name__ == "__main__":
    main()
