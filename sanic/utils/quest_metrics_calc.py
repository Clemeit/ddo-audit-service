"""Shared functions for calculating quest metrics and analytics."""

import json
import logging
import os
import time
from typing import Optional

from services.postgres import (
    get_quest_analytics,
    get_all_quests,
    get_quest_by_id,
    get_quest_metrics,
    get_quest_metrics_bulk,
)
from services.redis import get_redis_client

from models.quest_session import QuestAnalytics

logger = logging.getLogger(__name__)

# Constants for normalization
LOOKBACK_DAYS = 90
MIN_SESSIONS_FOR_METRICS = 100

# Redis key for intermediate analytics cache during bulk calculation
REDIS_QUEST_ANALYTICS_CACHE_KEY = "quest_metrics:analytics_cache"


def _coerce_to_number(value: Optional[object]) -> Optional[float]:
    """Convert common numeric-like inputs to float or return None.

    Handles ints, floats, and numeric strings while avoiding exceptions from
    unexpected values.
    """
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return None
        try:
            return float(stripped)
        except ValueError:
            logger.debug("Non-numeric value encountered in metrics calc: %s", value)
            return None

    logger.debug("Unsupported type for numeric coercion: %s", type(value))
    return None


def calculate_xp_per_minute(
    xp_value: Optional[int], length_seconds: Optional[int]
) -> Optional[float]:
    """Calculate XP per minute from raw XP and quest duration.

    Args:
        xp_value: XP reward (from heroic_elite or epic_elite)
        length_seconds: Quest length in seconds

    Returns:
        XP per minute, or None if insufficient data
    """
    xp_numeric = _coerce_to_number(xp_value)
    length_numeric = _coerce_to_number(length_seconds)

    if xp_numeric is None or length_numeric is None or length_numeric <= 0:
        return None

    return (xp_numeric / length_numeric) * 60  # Convert to per-minute


def calculate_relative_metric(
    value: Optional[float], peer_values: list[float | None]
) -> Optional[float]:
    """Normalize a metric to 0-1 scale based on peer comparison.

    0.0 = much lower than peers
    0.5 = at peer average
    1.0 = much higher than peers

    Args:
        value: The value to normalize
        peer_values: List of all peer values (e.g., all XP/min for same level)

    Returns:
        Normalized value 0-1, or None if insufficient data
    """
    if value is None or not peer_values:
        return None

    # Filter out None values
    valid_peers = [v for v in peer_values if v is not None]
    if not valid_peers:
        return None

    peer_values_sorted = sorted(valid_peers)
    min_peer = peer_values_sorted[0]
    max_peer = peer_values_sorted[-1]

    # If all peers are the same value, return 0.5 (average)
    if min_peer == max_peer:
        return 0.5

    # Normalize: (value - min) / (max - min) gives 0-1 range
    normalized = (value - min_peer) / (max_peer - min_peer)

    # Clamp to 0-1
    return max(0.0, min(1.0, normalized))


