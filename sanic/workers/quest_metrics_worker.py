"""
Quest Metrics Worker - Periodic batch processing to calculate quest metrics and update length estimates.

This worker runs daily (at midnight UTC) and performs the following operations:

Quest Metrics Update (Two-Pass Approach):
   Pass 1: Fetch analytics data from quest_sessions for all quests
           - Calculates total_sessions, average_duration, histograms, etc.
           - Stores intermediate results in Redis cache
   Pass 2: Calculate relative metrics using cached analytics
           - Computes heroic/epic XP per minute relative scores
           - Computes popularity relative scores by comparing to peer quests
   Final: Bulk write all metrics to quest_metrics table

Quest Length Update:
   - Extracted from computed analytics data (no separate queries)
   - Calculates average_duration_seconds for each quest with sufficient data
   - Batch updates 'length' column in quests table

The worker uses environment variables for configuration:
- LOOKBACK_DAYS: Days of historical data to analyze (default: 90)
- UPDATE_INTERVAL_DAYS: Days between update runs (default: 1)
- BATCH_SIZE: Number of quests to process per batch (default: 50)
- MIN_SESSIONS: Minimum completed sessions required to estimate length (default: 100)
- QUEST_METRICS_DELAY_SECS: Delay between quest processing in Pass 1 (default: 0.1)
"""

import os
import sys
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Tuple

# Ensure relative imports work when invoked as module or script
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from services.postgres import (  # type: ignore
    initialize_postgres,
    get_db_connection,
    get_all_quests,
    upsert_quest_metrics_batch,
)
from services.redis import initialize_redis  # type: ignore
from utils.quest_metrics_calc import get_all_quest_metrics_data  # type: ignore


