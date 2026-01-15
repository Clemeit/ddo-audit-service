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
    bulk_insert_quest_sessions,
    mark_activities_as_processed,
    get_unprocessed_quest_session_activities,
    get_quest_by_id,
)
from services.redis import (  # type: ignore
    initialize_redis,
    batch_get_active_quest_session_states,
    batch_update_active_quest_session_states,
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
    except (TypeError, ValueError):
        return default


def env_float(name: str, default: float) -> float:
    """Parse environment variable as float with fallback."""
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def process_character_activities(
    character_id: int,
    activities: List[Tuple[datetime, Optional[int], Optional[bool]]],
    initial_session: Optional[QuestSession],
) -> Tuple[List[Tuple], Optional[QuestSession]]:
    """Process all activities (location + status) for a single character.

    Args:
        character_id: ID of the character
        activities: List of (timestamp, area_id, is_online) sorted chronologically
        initial_session: The character's active session at the start (if any)

    Returns:
        Tuple of (sessions_to_insert, current_session)
        - sessions_to_insert: List of tuples (character_id, quest_id, entry_timestamp, exit_timestamp)
        - current_session: The character's active session at the end (if any)
    """
    sessions_to_insert = []
    current_session = initial_session
    last_area_id = None

    # Get quest's area if there's an initial session
    current_quest_area = None
    if current_session:
        quest = get_quest_by_id(current_session.quest_id)
        if quest:
            current_quest_area = quest.area_id

    for timestamp, area_id, is_online in activities:
        if area_id is not None:
            # Skip duplicate location events (same area)
            if area_id == last_area_id:
                continue

            last_area_id = area_id

            # If character has an active session, check if they're leaving it
            if current_session is not None and current_quest_area != area_id:
                # Only close the session if exit_timestamp would be >= entry_timestamp
                # This prevents negative durations from out-of-order or clock-skewed data
                if timestamp >= current_session.entry_timestamp:
                    # Calculate duration to check 1-day cap (86400 seconds)
                    duration_seconds = (
                        timestamp - current_session.entry_timestamp
                    ).total_seconds()
                    if duration_seconds <= 86400:
                        # Character is leaving the quest area
                        sessions_to_insert.append(
                            (
                                current_session.character_id,
                                current_session.quest_id,
                                current_session.entry_timestamp,
                                timestamp,  # exit_timestamp
                            )
                        )
                    # Sessions exceeding 1 day are silently discarded (not recorded)
                    current_session = None
                    current_quest_area = None
                else:
                    # Skip this activity if it's before the session start (out-of-order data)
                    logger.debug(
                        f"Skipping out-of-order activity for character {character_id}: "
                        f"activity timestamp {timestamp} is before active session start "
                        f"{current_session.entry_timestamp} (quest_id={current_session.quest_id})"
                    )
                    continue

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
            continue

        if is_online is not None:
            if not is_online:
                # Close session on logoff
                if current_session is not None:
                    # Only close the session if logout timestamp >= entry_timestamp
                    if timestamp >= current_session.entry_timestamp:
                        duration_seconds = (
                            timestamp - current_session.entry_timestamp
                        ).total_seconds()
                        if duration_seconds <= 86400:
                            sessions_to_insert.append(
                                (
                                    current_session.character_id,
                                    current_session.quest_id,
                                    current_session.entry_timestamp,
                                    timestamp,  # exit_timestamp (logoff time)
                                )
                            )
                        # Sessions exceeding 1 day are silently discarded (not recorded)
                        current_session = None
                        current_quest_area = None
                    else:
                        # Skip out-of-order logout event
                        logger.debug(
                            f"Skipping out-of-order logout for character {character_id}: "
                            f"logout timestamp {timestamp} is before active session start "
                            f"{current_session.entry_timestamp} (quest_id={current_session.quest_id})"
                        )
            # Reset last_area_id on logout so next location isn't treated as duplicate
            # Also reset on login to ensure location tracking restarts properly
            last_area_id = None

    # Do not persist sessions without an exit_timestamp
    # Active sessions will be tracked via Redis and closed when a leave event is observed

    return sessions_to_insert, current_session


def process_batch(
    last_timestamp: datetime,
    last_ctid: str,
    shard_count: int,
    shard_index: int,
    batch_size: int,
) -> Tuple[datetime, str, int, int]:
    """Process a batch of unprocessed location and status activities.

    Args:
        last_timestamp: Start processing from this timestamp
        last_ctid: Cursor ctid string like "(block,offset)" for keyset pagination
        shard_count: Total number of worker shards
        shard_index: Current shard index (0-based)
        batch_size: Maximum activities to process

    Returns:
        Tuple of (new_last_timestamp, new_last_ctid, activities_processed, sessions_created)
    """
    activities = get_unprocessed_quest_session_activities(
        last_timestamp, last_ctid, shard_count, shard_index, batch_size
    )

    if not activities:
        return last_timestamp, last_ctid, 0, 0

    logger.info(f"Processing {len(activities)} location/status activities")

    # Group activities by character_id, merging location and status activities
    by_character: Dict[int, List[Tuple[datetime, Optional[int], Optional[bool]]]] = (
        defaultdict(list)
    )

    # Add activities (timestamp, area_id, is_online) - type determines which value is set
    activity_ctids: List[str] = []
    for activity in activities:
        if activity.activity_type == "location":
            by_character[activity.character_id].append(
                (activity.timestamp, activity.area_id, None)
            )
        elif activity.activity_type == "status":
            by_character[activity.character_id].append(
                (activity.timestamp, None, activity.is_online)
            )
        else:
            # Defensive; query is filtered so this should not happen
            continue
        activity_ctids.append(activity.ctid_text)

    # Batch fetch initial active sessions for all characters from Redis
    character_ids = list(by_character.keys())
    session_states = batch_get_active_quest_session_states(character_ids)

    character_sessions = {}
    for character_id, state in session_states.items():
        if state:
            # Build a lightweight QuestSession object for processing continuity
            try:
                session = QuestSession(
                    id=None,
                    character_id=character_id,
                    quest_id=int(state.get("quest_id")),
                    entry_timestamp=datetime.fromisoformat(
                        state.get("entry_timestamp")
                    ),
                    exit_timestamp=None,
                    duration_seconds=None,
                    created_at=None,
                )
            except Exception:
                session = None
        else:
            session = None
        character_sessions[character_id] = session

    # Process each character's activities and collect Redis updates
    all_sessions_to_insert = []
    redis_updates_set = {}
    redis_updates_clear = []

    for character_id, char_activities in by_character.items():
        # Sort chronologically by timestamp (should already be sorted, but ensure it)
        char_activities.sort(key=lambda x: x[0])

        initial_session = character_sessions.get(character_id)

        sessions, current_session = process_character_activities(
            character_id, char_activities, initial_session
        )

        all_sessions_to_insert.extend(sessions)

        # Collect Redis updates instead of applying immediately
        if current_session is not None:
            redis_updates_set[character_id] = {
                "quest_id": int(current_session.quest_id),
                "entry_timestamp": current_session.entry_timestamp.isoformat(),
            }
        else:
            redis_updates_clear.append(character_id)

    # Batch apply all Redis updates in a single operation
    if redis_updates_set or redis_updates_clear:
        batch_update_active_quest_session_states(redis_updates_set, redis_updates_clear)

    # Bulk insert quest sessions
    if all_sessions_to_insert:
        logger.info(f"Inserting {len(all_sessions_to_insert)} quest sessions")
        bulk_insert_quest_sessions(all_sessions_to_insert)

    # Mark activities as processed
    if activity_ctids:
        logger.info(f"Marking {len(activity_ctids)} activities as processed")
        mark_activities_as_processed(activity_ctids)

    # Advance keyset cursor to last row returned (results are ordered)
    new_last_timestamp = activities[-1][1]
    new_last_ctid = activities[-1][5]

    return (
        new_last_timestamp,
        new_last_ctid,
        len(activities),
        len(all_sessions_to_insert),
    )


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

    # Initialize Redis (non-fatal if unavailable)
    try:
        initialize_redis()
    except Exception as e:
        logger.warning(
            f"Redis initialization failed, active session tracking across batches disabled: {e}"
        )

    # Start processing from lookback_days ago
    start_time = time.time()
    last_timestamp = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    last_ctid = "(0,0)"

    total_activities_processed = 0
    total_sessions_created = 0
    batch_count = 0

    while True:
        try:
            batch_count += 1
            batch_start_time = time.time()

            logger.info(f"Starting batch {batch_count}...")

            new_last_timestamp, new_last_ctid, activities_count, sessions_count = (
                process_batch(
                    last_timestamp, last_ctid, shard_count, shard_index, batch_size
                )
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
                last_ctid = "(0,0)"
                total_activities_processed = 0
                total_sessions_created = 0
                batch_count = 0
                start_time = time.time()
                continue

            last_timestamp = new_last_timestamp
            last_ctid = new_last_ctid

            # Calculate processing rate
            activities_per_second = (
                activities_count / batch_duration if batch_duration > 0 else 0
            )

            # Build progress log message
            log_parts = [
                f"Batch {batch_count} complete:",
                f"{activities_count:,} activities processed",
                f"{sessions_count:,} sessions created",
                f"batch took {format_duration(batch_duration)}",
                f"rate: {activities_per_second:.1f} activities/sec",
                f"total processed: {total_activities_processed:,} activities",
                f"runtime: {format_duration(total_runtime)}",
            ]

            logger.info(" | ".join(log_parts))

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
