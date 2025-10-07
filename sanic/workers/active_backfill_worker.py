import os
import sys
import time
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from typing import Dict, List, Tuple

# Ensure relative imports work when invoked as module or script
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from utils.activity import calculate_active_playstyle_score  # type: ignore
from utils.time import datetime_to_datetime_string  # type: ignore
from services.postgres import (  # type: ignore
    initialize_postgres,
    get_db_connection,
    set_characters_active_status_bulk,
)


logger = logging.getLogger("active_backfill_worker")
logging.basicConfig(
    level=os.getenv("WORKER_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def fetch_character_batch(
    last_id: int, shard_count: int, shard_index: int, batch_size: int
) -> List[Tuple[int, int]]:
    """Fetch a batch of characters for this shard that are missing a status row.

    Returns list of tuples (id, total_level).
    """
    query = """
        SELECT c.id, c.total_level
        FROM public.characters c
        LEFT JOIN public.character_report_status s ON s.character_id = c.id
        WHERE c.id > %s
          AND (c.id %% %s) = %s
          AND s.character_id IS NULL
        ORDER BY c.id ASC
        LIMIT %s
        """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (last_id, shard_count, shard_index, batch_size))
            rows = cursor.fetchall()
            return [(int(r[0]), int(r[1]) if r[1] is not None else 0) for r in rows]


def fetch_activities_for_ids(
    ids: List[int], lookback_days: int
) -> Dict[int, List[dict]]:
    """Fetch recent activities for a list of character ids and normalize them
    to the format expected by calculate_active_playstyle_score.
    """
    if not ids:
        return {}

    by_char: Dict[int, List[dict]] = defaultdict(list)

    query = """
        SELECT timestamp, character_id, activity_type, data
        FROM public.character_activity
        WHERE character_id = ANY(%s) AND timestamp >= NOW() - (make_interval(days => %s))
        ORDER BY character_id, timestamp ASC
        """

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (ids, lookback_days))
            rows = cursor.fetchall()

    for ts, cid, activity_type, data in rows:
        # Normalize to the structure used by utils.activity
        try:
            ts_str = (
                datetime_to_datetime_string(ts) if isinstance(ts, datetime) else str(ts)
            )
            normalized = {"timestamp": ts_str, "character_id": int(cid), "data": {}}
            if activity_type == "status":
                # Database stored as {"value": true/false}
                v = None
                try:
                    v = data.get("value") if isinstance(data, dict) else None
                except Exception:
                    v = None
                if v is not None:
                    normalized["data"]["status"] = bool(v)
            elif activity_type == "location":
                v = None
                try:
                    v = data.get("value") if isinstance(data, dict) else None
                except Exception:
                    v = None
                if v is not None:
                    try:
                        normalized["data"]["location_id"] = int(v)
                    except Exception:
                        pass
            elif activity_type == "total_level":
                # Expect a shape like {"total_level": int, "classes": [...]}
                if isinstance(data, dict):
                    if "total_level" in data:
                        normalized["data"]["total_level"] = data["total_level"]
                    if "classes" in data:
                        normalized["data"]["classes"] = data["classes"]

            if normalized["data"]:
                by_char[int(cid)].append(normalized)
        except Exception:
            # Be resilient to malformed rows
            continue

    return by_char


def compute_updates(
    chars: List[Tuple[int, int]],
    activities: Dict[int, List[dict]],
) -> List[Tuple[int, bool, datetime]]:
    """Compute active flag for characters and build upsert tuples.

    Input chars: list of (id, total_level)
    Returns list of (character_id, active, checked_at)
    """
    updates: List[Tuple[int, bool, datetime]] = []
    now_dt = datetime.now(timezone.utc)
    for cid, total_level in chars:
        acts = activities.get(cid, [])
        char_obj = {"id": cid, "total_level": total_level}
        try:
            result = calculate_active_playstyle_score(char_obj, acts)
            is_active = bool(result.get("is_active", False))
        except Exception:
            # Default to inactive on compute error; you may log specifics
            is_active = False
        updates.append((cid, is_active, now_dt))
    return updates


def fetch_stale_character_batch(
    last_id: int,
    shard_count: int,
    shard_index: int,
    stale_days: int,
    batch_size: int,
) -> List[Tuple[int, int]]:
    """Fetch a batch of characters whose status is stale or never checked.

    Returns list of tuples (id, total_level).
    """
    query = """
        SELECT c.id, c.total_level
        FROM public.character_report_status s
        JOIN public.characters c ON c.id = s.character_id
        WHERE (s.active_checked_at IS NULL OR s.active_checked_at < NOW() - (make_interval(days => %s)))
          AND c.id > %s
          AND (c.id %% %s) = %s
        ORDER BY c.id ASC
        LIMIT %s
        """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query, (stale_days, last_id, shard_count, shard_index, batch_size)
            )
            rows = cursor.fetchall()
            return [(int(r[0]), int(r[1]) if r[1] is not None else 0) for r in rows]