def get_quest_metrics_single(
    quest_id: int, force_refresh: bool = False, cached_metrics: Optional[dict] = None
) -> Optional[dict]:
    """Calculate metrics for a single quest efficiently using cached analytics when available.

    Args:
        quest_id: The quest ID to calculate metrics for
        force_refresh: When True, recompute analytics; otherwise prefer cached analytics_data
        cached_metrics: Optional pre-fetched cache entry to avoid duplicate lookups

    Returns:
        Dictionary containing metrics or None if quest not found/insufficient data
    """
    logger.info(f"Calculating metrics for single quest {quest_id}")

    try:
        # Fetch the specific quest
        quest = get_quest_by_id(quest_id)
        if not quest:
            logger.warning(f"Quest {quest_id} not found")
            return None

        logger.debug(f"Processing quest {quest.id}: {quest.name}")

        # Try cached analytics first unless a refresh is requested
        analytics: QuestAnalytics | None = None
        metrics_source = None if force_refresh else cached_metrics
        if metrics_source is None and not force_refresh:
            metrics_source = get_quest_metrics(quest_id)

        if metrics_source and metrics_source.get("analytics_data"):
            analytics = QuestAnalytics(**metrics_source["analytics_data"])

        # Fallback to live analytics when missing or forced
        if analytics is None:
            try:
                analytics = get_quest_analytics(quest_id, lookback_days=LOOKBACK_DAYS)
            except Exception as e:
                logger.error(f"Failed to get analytics for quest {quest_id}: {e}")
                return None

        # Skip if insufficient data
        if analytics.total_sessions < MIN_SESSIONS_FOR_METRICS:
            logger.debug(
                f"Quest {quest_id} skipped: insufficient sessions ({analytics.total_sessions} < {MIN_SESSIONS_FOR_METRICS})"
            )
            return None

        quest_metrics = {
            "heroic_xp_per_minute_relative": None,
            "epic_xp_per_minute_relative": None,
            "heroic_popularity_relative": None,
            "epic_popularity_relative": None,
            "analytics_data": analytics.model_dump(),
        }

        # Get peer quests for CR groups (only needed CR groups, not all)
        heroic_peers = []
        epic_peers = []
        all_quests = get_all_quests()
        PEER_RANGE = 0  # TODO: set to 2

        if quest.heroic_normal_cr is not None:
            heroic_peers = [
                q
                for q in all_quests
                if q.heroic_normal_cr is not None
                and q.id != quest.id
                and abs(q.heroic_normal_cr - quest.heroic_normal_cr) <= PEER_RANGE
            ]

        if quest.epic_normal_cr is not None:
            epic_peers = [
                q
                for q in all_quests
                if q.epic_normal_cr is not None
                and q.id != quest.id
                and abs(q.epic_normal_cr - quest.epic_normal_cr) <= PEER_RANGE
            ]

        # Fetch cached analytics for peer quests in a single query
        peer_ids = list({peer.id for peer in heroic_peers + epic_peers})
        peer_metrics_map = get_quest_metrics_bulk(peer_ids) if peer_ids else {}

        # Calculate Heroic XP/min relative metric
        if (
            quest.heroic_normal_cr is not None
            and quest.xp
            and "heroic_elite" in quest.xp
            and quest.length is not None
        ):
            heroic_xp_per_min = calculate_xp_per_minute(
                quest.xp.get("heroic_elite"), quest.length
            )

            if heroic_xp_per_min is not None:
                # Collect peer XP/min values
                peer_xp_per_mins = []
                for peer_quest in heroic_peers:
                    if (
                        peer_quest.xp
                        and "heroic_elite" in peer_quest.xp
                        and peer_quest.length is not None
                    ):
                        peer_xpm = calculate_xp_per_minute(
                            peer_quest.xp.get("heroic_elite"), peer_quest.length
                        )
                        if peer_xpm is not None:
                            peer_xp_per_mins.append(peer_xpm)

                relative = calculate_relative_metric(
                    heroic_xp_per_min, peer_xp_per_mins
                )
                quest_metrics["heroic_xp_per_minute_relative"] = relative

        # Calculate Epic XP/min relative metric
        if (
            quest.epic_normal_cr is not None
            and quest.xp
            and "epic_elite" in quest.xp
            and quest.length is not None
        ):
            epic_xp_per_min = calculate_xp_per_minute(
                quest.xp.get("epic_elite"), quest.length
            )

            if epic_xp_per_min is not None:
                # Collect peer XP/min values
                peer_xp_per_mins = []
                for peer_quest in epic_peers:
                    if (
                        peer_quest.xp
                        and "epic_elite" in peer_quest.xp
                        and peer_quest.length is not None
                    ):
                        peer_xpm = calculate_xp_per_minute(
                            peer_quest.xp.get("epic_elite"), peer_quest.length
                        )
                        if peer_xpm is not None:
                            peer_xp_per_mins.append(peer_xpm)

                relative = calculate_relative_metric(epic_xp_per_min, peer_xp_per_mins)
                quest_metrics["epic_xp_per_minute_relative"] = relative

        # Calculate popularity relative metric using cached peer analytics
        # Use heroic CR if available, fall back to epic CR if not
        all_peer_sessions = []

        def _peer_total_sessions(peer_id: int) -> Optional[float]:
            peer_metrics = peer_metrics_map.get(peer_id)
            if not peer_metrics:
                return None

            peer_data = peer_metrics.get("analytics_data")
            if not peer_data:
                return None

            try:
                peer_analytics = (
                    QuestAnalytics(**peer_data)
                    if isinstance(peer_data, dict)
                    else peer_data
                )
                return peer_analytics.total_sessions
            except Exception:
                logger.debug("Failed to parse peer analytics for quest %s", peer_id)
                return None

        if quest.heroic_normal_cr is not None:
            for peer_quest in heroic_peers:
                peer_sessions = _peer_total_sessions(peer_quest.id)
                if peer_sessions is not None:
                    all_peer_sessions.append(peer_sessions)
        elif quest.epic_normal_cr is not None:
            # Fall back to epic CR if no heroic CR
            for peer_quest in epic_peers:
                peer_sessions = _peer_total_sessions(peer_quest.id)
                if peer_sessions is not None:
                    all_peer_sessions.append(peer_sessions)

        if analytics.total_sessions > 0 and all_peer_sessions:
            popularity_relative = calculate_relative_metric(
                float(analytics.total_sessions), all_peer_sessions
            )
            quest_metrics["heroic_popularity_relative"] = popularity_relative
            # epic_popularity_relative remains None (future feature)

        logger.debug(f"Metrics calculated for quest {quest_id}")
        return quest_metrics

    except Exception as e:
        logger.error(
            f"Error calculating metrics for quest {quest_id}: {e}", exc_info=True
        )
        return None


