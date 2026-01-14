"""Shared functions for calculating quest metrics and analytics."""

import logging
from typing import Optional

from services.postgres import (
    get_quest_analytics,
    get_all_quests,
)

logger = logging.getLogger(__name__)

# Constants for normalization
LOOKBACK_DAYS = 90
MIN_SESSIONS_FOR_METRICS = 100


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
    if xp_value is None or length_seconds is None or length_seconds <= 0:
        return None

    return (xp_value / length_seconds) * 60  # Convert to per-minute


def calculate_relative_metric(
    value: Optional[float], peer_values: list[float]
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
    if value is None or not peer_values or len(peer_values) == 0:
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


def get_all_quest_metrics_data() -> dict:
    """Calculate metrics for all quests with sufficient data.

    Returns:
        Dictionary mapping quest_id to metrics dict containing:
        - heroic_xp_per_minute_relative
        - epic_xp_per_minute_relative
        - popularity_relative
        - analytics_data (QuestAnalytics as dict)
    """
    logger.info("Calculating quest metrics for all quests")

    try:
        # Fetch all quests
        all_quests = get_all_quests()
        if not all_quests:
            logger.warning("No quests found in database")
            return {}

        logger.info(f"Processing {len(all_quests)} quests")

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
            logger.debug(f"Processing quest {quest.id}: {quest.name}")

            # Get analytics for this quest
            try:
                analytics = get_quest_analytics(quest.id, lookback_days=LOOKBACK_DAYS)
            except Exception as e:
                logger.error(f"Failed to get analytics for quest {quest.id}: {e}")
                continue

            # Skip if insufficient data
            if analytics.total_sessions < MIN_SESSIONS_FOR_METRICS:
                logger.debug(
                    f"Quest {quest.id} skipped: insufficient sessions ({analytics.total_sessions} < {MIN_SESSIONS_FOR_METRICS})"
                )
                continue

            quest_metrics = {
                "heroic_xp_per_minute_relative": None,
                "epic_xp_per_minute_relative": None,
                "popularity_relative": None,
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
                    # Get all heroic XP/min for peers at same CR
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
                    # Get all epic XP/min for peers at same CR
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

            # Calculate popularity relative metric
            # Group all quests by heroic CR for popularity comparison
            all_heroic_sessions = []
            all_heroic_quests = []

            if quest.heroic_normal_cr is not None:
                cr = quest.heroic_normal_cr
                for peer_quest in heroic_cr_groups.get(cr, []):
                    all_heroic_quests.append(peer_quest.id)
                    try:
                        peer_analytics = get_quest_analytics(
                            peer_quest.id, lookback_days=LOOKBACK_DAYS
                        )
                        all_heroic_sessions.append(peer_analytics.total_sessions)
                    except Exception as e:
                        logger.debug(
                            f"Could not get analytics for peer quest {peer_quest.id}: {e}"
                        )

            if analytics.total_sessions > 0 and all_heroic_sessions:
                popularity_relative = calculate_relative_metric(
                    float(analytics.total_sessions), all_heroic_sessions
                )
                quest_metrics["popularity_relative"] = popularity_relative

            metrics_data[quest.id] = quest_metrics
            logger.debug(f"Metrics calculated for quest {quest.id}")

        logger.info(
            f"Quest metrics calculation complete. Processed {len(metrics_data)} quests"
        )
        return metrics_data

    except Exception as e:
        logger.error(f"Error calculating quest metrics: {e}", exc_info=True)
        raise