logger = logging.getLogger("quest_metrics_worker")
logging.basicConfig(
    level=os.getenv("WORKER_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)


def env_int(name: str, default: int) -> int:
    """Parse integer from environment variable with fallback to default."""
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def env_float(name: str, default: float) -> float:
    """Parse float from environment variable with fallback to default."""
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def clamp_to_smallint(value: float) -> int:
    """Clamp a value to PostgreSQL smallint range (0 to 32767) and round."""
    clamped = max(0, min(32767, value))
    return round(clamped)


def bulk_update_quest_lengths(
    updates_with_value: List[Tuple[int, int]],
    updates_to_null: List[int],
    batch_size: int = 50,
) -> None:
    """
    Bulk update quest length values in the database in batches.

    Args:
        updates_with_value: List of (quest_id, length_seconds) tuples
        updates_to_null: List of quest_ids to set length to null
        batch_size: Number of quests to update per batch
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # Update quests with calculated length values in batches
            if updates_with_value:
                update_query = "UPDATE public.quests SET length = %s WHERE id = %s"
                for i in range(0, len(updates_with_value), batch_size):
                    batch = updates_with_value[i : i + batch_size]
                    cursor.executemany(
                        update_query, [(length, qid) for qid, length in batch]
                    )
                    conn.commit()
                    logger.debug(f"Updated {len(batch)} quests with length values")

            # Set length to null for quests with insufficient data in batches
            if updates_to_null:
                null_query = "UPDATE public.quests SET length = NULL WHERE id = %s"
                for i in range(0, len(updates_to_null), batch_size):
                    batch = updates_to_null[i : i + batch_size]
                    cursor.executemany(null_query, [(qid,) for qid in batch])
                    conn.commit()
                    logger.debug(f"Set {len(batch)} quests length to null")


def extract_and_batch_quest_lengths(
    metrics_data: dict, all_quest_ids: List[int], min_sessions: int = 100
) -> Tuple[List[Tuple[int, int]], List[int]]:
    """
    Extract length estimates from metrics_data and identify quests with insufficient data.

    Quests in metrics_data have sufficient sessions and their average_duration is extracted.
    Quests not in metrics_data (skipped in Pass 2 due to insufficient sessions or errors)
    will have their length set to null to avoid stale data.

    Args:
        metrics_data: Dictionary mapping quest_id to metrics dict with analytics_data
        all_quest_ids: List of all quest IDs in the database
        min_sessions: Minimum sessions required to estimate length

    Returns:
        Tuple of:
        - List of (quest_id, length_seconds) for quests with sufficient data
        - List of quest_ids to set to null (insufficient data or not in metrics_data)
    """
    updates_with_value: List[Tuple[int, int]] = []
    updates_to_null: List[int] = []

    # Process quests in metrics_data
    for quest_id, metrics in metrics_data.items():
        try:
            analytics_data = metrics.get("analytics_data", {})
            total_sessions = analytics_data.get("total_sessions", 0)
            average_duration = analytics_data.get("average_duration_seconds")

            if total_sessions >= min_sessions and average_duration is not None:
                # Sufficient data - update with clamped value
                length_seconds = clamp_to_smallint(average_duration)
                updates_with_value.append((quest_id, length_seconds))
            else:
                # Insufficient data - set to null
                updates_to_null.append(quest_id)

        except Exception as e:
            logger.error(f"Failed to extract length for quest {quest_id}: {e}")
            updates_to_null.append(quest_id)

    # Identify quests not in metrics_data (skipped in Pass 2 due to insufficient sessions)
    # and set their length to null to avoid stale data
    quests_in_metrics = set(metrics_data.keys())
    for quest_id in all_quest_ids:
        if quest_id not in quests_in_metrics:
            updates_to_null.append(quest_id)

    return updates_with_value, updates_to_null


def run_metrics_update(batch_size: int = 50, min_sessions: int = 100) -> None:
    """
    Compute and update quest metrics for all quests using two-pass approach.
    Also updates quest lengths based on the computed analytics.

    Pass 1: Fetch analytics data (total sessions, durations, histograms) for all quests
            and cache in Redis
    Pass 2: Calculate relative metrics (XP/min, popularity) using cached analytics
    Final: Bulk upsert all metrics to quest_metrics table
    Length Update: Extract average_duration_seconds from analytics and batch update quest lengths

    Metrics calculated:
    - Heroic and Epic XP/min relative scores (0-1 normalized vs peers)
    - Popularity relative score (0-1 normalized vs peers)
    - Cached quest analytics (90-day lookback)

    Args:
        batch_size: Number of quests to update per database batch
        min_sessions: Minimum sessions required to calculate length
    """
    logger.info("Starting quest metrics calculation")
    start_time = time.time()

    try:
        all_quests = get_all_quests()

        if not all_quests:
            logger.warning("No quests found in database")
            return

        all_quest_ids = [quest.id for quest in all_quests]

        # Calculate metrics for all quests
        metrics_data = get_all_quest_metrics_data(all_quests)

        if not metrics_data:
            logger.warning("No metrics data generated")
            return

        logger.info(f"Calculated metrics for {len(metrics_data)} quests")

        # Batch upsert metrics to database
        rows_upserted = upsert_quest_metrics_batch(metrics_data)

        # Extract and batch update quest lengths from computed metrics
        logger.info("Extracting quest lengths from computed analytics")
        updates_with_value, updates_to_null = extract_and_batch_quest_lengths(
            metrics_data, all_quest_ids, min_sessions
        )

        if updates_with_value or updates_to_null:
            bulk_update_quest_lengths(updates_with_value, updates_to_null, batch_size)
            logger.info(
                f"Quest lengths updated: {len(updates_with_value)} with values, "
                f"{len(updates_to_null)} set to null"
            )

        elapsed_time = time.time() - start_time
        logger.info(
            f"Quest metrics and lengths update complete in {elapsed_time:.1f}s - "
            f"Calculated: {len(metrics_data)} quests, Upserted metrics: {rows_upserted} rows, "
            f"Updated lengths: {len(updates_with_value) + len(updates_to_null)}"
        )

    except Exception as e:
        logger.error(f"Failed to update quest metrics: {e}", exc_info=True)


def main():
    """Main worker loop."""
    # Configuration from environment variables
    lookback_days = env_int("LOOKBACK_DAYS", 90)
    batch_size = env_int("BATCH_SIZE", 50)
    min_sessions = env_int("MIN_SESSIONS", 100)

    logger.info("Quest Metrics Worker starting")
    logger.info(
        f"Configuration: lookback_days={lookback_days}, "
        f"batch_size={batch_size}, "
        f"min_sessions={min_sessions}"
    )

    # Initialize database and Redis connections
    initialize_postgres()
    initialize_redis()

    # Main loop: repeat daily at midnight UTC
    while True:
        # Attempt metrics and length update - continue even if it fails
        try:
            run_metrics_update(batch_size, min_sessions)
        except Exception as e:
            logger.error(
                f"Update cycle metrics and length update failed: {e}", exc_info=True
            )

        # Calculate time until next midnight (UTC)
        now = datetime.now(timezone.utc)
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
            days=1
        )
        seconds_until_midnight = (tomorrow - now).total_seconds()
        logger.info(
            f"Next run at {tomorrow.strftime('%Y-%m-%d %H:%M:%S')}. Sleeping for {seconds_until_midnight:.0f} seconds."
        )
        # Sleep until next update
        time.sleep(seconds_until_midnight)


if __name__ == "__main__":
    main()