def compute_all_quest_analytics_pass1() -> dict:
    """Pass 1: Fetch and cache analytics data for all quests in Redis.

    This pass queries the quest_sessions table for each quest to compute
    analytics_data (total sessions, average duration, histograms, etc.).
    Results are stored in Redis for use in Pass 2.

    Returns:
        Dictionary mapping quest_id to QuestAnalytics dict
    """
    logger.info("[PASS 1] Fetching analytics for all quests")

    try:
        all_quests = get_all_quests()
        if not all_quests:
            logger.warning("No quests found in database")
            return {}

        logger.info(f"[PASS 1] Processing {len(all_quests)} quests")

        # Get delay between quest processing to reduce postgres load
        delay_between_quests = float(os.getenv("QUEST_METRICS_DELAY_SECS", "0.1"))

        analytics_by_id = {}

        for quest in all_quests:
            try:
                analytics = get_quest_analytics(quest.id, lookback_days=LOOKBACK_DAYS)
                analytics_by_id[quest.id] = analytics.model_dump()

                # Sleep between quest processing to reduce load on postgres
                if delay_between_quests > 0:
                    time.sleep(delay_between_quests)

            except Exception as e:
                logger.error(
                    f"[PASS 1] Failed to get analytics for quest {quest.id}: {e}",
                    exc_info=True,
                )

        # Store analytics in Redis for Pass 2
        logger.info(f"[PASS 1] Storing {len(analytics_by_id)} quest analytics in Redis")
        with get_redis_client() as redis_client:
            # Convert dict to JSON and store as a single hash
            for quest_id, analytics_data in analytics_by_id.items():
                redis_client.hset(
                    REDIS_QUEST_ANALYTICS_CACHE_KEY,
                    str(quest_id),
                    json.dumps(analytics_data),
                )

            # Set expiration to 24 hours (cleanup in case pass 2 fails)
            redis_client.expire(REDIS_QUEST_ANALYTICS_CACHE_KEY, 86400)

        logger.info(
            f"[PASS 1] Complete. Fetched analytics for {len(analytics_by_id)} quests"
        )
        return analytics_by_id

    except Exception as e:
        logger.error(f"[PASS 1] Error fetching quest analytics: {e}", exc_info=True)
        raise


