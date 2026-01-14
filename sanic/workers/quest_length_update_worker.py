"""
Quest Length Update Worker - Periodic batch processing to update quest length estimates.

This worker updates the 'length' column in the quests table based on historical
analytics from completed quest sessions. It runs on startup and then repeats daily.

Processing Logic:
1. Fetch all quest IDs from the quests table
2. Process in batches of 50 quests
3. For each quest:
   - Get analytics from quest_sessions (using configurable lookback period)
   - If total_sessions >= 100: extract average_duration_seconds, round and clamp to 0-32767
   - If total_sessions < 100: set length to null
4. Bulk update quest rows with new length values
5. Sleep for configured interval (default 1 day) and repeat

The worker uses environment variables for configuration:
- LOOKBACK_DAYS: Days of historical data to analyze (default: 90)
- UPDATE_INTERVAL_DAYS: Days between update runs (default: 1)
- BATCH_SIZE: Number of quests to process per batch (default: 50)
- MIN_SESSIONS: Minimum completed sessions required to estimate length (default: 100)
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
    get_quest_analytics,
    upsert_quest_metrics_batch,
)
from utils.quest_metrics_calc import get_all_quest_metrics_data  # type: ignore


logger = logging.getLogger("quest_length_update_worker")
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


def clamp_to_smallint(value: float) -> int:
    """Clamp a value to PostgreSQL smallint range (0 to 32767) and round."""
    clamped = max(0, min(32767, value))
    return round(clamped)


def fetch_all_quest_ids() -> List[int]:
    """Fetch all quest IDs from the database."""
    query = "SELECT id FROM public.quests ORDER BY id ASC"
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query)
            rows = cursor.fetchall()
            return [int(row[0]) for row in rows]


def process_quest_batch(
    quest_ids: List[int], lookback_days: int, min_sessions: int = 100
) -> Tuple[List[Tuple[int, int]], List[int], List[int]]:
    """
    Process a batch of quests and determine their length values.

    Args:
        quest_ids: List of quest IDs to process
        lookback_days: Number of days of historical data to analyze
        min_sessions: Minimum sessions required to calculate length

    Returns:
        Tuple of:
        - List of (quest_id, length_seconds) for quests with sufficient data
        - List of quest_ids to set to null (insufficient data)
        - List of quest_ids that failed to process
    """
    updates_with_value: List[Tuple[int, int]] = []
    updates_to_null: List[int] = []
    errors: List[int] = []

    for quest_id in quest_ids:
        try:
            analytics = get_quest_analytics(quest_id, lookback_days)

            if (
                analytics.total_sessions >= min_sessions
                and analytics.average_duration_seconds is not None
            ):
                # Sufficient data - update with clamped value
                length_seconds = clamp_to_smallint(analytics.average_duration_seconds)
                updates_with_value.append((quest_id, length_seconds))
            else:
                # Insufficient data - set to null
                updates_to_null.append(quest_id)

        except Exception as e:
            logger.error(f"Failed to process quest {quest_id}: {e}")
            errors.append(quest_id)

    return updates_with_value, updates_to_null, errors


def bulk_update_quest_lengths(
    updates_with_value: List[Tuple[int, int]], updates_to_null: List[int]
) -> None:
    """
    Bulk update quest length values in the database.

    Args:
        updates_with_value: List of (quest_id, length_seconds) tuples
        updates_to_null: List of quest_ids to set length to null
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # Update quests with calculated length values
            if updates_with_value:
                update_query = "UPDATE public.quests SET length = %s WHERE id = %s"
                cursor.executemany(
                    update_query, [(length, qid) for qid, length in updates_with_value]
                )

            # Set length to null for quests with insufficient data
            if updates_to_null:
                null_query = "UPDATE public.quests SET length = NULL WHERE id = %s"
                cursor.executemany(null_query, [(qid,) for qid in updates_to_null])

            conn.commit()


def run_metrics_update() -> None:
    """
    Compute and update quest metrics for all quests.

    This includes:
    - Heroic and Epic XP/min relative scores
    - Popularity relative score
    - Cached quest analytics (90-day lookback)
    """
    logger.info("Starting quest metrics calculation")
    start_time = time.time()

    try:
        # Calculate metrics for all quests
        metrics_data = get_all_quest_metrics_data()

        if not metrics_data:
            logger.warning("No metrics data generated")
            return

        logger.info(f"Calculated metrics for {len(metrics_data)} quests")

        # Batch upsert to database
        rows_upserted = upsert_quest_metrics_batch(metrics_data)

        elapsed_time = time.time() - start_time
        logger.info(
            f"Quest metrics update complete in {elapsed_time:.1f}s - "
            f"Calculated: {len(metrics_data)} quests, Upserted: {rows_upserted} rows"
        )

    except Exception as e:
        logger.error(f"Failed to update quest metrics: {e}", exc_info=True)


