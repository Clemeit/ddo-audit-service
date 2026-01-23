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
    get_unprocessed_quest_activities,
    get_latest_character_states,
    get_all_quests,
    truncate_quest_sessions,
)
from services.redis import (  # type: ignore
    initialize_redis,
    batch_get_active_quest_session_states,
    batch_update_active_quest_session_states,
    get_quest_worker_checkpoint,
    set_quest_worker_checkpoint,
    clear_all_active_quest_sessions,
)
from models.quest_session import QuestSession  # type: ignore


logger = logging.getLogger("quest_session_worker")
logging.basicConfig(
    level=os.getenv("WORKER_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)

# Preloaded quest lookup maps (area_id -> quest_id, quest_id -> area_id)
QUEST_AREA_TO_ID: Dict[int, int] = {}
QUEST_ID_TO_AREA: Dict[int, int] = {}
# Quest IDs to exclude due to non-unique area mappings
EXCLUDED_QUEST_IDS: set = set()


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


def load_quest_area_maps() -> None:
    """Load quest metadata once to avoid per-activity DB lookups.

    Also identifies and excludes quests with non-unique area IDs, since we cannot
    reliably determine which quest a character entered based on area_id alone.
    """
    global QUEST_AREA_TO_ID, QUEST_ID_TO_AREA, EXCLUDED_QUEST_IDS
    quests = get_all_quests()
    area_to_id: Dict[int, int] = {}
    id_to_area: Dict[int, int] = {}
    area_quest_map: Dict[int, List[int]] = {}  # Track all quests per area

    # First pass: collect all quests per area
    for quest in quests:
        if quest.area_id is not None:
            if quest.area_id not in area_quest_map:
                area_quest_map[quest.area_id] = []
            area_quest_map[quest.area_id].append(quest.id)

    # Identify areas with multiple quests and mark those quest IDs as excluded
    excluded_ids = set()
    for _, quest_ids in area_quest_map.items():
        if len(quest_ids) > 1:
            excluded_ids.update(quest_ids)

    # Second pass: build lookup maps excluding ambiguous quests
    for quest in quests:
        if quest.area_id is not None and quest.id not in excluded_ids:
            area_to_id[quest.area_id] = quest.id
            id_to_area[quest.id] = quest.area_id

    QUEST_AREA_TO_ID = area_to_id
    QUEST_ID_TO_AREA = id_to_area
    EXCLUDED_QUEST_IDS = excluded_ids

    logger.info(
        f"Loaded quest lookup maps: {len(QUEST_AREA_TO_ID)} areas -> quests, "
        f"excluding {len(excluded_ids)} quests with non-unique area IDs"
    )
    if excluded_ids:
        logger.warning(
            f"Excluded {len(excluded_ids)} quests due to non-unique area mappings"
        )


def process_character_activities(
    character_id: int,
    activities: List[Tuple[datetime, str, Optional[int], Optional[bool], dict]],
    initial_session: Optional[QuestSession],
    initial_total_level: Optional[int] = None,
    initial_classes: Optional[list] = None,
    initial_group_id: Optional[int] = None,
) -> Tuple[List[dict], Optional[QuestSession], bool]:
    """Process all activities for a single character and generate session changes.

    Args:
        character_id: ID of the character
        activities: List of (timestamp, activity_type, area_id, is_active, data) sorted chronologically
        initial_session: The character's active session at the start (if any)
        initial_total_level: Starting total level (from latest TOTAL_LEVEL activity)
        initial_classes: Starting classes (from latest TOTAL_LEVEL activity)
        initial_group_id: Starting group ID (from latest GROUP_ID activity)

    Returns:
        Tuple of (sessions_to_insert, final_session, status_seen)
        - sessions_to_insert: List of dicts with session data including entry state
        - final_session: Active session at end of processing (None if none)
        - status_seen: True if any status event (login/logout) was processed
    """
    sessions_to_insert: list[dict] = []
    current_session = initial_session
    last_area_id = None
    status_seen = False

    # Track character state: level, classes, and group_id
    # Initialize with the latest known state (may be from weeks/months ago)
    character_total_level: Optional[int] = initial_total_level
    character_classes: Optional[list] = initial_classes
    character_group_id: Optional[int] = initial_group_id

    # Get quest's area if there's an initial session
    current_quest_area = None
    if current_session:
        current_quest_area = QUEST_ID_TO_AREA.get(current_session.quest_id)

    for timestamp, activity_type, area_id, is_active, activity_data in activities:
        # Update character state from TOTAL_LEVEL and GROUP_ID activities
        if activity_type == "total_level" and activity_data:
            total_level_val = activity_data.get("total_level")
            if total_level_val is not None:
                character_total_level = total_level_val
            classes_val = activity_data.get("classes")
            if classes_val is not None:
                character_classes = classes_val

        if activity_type == "group_id" and activity_data:
            group_id_val = activity_data.get("value")
            if group_id_val is not None:
                character_group_id = group_id_val

        # Handle status events (login/logout) first
        if activity_type == "status":
            # Discard any active session without persisting it when a status change occurs
            current_session = None
            current_quest_area = None
            last_area_id = None  # reset dedup tracking after status change
            status_seen = True
            continue

        # From here on, only location events are expected
        if area_id == last_area_id:
            # Skip duplicate location events (same area)
            continue

        last_area_id = area_id

        # If character has an active session, check if they're leaving it
        if current_session is not None and current_quest_area != area_id:
            # Only close the session if exit_timestamp would be >= entry_timestamp
            # This prevents negative durations from out-of-order or clock-skewed data
            if timestamp >= current_session.entry_timestamp:
                sessions_to_insert.append(
                    {
                        "character_id": current_session.character_id,
                        "quest_id": current_session.quest_id,
                        "entry_timestamp": current_session.entry_timestamp,
                        "exit_timestamp": timestamp,
                        "entry_total_level": current_session.entry_total_level,
                        "entry_classes": current_session.entry_classes,
                        "entry_group_id": current_session.entry_group_id,
                    }
                )
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
        new_quest_id = QUEST_AREA_TO_ID.get(area_id) if area_id is not None else None
        if new_quest_id is not None:
            # Only create new session if different from current
            if current_session is None or current_session.quest_id != new_quest_id:
                current_session = QuestSession(
                    character_id=character_id,
                    quest_id=new_quest_id,
                    entry_timestamp=timestamp,
                    exit_timestamp=None,
                    entry_total_level=character_total_level,
                    entry_classes=character_classes,
                    entry_group_id=character_group_id,
                )
                current_quest_area = area_id

    # Do not persist sessions without an exit_timestamp
    # Active sessions will be tracked via Redis and closed when a leave event is observed

    return sessions_to_insert, current_session, status_seen


def process_batch(
    last_timestamp: datetime,
    max_character_id_at_timestamp: int,
    batch_size: int,
    time_window_hours: int = 24,
) -> Tuple[datetime, int, int, int]:
    """Process a batch of quest-related activities using composite checkpoint.

    Args:
        last_timestamp: Start processing from this timestamp
        max_character_id_at_timestamp: Max character_id at last_timestamp (for pagination)
        batch_size: Maximum activities to process (safety limit)
        time_window_hours: Time window in hours for time-based batching

    Returns:
        Tuple of (new_last_timestamp, max_char_id_at_new_timestamp, activities_processed, sessions_created)
    """
    # Fetch activities using composite checkpoint
    logger.debug(
        f"Fetching activities from {last_timestamp} (max_char_id={max_character_id_at_timestamp})"
    )
    activities = get_unprocessed_quest_activities(
        last_timestamp, max_character_id_at_timestamp, batch_size, time_window_hours
    )

    if not activities:
        return last_timestamp, max_character_id_at_timestamp, 0, 0

    logger.info(f"Processing {len(activities)} quest activities")

    # Group activities by character_id
    by_character: Dict[
        int, List[Tuple[datetime, str, Optional[int], Optional[bool], dict]]
    ] = defaultdict(list)
    for (
        character_id,
        timestamp,
        activity_type,
        area_id,
        is_active,
        activity_data,
    ) in activities:
        by_character[character_id].append(
            (timestamp, activity_type, area_id, is_active, activity_data)
        )

    # Batch fetch initial active sessions for all characters from Redis
    character_ids = list(by_character.keys())
    session_states = batch_get_active_quest_session_states(character_ids)

    character_sessions = {}
    for character_id, state in session_states.items():
        if state:
            # Build a lightweight QuestSession object for processing continuity
            # Include entry state fields so they're preserved when session is later closed
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
                    entry_total_level=state.get("entry_total_level"),
                    entry_classes=state.get("entry_classes"),
                    entry_group_id=state.get("entry_group_id"),
                )
            except Exception:
                session = None
        else:
            session = None
        character_sessions[character_id] = session

    # Fetch the most recent TOTAL_LEVEL and GROUP_ID activities for all characters
    # This ensures we have current state even if activities are sparse (weeks/months old)
    character_ids_list = list(by_character.keys())
    latest_states = get_latest_character_states(character_ids_list)

    # Track which characters have TOTAL_LEVEL and GROUP_ID in current batch vs from history
    characters_with_recent_total_level = set()
    characters_with_recent_group_id = set()

    for character_id, char_activities in by_character.items():
        for _, activity_type, _, _, _ in char_activities:
            if activity_type == "total_level":
                characters_with_recent_total_level.add(character_id)
            elif activity_type == "group_id":
                characters_with_recent_group_id.add(character_id)

    # Calculate statistics for logging
    total_characters = len(character_ids_list)
    total_level_from_history = sum(
        1
        for char_id in character_ids_list
        if char_id not in characters_with_recent_total_level
        and latest_states.get(char_id, {}).get("total_level") is not None
    )
    group_id_from_history = sum(
        1
        for char_id in character_ids_list
        if char_id not in characters_with_recent_group_id
        and latest_states.get(char_id, {}).get("group_id") is not None
    )

    # Log statistics about historical data usage
    total_level_pct = (
        (total_level_from_history / total_characters * 100)
        if total_characters > 0
        else 0
    )
    group_id_pct = (
        (group_id_from_history / total_characters * 100) if total_characters > 0 else 0
    )

    logger.info(
        f"Character state lookback stats: "
        f"total_level from history {total_level_from_history}/{total_characters} ({total_level_pct:.1f}%) | "
        f"group_id from history {group_id_from_history}/{total_characters} ({group_id_pct:.1f}%)"
    )

    # Process each character's activities and collect Redis updates
    all_sessions_to_insert = []
    redis_updates_set = {}
    redis_updates_clear = []
    filtered_session_count = 0

    for character_id, char_activities in by_character.items():
        # Sort chronologically (should already be sorted, but ensure it)
        char_activities.sort(key=lambda x: x[0])

        initial_session = character_sessions.get(character_id)

        # Get the latest state for this character to use as initial state
        latest_state = latest_states.get(character_id, {})
        initial_total_level = latest_state.get("total_level")
        initial_classes = latest_state.get("classes")
        initial_group_id = latest_state.get("group_id")

        sessions, final_session, status_seen = process_character_activities(
            character_id,
            char_activities,
            initial_session,
            initial_total_level,
            initial_classes,
            initial_group_id,
        )

        # Filter out sessions for excluded quests (non-unique area IDs)
        filtered_sessions = [
            s for s in sessions if s["quest_id"] not in EXCLUDED_QUEST_IDS
        ]
        filtered_session_count += len(sessions) - len(filtered_sessions)
        all_sessions_to_insert.extend(filtered_sessions)

        # Collect Redis updates instead of applying immediately
        if status_seen:
            redis_updates_clear.append(character_id)
        elif final_session is not None:
            # Don't track excluded quests in Redis either
            if final_session.quest_id not in EXCLUDED_QUEST_IDS:
                redis_updates_set[character_id] = {
                    "quest_id": int(final_session.quest_id),
                    "entry_timestamp": final_session.entry_timestamp.isoformat(),
                    "entry_total_level": final_session.entry_total_level,
                    "entry_classes": final_session.entry_classes,
                    "entry_group_id": final_session.entry_group_id,
                }
            else:
                # Clear any existing session for this character to avoid stale data
                redis_updates_clear.append(character_id)
        else:
            redis_updates_clear.append(character_id)

    # Batch apply all Redis updates in a single operation
    if redis_updates_set or redis_updates_clear:
        batch_update_active_quest_session_states(redis_updates_set, redis_updates_clear)

    # Bulk insert quest sessions
    if all_sessions_to_insert:
        logger.info(f"Inserting {len(all_sessions_to_insert)} quest sessions")
        bulk_insert_quest_sessions(all_sessions_to_insert)

    # Calculate new checkpoint: use the timestamp and max character_id from the last activity
    # This ensures proper pagination for next batch if activities with same timestamp span batches
    if activities:
        last_activity = activities[-1]
        new_last_timestamp = last_activity[1]  # timestamp
        new_max_character_id = last_activity[0]  # character_id
    else:
        new_last_timestamp = last_timestamp
        new_max_character_id = max_character_id_at_timestamp

    return (
        new_last_timestamp,
        new_max_character_id,
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
    time_window_hours = 24
    batch_size = env_int("QUEST_WORKER_BATCH_SIZE", 10000)
    lookback_days = env_int("QUEST_WORKER_LOOKBACK_DAYS", 90)
    sleep_between_batches = env_float("QUEST_WORKER_SLEEP_SECS", 1.0)
    idle_sleep = env_float("QUEST_WORKER_IDLE_SECS", 300.0)
    allow_truncate = os.getenv("QUEST_WORKER_ALLOW_TRUNCATE", "false").lower() == "true"

    logger.info(
        f"Quest Session Worker starting (batch_size={batch_size}, "
        f"time_window_hours={time_window_hours}, lookback_days={lookback_days})"
    )

    # Initialize database and Redis connections
    initialize_postgres()
    initialize_redis()

    # Load quest area lookup maps
    load_quest_area_maps()

    # Try to resume from checkpoint, otherwise cold start
    start_time = time.time()
    checkpoint = get_quest_worker_checkpoint()

    is_cold_start = checkpoint is None

    if is_cold_start:
        logger.info("No checkpoint found - performing cold start initialization")

        if allow_truncate:
            # Explicit truncate only if explicitly enabled (disaster recovery)
            logger.warning(
                "QUEST_WORKER_ALLOW_TRUNCATE=true: Truncating quest_sessions table. "
                "This should only be done during initial setup or disaster recovery."
            )
            truncate_quest_sessions()
        else:
            # Safe cold start: preserve existing sessions and reprocess from lookback period
            logger.info(
                "QUEST_WORKER_ALLOW_TRUNCATE is disabled (recommended). "
                "Resuming from lookback period without truncating quest_sessions table. "
                "Idempotent constraints ensure reprocessed sessions won't create duplicates."
            )

        # Clear any stale active quest sessions from Redis
        logger.info("Clearing active quest sessions from Redis for clean cold start")
        clear_all_active_quest_sessions()

        # Set last_timestamp to lookback_days ago
        last_timestamp = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        max_character_id_at_timestamp = 0
        logger.info(
            f"Cold start: setting initial timestamp to {lookback_days} days ago: "
            f"{last_timestamp.isoformat()}"
        )
    else:
        # Warm start: resume from checkpoint
        last_timestamp, max_character_id_at_timestamp = checkpoint
        logger.info(
            f"Found checkpoint, resuming from timestamp: {last_timestamp.isoformat()} "
            f"(max_char_id={max_character_id_at_timestamp})"
        )

    total_activities_processed = 0
    total_sessions_created = 0
    batch_count = 0
    first_run = True
    last_quest_lookup = time.time()

    while True:
        try:
            # Refresh quest area maps every hour in case of updates
            if time.time() - last_quest_lookup > 3600:
                load_quest_area_maps()
                last_quest_lookup = time.time()

            batch_count += 1
            batch_start_time = time.time()

            logger.info(f"Starting batch {batch_count}...")

            (
                new_last_timestamp,
                new_max_character_id,
                activities_count,
                sessions_count,
            ) = process_batch(
                last_timestamp,
                max_character_id_at_timestamp,
                batch_size,
                time_window_hours,
            )

            batch_duration = time.time() - batch_start_time
            total_activities_processed += activities_count
            total_sessions_created += sessions_count
            total_runtime = time.time() - start_time

            if new_last_timestamp == last_timestamp and activities_count == 0:
                # No new activities processed in this batch
                if first_run:
                    # Move last_timestamp forward to avoid infinite loop on already-processed events
                    new_last_timestamp = last_timestamp + timedelta(
                        hours=time_window_hours
                    )
                    new_max_character_id = 0
                    logger.debug(
                        f"No activities found in window, advancing timestamp to {new_last_timestamp.isoformat()}"
                    )

            if (
                activities_count == 0 or (activities_count < 100 and batch_count >= 10)
            ) and not first_run:
                logger.info(
                    f"✓ No more activities found. "
                    f"Total processed: {total_activities_processed:,} activities, "
                    f"{total_sessions_created:,} sessions created in {batch_count} batches. "
                    f"Total runtime: {format_duration(total_runtime)}. "
                    f"Sleeping for {format_duration(idle_sleep)} before next check..."
                )
                last_timestamp = max(
                    datetime.now(timezone.utc) - timedelta(hours=time_window_hours),
                    last_timestamp,
                )
                max_character_id_at_timestamp = 0
                time.sleep(idle_sleep)

                # Reset for next cycle
                total_activities_processed = 0
                total_sessions_created = 0
                batch_count = 0
                start_time = time.time()
                continue

            # Calculate processing rate
            activities_per_second = (
                activities_count / batch_duration if batch_duration > 0 else 0
            )

            # Update checkpoint in Redis with composite (timestamp, max_character_id)
            set_quest_worker_checkpoint(new_last_timestamp, new_max_character_id)

            # Calculate checkpoint age for monitoring
            checkpoint_age = datetime.now(timezone.utc) - new_last_timestamp

            # Build progress log message
            log_parts = [
                f"Batch {batch_count} complete:",
                f"{activities_count:,} activities processed",
                f"{sessions_count:,} sessions created",
                f"batch took {format_duration(batch_duration)}",
                f"rate: {activities_per_second:.1f} activities/sec",
                f"total processed: {total_activities_processed:,} activities",
                f"runtime: {format_duration(total_runtime)}",
                f"checkpoint age: {format_duration(checkpoint_age.total_seconds())}",
            ]

            logger.info(" | ".join(log_parts))

            last_timestamp = new_last_timestamp
            max_character_id_at_timestamp = new_max_character_id

            # Check if we've caught up to current time
            if datetime.now(timezone.utc) - new_last_timestamp <= timedelta(
                hours=time_window_hours
            ):
                # We've caught up to current time
                first_run = False

            # Small sleep between batches to avoid overwhelming the database
            if sleep_between_batches > 0 and not first_run:
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