def compute_all_quest_relative_metrics_pass2() -> dict:
    """Pass 2: Calculate relative metrics for all quests using cached analytics.

    This pass uses the analytics data cached in Redis from Pass 1 to compute
    relative XP/min and popularity metrics by comparing each quest to its peers.

    Returns:
        Dictionary mapping quest_id to complete metrics dict ready for DB upsert
    """
    logger.info("[PASS 2] Calculating relative metrics for all quests")

    try:
        # Load analytics from Redis
        logger.info("[PASS 2] Loading cached analytics from Redis")
        analytics_by_id = {}

        with get_redis_client() as redis_client:
            cached_data = redis_client.hgetall(REDIS_QUEST_ANALYTICS_CACHE_KEY)
            if not cached_data:
                logger.error("[PASS 2] No cached analytics found in Redis")
                raise RuntimeError(
                    "Pass 2 requires Pass 1 analytics cache in Redis. Run Pass 1 first."
                )

            for quest_id_bytes, analytics_json_bytes in cached_data.items():
                quest_id = int(quest_id_bytes.decode("utf-8"))
                analytics_dict = json.loads(analytics_json_bytes.decode("utf-8"))
                analytics_by_id[quest_id] = QuestAnalytics(**analytics_dict)

        logger.info(f"[PASS 2] Loaded analytics for {len(analytics_by_id)} quests")

        # Fetch all quests metadata
        all_quests = get_all_quests()
        if not all_quests:
            logger.warning("No quests found in database")
            return {}

        # Group quests by CR level (both heroic and epic)
        heroic_cr_groups = {}
        epic_cr_groups = {}

        for quest in all_quests:
            if quest.heroic_normal_cr is not None:
                cr = quest.heroic_normal_cr
                if cr not in heroic_cr_groups:
                    heroic_cr_groups[cr] = []
                heroic_cr_groups[cr].append(quest)

            if quest.epic_normal_cr is not None:
                cr = quest.epic_normal_cr
                if cr not in epic_cr_groups:
                    epic_cr_groups[cr] = []
                epic_cr_groups[cr].append(quest)

        metrics_data = {}

        # Process each quest
        for quest in all_quests:
            logger.debug(f"[PASS 2] Processing quest {quest.id}: {quest.name}")

            analytics = analytics_by_id.get(quest.id)

            if analytics is None:
                logger.debug(f"[PASS 2] No analytics found for quest {quest.id}")
                continue

            # Skip if insufficient data
            if analytics.total_sessions < MIN_SESSIONS_FOR_METRICS:
                logger.debug(
                    f"[PASS 2] Quest {quest.id} skipped: insufficient sessions "
                    f"({analytics.total_sessions} < {MIN_SESSIONS_FOR_METRICS})"
                )
                continue

            quest_metrics = {
                "heroic_xp_per_minute_relative": None,
                "epic_xp_per_minute_relative": None,
                "heroic_popularity_relative": None,
                "epic_popularity_relative": None,
                "analytics_data": analytics.model_dump(),
            }

            # Calculate Heroic XP/min relative metric
            if (
                quest.heroic_normal_cr is not None
                and quest.xp
                and "heroic_elite" in quest.xp
                and quest.length is not None
            ):
                heroic_xp_per_min = calculate_xp_per_minute(
                    quest.xp.get("heroic_elite"), quest.length
                )

                if heroic_xp_per_min is not None:
                    cr = quest.heroic_normal_cr
                    peer_xp_per_mins = []
                    for peer_quest in heroic_cr_groups.get(cr, []):
                        if (
                            peer_quest.xp
                            and "heroic_elite" in peer_quest.xp
                            and peer_quest.length is not None
                        ):
                            peer_xpm = calculate_xp_per_minute(
                                peer_quest.xp.get("heroic_elite"), peer_quest.length
                            )
                            if peer_xpm is not None:
                                peer_xp_per_mins.append(peer_xpm)

                    relative = calculate_relative_metric(
                        heroic_xp_per_min, peer_xp_per_mins
                    )
                    quest_metrics["heroic_xp_per_minute_relative"] = relative

            # Calculate Epic XP/min relative metric
            if (
                quest.epic_normal_cr is not None
                and quest.xp
                and "epic_elite" in quest.xp
                and quest.length is not None
            ):
                epic_xp_per_min = calculate_xp_per_minute(
                    quest.xp.get("epic_elite"), quest.length
                )

                if epic_xp_per_min is not None:
                    cr = quest.epic_normal_cr
                    peer_xp_per_mins = []
                    for peer_quest in epic_cr_groups.get(cr, []):
                        if (
                            peer_quest.xp
                            and "epic_elite" in peer_quest.xp
                            and peer_quest.length is not None
                        ):
                            peer_xpm = calculate_xp_per_minute(
                                peer_quest.xp.get("epic_elite"), peer_quest.length
                            )
                            if peer_xpm is not None:
                                peer_xp_per_mins.append(peer_xpm)

                    relative = calculate_relative_metric(
                        epic_xp_per_min, peer_xp_per_mins
                    )
                    quest_metrics["epic_xp_per_minute_relative"] = relative

            # Calculate popularity relative metric using cached analytics
            # Use heroic CR if available, fall back to epic CR if not
            all_peer_sessions = []

            if quest.heroic_normal_cr is not None:
                cr = quest.heroic_normal_cr
                for peer_quest in heroic_cr_groups.get(cr, []):
                    peer_analytics = analytics_by_id.get(peer_quest.id)
                    if peer_analytics is not None:
                        all_peer_sessions.append(peer_analytics.total_sessions)
            elif quest.epic_normal_cr is not None:
                cr = quest.epic_normal_cr
                for peer_quest in epic_cr_groups.get(cr, []):
                    peer_analytics = analytics_by_id.get(peer_quest.id)
                    if peer_analytics is not None:
                        all_peer_sessions.append(peer_analytics.total_sessions)

            if analytics.total_sessions > 0 and all_peer_sessions:
                popularity_relative = calculate_relative_metric(
                    float(analytics.total_sessions), all_peer_sessions
                )
                quest_metrics["heroic_popularity_relative"] = popularity_relative
                # epic_popularity_relative remains None (future feature)

            metrics_data[quest.id] = quest_metrics
            logger.debug(f"[PASS 2] Metrics calculated for quest {quest.id}")

        logger.info(
            f"[PASS 2] Complete. Calculated metrics for {len(metrics_data)} quests"
        )
        return metrics_data

    except Exception as e:
        logger.error(f"[PASS 2] Error calculating relative metrics: {e}", exc_info=True)
        raise
    finally:
        # Ensure Redis cache is cleaned up even on error
        with get_redis_client() as redis_client:
            redis_client.delete(REDIS_QUEST_ANALYTICS_CACHE_KEY)


def get_all_quest_metrics_data() -> dict:
    """Calculate complete metrics for all quests using two-pass approach.

    Pass 1: Fetch analytics data from quest_sessions for each quest
    Pass 2: Calculate relative metrics using cached analytics from Pass 1

    Returns:
        Dictionary mapping quest_id to metrics dict containing:
        - heroic_xp_per_minute_relative
        - epic_xp_per_minute_relative
        - heroic_popularity_relative
        - epic_popularity_relative
        - analytics_data (QuestAnalytics as dict)
    """
    logger.info("Starting two-pass quest metrics calculation")

    try:
        # Pass 1: Fetch and cache analytics
        compute_all_quest_analytics_pass1()

        # Pass 2: Calculate relative metrics using cached analytics
        metrics_data = compute_all_quest_relative_metrics_pass2()

        logger.info(
            f"Two-pass calculation complete. Final metrics for {len(metrics_data)} quests"
        )
        return metrics_data

    except Exception as e:
        logger.error(f"Error in two-pass quest metrics calculation: {e}", exc_info=True)
        raise