def seed_missing_status_rows_for_shard(
    shard_count: int,
    shard_index: int,
    seed_limit: int = 0,
) -> int:
    """Optionally insert missing status rows for this shard.

    Returns number of rows inserted (best-effort; may not be exact across versions).
    """
    if shard_count <= 0:
        shard_count = 1
    base = (
        "INSERT INTO public.character_report_status (character_id) "
        "SELECT id FROM public.characters WHERE (id %% %s) = %s"
    )
    params: List[object] = [shard_count, shard_index]
    if seed_limit and seed_limit > 0:
        query = base + " ORDER BY id ASC LIMIT %s ON CONFLICT DO NOTHING"
        params.append(seed_limit)
    else:
        query = base + " ON CONFLICT DO NOTHING"

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, tuple(params))
            # rowcount is not reliable for INSERT..SELECT; treat as best-effort
            try:
                return cursor.rowcount or 0
            except Exception:
                return 0


def _hostname_mod(n: int) -> int:
    try:
        h = os.getenv("HOSTNAME", "")
        if not h or n <= 0:
            return 0
        # Simple stable hash: sum of bytes mod n
        return sum(h.encode("utf-8")) % n
    except Exception:
        return 0


def main():
    initialize_postgres()

    # Config via env
    shard_count = max(1, env_int("WORKER_SHARDS", 1))
    auto_index = os.getenv("WORKER_AUTO_INDEX", "").lower() in ("1", "true", "yes")
    if auto_index:
        shard_index = _hostname_mod(shard_count)
    else:
        shard_index = max(0, env_int("WORKER_INDEX", 0)) % shard_count
    batch_size = max(1, env_int("WORKER_BATCH_SIZE", 1000))
    lookback_days = max(1, env_int("WORKER_LOOKBACK_DAYS", 90))
    sleep_secs = max(0.0, env_float("WORKER_SLEEP_SECS", 0.0))
    max_batches = env_int("WORKER_MAX_BATCHES", 0)  # 0 = run until done
    stale_days = max(1, env_int("WORKER_STALE_DAYS", 7))
    seed_missing = os.getenv("WORKER_SEED_MISSING", "").lower() in ("1", "true", "yes")
    seed_limit = env_int("WORKER_SEED_LIMIT", 0)

    logger.info(
        "Starting active backfill worker: shard %s/%s, batch_size=%s, lookback_days=%s, stale_days=%s",
        shard_index,
        shard_count,
        batch_size,
        lookback_days,
        stale_days,
    )

    # Optional one-time seed of missing rows for this shard (bounded by seed_limit)
    if seed_missing:
        try:
            inserted = seed_missing_status_rows_for_shard(
                shard_count, shard_index, seed_limit
            )
            logger.info(
                "Seeded missing status rows for shard %s: inserted~%s",
                shard_index,
                inserted,
            )
        except Exception as e:
            logger.warning("Seeding missing status rows failed: %s", e)

    last_id = -1
    stale_last_id = -1
    batches = 0
    total_processed = 0

    try:
        while True:
            # Prefer stale rows first
            chars = fetch_stale_character_batch(
                stale_last_id, shard_count, shard_index, stale_days, batch_size
            )
            processing_stale = True

            if not chars:
                # Then process new/unseen characters by id
                chars = fetch_character_batch(
                    last_id, shard_count, shard_index, batch_size
                )
                processing_stale = False
            if not chars:
                logger.info(
                    "No more characters for this shard. Processed=%s. Exiting.",
                    total_processed,
                )
                break

            batch_start = time.perf_counter()
            ids = [cid for cid, _ in chars]
            acts = fetch_activities_for_ids(ids, lookback_days)
            updates = compute_updates(chars, acts)

            if updates:
                # Bulk upsert into character_report_status
                try:
                    set_characters_active_status_bulk(updates)
                except Exception as e:
                    logger.error(
                        "Bulk upsert failed for batch starting id %s: %s", last_id, e
                    )

            elapsed_s = max(0.0, time.perf_counter() - batch_start)
            duration_ms = round(elapsed_s * 1000.0, 2)
            avg_ms_per_char = round(duration_ms / max(1, len(chars)), 2)

            total_processed += len(chars)
            if processing_stale:
                stale_last_id = ids[-1]
            else:
                last_id = ids[-1]
            batches += 1
            logger.info(
                "Shard %s: processed %s batch of %s (last_id=%s, stale_last_id=%s) in %sms (avg %sms/char), total=%s",
                shard_index,
                "stale" if processing_stale else "new",
                len(chars),
                last_id,
                stale_last_id,
                duration_ms,
                avg_ms_per_char,
                total_processed,
            )

            if 0 < max_batches <= batches:
                logger.info("Reached max_batches=%s. Exiting.", max_batches)
                break

            if sleep_secs > 0:
                time.sleep(sleep_secs)

    except KeyboardInterrupt:
        logger.info("Interrupted by user. Exiting.")


if __name__ == "__main__":
    main()
