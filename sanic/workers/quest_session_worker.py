"""
Quest Session Worker - Scheduled batch processing for quest duration tracking.

This worker processes location activities from the character_activity table to track
how long characters spend in quests. It runs on a schedule (e.g., hourly or every 6 hours)
rather than continuously, making it efficient for data that can be up to 1 day stale.

Processing Logic:
1. Fetch unprocessed location activities in batches
2. Group by character_id and process chronologically
3. For each character's activities:
   - Track current active quest session
   - On location change:
     * If in a quest and moving to different area -> close session
     * If moving to a quest area -> create new session
4. Bulk insert new sessions and mark activities as processed

The worker uses sharding to allow horizontal scaling across multiple instances.
"""

import os
import sys
import time
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import Dict, List, Tuple, Optional

# Ensure relative imports work when invoked as module or script
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from services.postgres import (  # type: ignore
    initialize_postgres,
    get_db_connection,
    get_active_quest_session,
    bulk_insert_quest_sessions,
    mark_activities_as_processed,
    get_unprocessed_location_activities,
    get_quest_by_id,
)
from utils.quest_sessions import get_quest_id_for_area  # type: ignore
from models.quest_session import QuestSession  # type: ignore


logger = logging.getLogger("quest_session_worker")
logging.basicConfig(
    level=os.getenv("WORKER_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)


def env_int(name: str, default: int) -> int:
    """Parse environment variable as integer with fallback."""
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def env_float(name: str, default: float) -> float:
    """Parse environment variable as float with fallback."""
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def process_character_activities(
    character_id: int,
    activities: List[Tuple[datetime, int]],
    initial_session: Optional[QuestSession],
) -> Tuple[List[Tuple], List[Tuple]]:
    """Process all activities for a single character and generate session changes.

    Args:
        character_id: ID of the character
        activities: List of (timestamp, area_id) sorted chronologically
        initial_session: The character's active session at the start (if any)

    Returns:
        Tuple of (sessions_to_insert, activities_to_mark)
        - sessions_to_insert: List of tuples (character_id, quest_id, entry_timestamp, exit_timestamp)
        - activities_to_mark: List of tuples (character_id, timestamp) to mark as processed
    """
    sessions_to_insert = []
    activities_to_mark = []
    current_session = initial_session
    last_area_id = None

    # Get quest's area if there's an initial session
    current_quest_area = None
    if current_session:
        quest = get_quest_by_id(current_session.quest_id)
        if quest:
            current_quest_area = quest.area_id

    for timestamp, area_id in activities:
        # Skip duplicate location events (same area)
        if area_id == last_area_id:
            activities_to_mark.append((character_id, timestamp))
            continue

        last_area_id = area_id

        # Determine if we need to close current session and/or open new session
        session_to_close = None
        session_to_open = None

        # If character has an active session, check if they're leaving it
        if current_session is not None and current_quest_area != area_id:
            # Character is leaving the quest area
            sessions_to_insert.append(
                (
                    current_session.character_id,
                    current_session.quest_id,
                    current_session.entry_timestamp,
                    timestamp,  # exit_timestamp
                )
            )
            current_session = None
            current_quest_area = None

        # Check if new area is a quest area
        new_quest_id = get_quest_id_for_area(area_id)
        if new_quest_id is not None:
            # Only create new session if different from current
            if current_session is None or current_session.quest_id != new_quest_id:
                current_session = QuestSession(
                    character_id=character_id,
                    quest_id=new_quest_id,
                    entry_timestamp=timestamp,
                    exit_timestamp=None,
                )
                current_quest_area = area_id

        # Mark this activity as processed
        activities_to_mark.append((character_id, timestamp))

    # If there's still an active session at the end, insert it without exit_timestamp
    if current_session and current_session != initial_session:
        sessions_to_insert.append(
            (
                current_session.character_id,
                current_session.quest_id,
                current_session.entry_timestamp,
                None,  # exit_timestamp (still active)
            )
        )

    return sessions_to_insert, activities_to_mark


def process_batch(
    last_timestamp: datetime,
    shard_count: int,
    shard_index: int,
    batch_size: int,
) -> Tuple[datetime, int, int]:
    """Process a batch of unprocessed location activities.

    Args:
        last_timestamp: Start processing from this timestamp
        shard_count: Total number of worker shards
        shard_index: Current shard index (0-based)
        batch_size: Maximum activities to process

    Returns:
        Tuple of (new_last_timestamp, activities_processed, sessions_created)
    """
    # Fetch unprocessed activities
    activities = get_unprocessed_location_activities(
        last_timestamp, shard_count, shard_index, batch_size
    )

    if not activities:
        return last_timestamp, 0, 0

    logger.info(f"Processing {len(activities)} unprocessed location activities")

    # Group activities by character_id
    by_character: Dict[int, List[Tuple[datetime, int]]] = defaultdict(list)
    for character_id, timestamp, area_id in activities:
        by_character[character_id].append((timestamp, area_id))

    # Get initial active sessions for all characters
    character_sessions = {}
    for character_id in by_character.keys():
        session = get_active_quest_session(character_id)
        character_sessions[character_id] = session

    # Process each character's activities
    all_sessions_to_insert = []
    all_activities_to_mark = []

    for character_id, char_activities in by_character.items():
        # Sort chronologically (should already be sorted, but ensure it)
        char_activities.sort(key=lambda x: x[0])

        initial_session = character_sessions.get(character_id)
        sessions, activities_marked = process_character_activities(
            character_id, char_activities, initial_session
        )

        all_sessions_to_insert.extend(sessions)
        all_activities_to_mark.extend(activities_marked)

    # Bulk insert quest sessions
    if all_sessions_to_insert:
        logger.info(f"Inserting {len(all_sessions_to_insert)} quest sessions")
        bulk_insert_quest_sessions(all_sessions_to_insert)

    # Mark activities as processed
    if all_activities_to_mark:
        logger.info(f"Marking {len(all_activities_to_mark)} activities as processed")
        mark_activities_as_processed(all_activities_to_mark)

    # Update last_timestamp to the latest processed
    new_last_timestamp = (
        max(ts for _, ts in all_activities_to_mark)
        if all_activities_to_mark
        else last_timestamp
    )

    return new_last_timestamp, len(activities), len(all_sessions_to_insert)


def get_estimated_remaining_activities(
    last_timestamp: datetime, shard_count: int, shard_index: int
) -> int:
    """Estimate number of remaining unprocessed activities for this shard."""
    query = """
        SELECT COUNT(*) 
        FROM public.character_activity
        WHERE timestamp > %s
          AND activity_type = 'location'
          AND quest_session_processed = false
          AND (character_id %% %s) = %s
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(query, (last_timestamp, shard_count, shard_index))
                result = cursor.fetchone()
                return result[0] if result else 0
    except Exception as e:
        logger.warning(f"Failed to estimate remaining activities: {e}")
        return -1  # Unknown


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        minutes = seconds / 60
        return f"{minutes:.1f}m"
    else:
        hours = seconds / 3600
        return f"{hours:.1f}h"


def run_worker():
    """Main worker loop - scheduled batch processing."""
    # Configuration from environment
    shard_count = env_int("QUEST_WORKER_SHARDS", 1)
    shard_index = env_int("QUEST_WORKER_INDEX", 0)
    batch_size = env_int("QUEST_WORKER_BATCH_SIZE", 10000)
    lookback_days = env_int("QUEST_WORKER_LOOKBACK_DAYS", 90)
    sleep_between_batches = env_float("QUEST_WORKER_SLEEP_SECS", 1.0)
    idle_sleep = env_float("QUEST_WORKER_IDLE_SECS", 300.0)

    logger.info(
        f"Quest Session Worker starting (shard {shard_index + 1}/{shard_count}, "
        f"batch_size={batch_size}, lookback_days={lookback_days})"
    )

    # Initialize database connection
    initialize_postgres()

    # Start processing from lookback_days ago
    start_time = time.time()
    last_timestamp = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    total_activities_processed = 0
    total_sessions_created = 0
    batch_count = 0

    # Estimate initial workload
    logger.info("Estimating initial workload...")
    initial_estimate = get_estimated_remaining_activities(
        last_timestamp, shard_count, shard_index
    )
    if initial_estimate > 0:
        logger.info(f"Estimated {initial_estimate:,} unprocessed activities to process")

    while True:
        try:
            batch_count += 1
            batch_start_time = time.time()

            logger.info(f"Starting batch {batch_count}...")

            new_last_timestamp, activities_count, sessions_count = process_batch(
                last_timestamp, shard_count, shard_index, batch_size
            )

            batch_duration = time.time() - batch_start_time
            total_activities_processed += activities_count
            total_sessions_created += sessions_count
            total_runtime = time.time() - start_time

            if activities_count == 0:
                logger.info(
                    f"✓ No more unprocessed activities found. "
                    f"Total processed: {total_activities_processed:,} activities, "
                    f"{total_sessions_created:,} sessions created in {batch_count} batches. "
                    f"Total runtime: {format_duration(total_runtime)}. "
                    f"Sleeping for {format_duration(idle_sleep)} before next check..."
                )
                time.sleep(idle_sleep)
                # Reset for next cycle
                last_timestamp = datetime.now(timezone.utc) - timedelta(
                    days=lookback_days
                )
                total_activities_processed = 0
                total_sessions_created = 0
                batch_count = 0
                start_time = time.time()
                continue

            # Calculate processing rate
            avg_time_per_activity = (
                batch_duration / activities_count if activities_count > 0 else 0
            )
            activities_per_second = (
                activities_count / batch_duration if batch_duration > 0 else 0
            )

            # Estimate remaining work
            remaining_estimate = get_estimated_remaining_activities(
                new_last_timestamp, shard_count, shard_index
            )

            # Build progress log message
            log_parts = [
                f"Batch {batch_count} complete:",
                f"{activities_count:,} activities processed",
                f"{sessions_count:,} sessions created",
                f"batch took {format_duration(batch_duration)}",
                f"rate: {activities_per_second:.1f} activities/sec",
            ]

            if remaining_estimate >= 0:
                total_estimate = (
                    initial_estimate
                    if initial_estimate > 0
                    else total_activities_processed + remaining_estimate
                )
                progress_pct = (
                    (total_activities_processed / total_estimate * 100)
                    if total_estimate > 0
                    else 0
                )
                log_parts.append(
                    f"progress: {total_activities_processed:,}/{total_estimate:,} ({progress_pct:.1f}%)"
                )
                log_parts.append(f"remaining: ~{remaining_estimate:,} activities")

                # Estimate time remaining
                if activities_per_second > 0 and remaining_estimate > 0:
                    eta_seconds = remaining_estimate / activities_per_second
                    log_parts.append(f"ETA: ~{format_duration(eta_seconds)}")

            log_parts.append(f"runtime: {format_duration(total_runtime)}")

            logger.info(" | ".join(log_parts))

            last_timestamp = new_last_timestamp

            # Small sleep between batches to avoid overwhelming the database
            if sleep_between_batches > 0:
                time.sleep(sleep_between_batches)

        except KeyboardInterrupt:
            logger.info("Worker interrupted by user")
            break
        except Exception as e:
            logger.error(f"Error processing batch: {e}", exc_info=True)
            time.sleep(5)  # Wait before retrying

    logger.info(
        f"Quest Session Worker stopped. Total: {total_activities_processed:,} activities, "
        f"{total_sessions_created:,} sessions, {batch_count} batches, "
        f"runtime: {format_duration(time.time() - start_time)}"
    )


if __name__ == "__main__":
    run_worker()


if __name__ == "__main__":
    run_worker()