def run_full_update(lookback_days: int, batch_size: int, min_sessions: int) -> None:
    """
    Run a full update cycle for all quests.

    Args:
        lookback_days: Number of days of historical data to analyze
        batch_size: Number of quests to process per batch
        min_sessions: Minimum sessions required to calculate length
    """
    logger.info("Starting full quest length update cycle")
    start_time = time.time()

    # Fetch all quest IDs
    all_quest_ids = fetch_all_quest_ids()
    total_quests = len(all_quest_ids)
    logger.info(f"Found {total_quests} quests to process")

    # Statistics
    total_updated_with_value = 0
    total_updated_to_null = 0
    total_errors = 0

    # Process in batches
    for batch_start in range(0, total_quests, batch_size):
        batch_end = min(batch_start + batch_size, total_quests)
        batch_ids = all_quest_ids[batch_start:batch_end]
        batch_num = (batch_start // batch_size) + 1
        total_batches = (total_quests + batch_size - 1) // batch_size

        logger.info(
            f"Processing batch {batch_num}/{total_batches} "
            f"(quests {batch_start + 1}-{batch_end} of {total_quests})"
        )

        # Process the batch
        updates_with_value, updates_to_null, errors = process_quest_batch(
            batch_ids, lookback_days, min_sessions
        )

        # Bulk update
        try:
            bulk_update_quest_lengths(updates_with_value, updates_to_null)
            total_updated_with_value += len(updates_with_value)
            total_updated_to_null += len(updates_to_null)
            total_errors += len(errors)

            logger.info(
                f"Batch {batch_num} complete: "
                f"{len(updates_with_value)} updated with values, "
                f"{len(updates_to_null)} set to null, "
                f"{len(errors)} errors"
            )
        except Exception as e:
            logger.error(f"Failed to update batch {batch_num}: {e}")
            total_errors += len(batch_ids)

    # Final statistics
    elapsed_time = time.time() - start_time
    logger.info(
        f"Full update cycle complete in {elapsed_time:.1f}s - "
        f"Total: {total_quests} quests, "
        f"Updated with values: {total_updated_with_value}, "
        f"Set to null: {total_updated_to_null}, "
        f"Errors: {total_errors}"
    )


def main():
    """Main worker loop."""
    # Configuration from environment variables
    lookback_days = env_int("LOOKBACK_DAYS", 90)
    update_interval_days = env_int("UPDATE_INTERVAL_DAYS", 1)
    batch_size = env_int("BATCH_SIZE", 50)
    min_sessions = env_int("MIN_SESSIONS", 100)

    logger.info("Quest Length Update Worker starting")
    logger.info(
        f"Configuration: lookback_days={lookback_days}, "
        f"update_interval_days={update_interval_days}, "
        f"batch_size={batch_size}, "
        f"min_sessions={min_sessions}"
    )

    # Initialize database connection
    initialize_postgres()

    # Cold start: Pre-populate metrics table on first run
    logger.info(
        "Starting cold-start: computing metrics and quest lengths for all quests"
    )
    metrics_failed = False
    full_update_failed = False

    # Attempt metrics update - continue even if it fails
    try:
        run_metrics_update()
    except Exception as e:
        logger.error(f"Cold-start metrics update failed: {e}", exc_info=True)
        metrics_failed = True

    # Attempt full length update - continue even if metrics failed
    try:
        run_full_update(lookback_days, batch_size, min_sessions)
    except Exception as e:
        logger.error(f"Cold-start full update failed: {e}", exc_info=True)
        full_update_failed = True

    # Raise if both operations failed
    if metrics_failed and full_update_failed:
        raise RuntimeError(
            "Cold-start failed: both metrics update and full update encountered errors"
        )

    # Main loop: repeat at configured interval
    while True:
        # Sleep until next update
        sleep_seconds = update_interval_days * 86400
        next_run = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(
            seconds=sleep_seconds
        )
        logger.info(
            f"Sleeping for {update_interval_days} day(s) until next run at {next_run}"
        )
        time.sleep(sleep_seconds)

        # Attempt metrics update - continue even if it fails
        try:
            run_metrics_update()
        except Exception as e:
            logger.error(f"Update cycle metrics update failed: {e}", exc_info=True)

        # Attempt full length update - continue even if metrics failed
        try:
            run_full_update(lookback_days, batch_size, min_sessions)
        except Exception as e:
            logger.error(f"Update cycle full update failed: {e}", exc_info=True)


if __name__ == "__main__":
    main()
