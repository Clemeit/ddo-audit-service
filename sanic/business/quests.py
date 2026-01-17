"""
Quest business logic and analytics calculations.

This module handles quest-related business logic, including analytics calculations
and formatting utilities. It coordinates between the data layer (postgres service)
and the API layer.
"""

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

from models.quest_session import QuestAnalytics
import services.postgres as postgres_client

# Setup logging
logger = logging.getLogger(__name__)


def get_quest_analytics(quest_id: int, lookback_days: int = 90) -> QuestAnalytics:
    """Get comprehensive analytics for a quest.

    Args:
        quest_id: ID of the quest
        lookback_days: Number of days to look back (default 90)

    Returns:
        QuestAnalytics object with duration stats and activity patterns
    """
    logger.info(
        f"Getting quest analytics for quest_id={quest_id}, lookback_days={lookback_days}"
    )
    try:
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        logger.debug(f"Cutoff date for analytics: {cutoff_date}")
    except Exception as e:
        logger.error(f"Error calculating cutoff date: {e}", exc_info=True)
        raise

    try:
        # Fetch raw analytics data from the database
        raw_analytics = postgres_client.get_quest_analytics_raw(quest_id, cutoff_date)
        
        if raw_analytics is None:
            logger.warning(f"No data found for quest_id={quest_id}")
            return QuestAnalytics(
                average_duration_seconds=None,
                standard_deviation_seconds=None,
                histogram=[],
                activity_by_hour=[],
                activity_by_day_of_week=[],
                activity_over_time=[],
                total_sessions=0,
                completed_sessions=0,
                active_sessions=0,
            )

        # Extract and process the analytics data
        (
            avg_duration,
            stddev_duration,
            p01,
            q25,
            q75,
            p99,
            total_sessions,
            completed_sessions,
            active_sessions,
            histogram_rows,
            hour_rows,
            dow_rows,
            time_rows,
        ) = raw_analytics

        # Use p01/p99 to bound range and q25/q75 to compute IQR
        min_duration = p01
        max_duration = p99
        iqr = (q75 - q25) if (q75 is not None and q25 is not None) else None

        logger.debug(
            f"Computed stats - avg_duration={avg_duration}, stddev={stddev_duration}, "
            f"min={min_duration}, max={max_duration}, total={total_sessions}, "
            f"completed={completed_sessions}, active={active_sessions}"
        )

        # Generate dynamic bin ranges using Freedman–Diaconis for bin width
        logger.debug("Generating dynamic bins (Freedman–Diaconis)...")
        num_bins_fd = 8
        duration_range = max(0.0, max_duration - min_duration)
        if (
            iqr is not None
            and iqr > 0
            and total_sessions > 0
            and duration_range > 0
        ):
            bin_width_fd = (2.0 * iqr) / (total_sessions ** (1.0 / 3.0))
            if bin_width_fd > 0:
                num_bins_fd = max(1, math.ceil(duration_range / bin_width_fd))
            logger.debug(
                f"FD bin width={bin_width_fd if iqr else 'N/A'}; raw num_bins={num_bins_fd}"
            )
        else:
            # Fallback: square-root choice
            num_bins_fd = max(
                1, round(math.sqrt(total_sessions)) if total_sessions > 0 else 8
            )

        # Clamp to a readable range to keep labels and CASE complexity sane
        num_bins_fd = max(5, min(12, num_bins_fd))

        bin_ranges = _generate_dynamic_bins(
            min_duration, max_duration, num_bins=num_bins_fd
        )
        logger.debug(f"Generated {len(bin_ranges)} bin ranges: {bin_ranges}")

        # Process histogram rows
        logger.debug("Processing histogram rows...")
        histogram = []
        for row in histogram_rows:
            if len(row) < 2:
                logger.warning(f"Skipping malformed histogram row: {row}")
                continue
            bin_num = row[0]
            count = row[1]
            if 1 <= bin_num <= len(bin_ranges):
                if (
                    bin_num - 1 < len(bin_ranges)
                    and len(bin_ranges[bin_num - 1]) >= 3
                ):
                    bin_start, bin_end, _ = bin_ranges[bin_num - 1]
                    histogram.append(
                        {
                            "bin_start": bin_start,
                            "bin_end": (
                                bin_end if bin_end != float("inf") else None
                            ),
                            "count": count,
                        }
                    )
                else:
                    logger.warning(
                        f"Invalid bin configuration for bin_num={bin_num}, "
                        f"bin_ranges length={len(bin_ranges)}, "
                        f"tuple length={len(bin_ranges[bin_num - 1]) if bin_num - 1 < len(bin_ranges) else 'N/A'}"
                    )
            else:
                logger.warning(
                    f"Bin number {bin_num} out of range [1, {len(bin_ranges)}]"
                )

        # Process activity by hour
        logger.debug(f"Retrieved {len(hour_rows)} activity by hour rows")
        activity_by_hour = [
            {"hour": int(row[0]), "count": int(row[1])}
            for row in hour_rows
            if len(row) >= 2
        ]

        # Process activity by day of week
        logger.debug(f"Retrieved {len(dow_rows)} activity by day of week rows")
        day_names = [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]
        activity_by_day_of_week = []
        for row in dow_rows:
            if len(row) < 2:
                logger.warning(f"Skipping malformed day of week row: {row}")
                continue
            day_num = int(row[0])
            if 0 <= day_num < 7:
                activity_by_day_of_week.append(
                    {
                        "day": day_num,
                        "day_name": day_names[day_num],
                        "count": int(row[1]),
                    }
                )

        # Process activity over time
        logger.debug(f"Retrieved {len(time_rows)} activity over time rows")
        activity_over_time = [
            {"date": row[0].strftime("%Y-%m-%d"), "count": int(row[1])}
            for row in time_rows
            if len(row) >= 2 and row[0] is not None
        ]

        logger.info(
            f"Quest analytics completed for quest_id={quest_id}: "
            f"total_sessions={total_sessions}, histogram_bins={len(histogram)}, "
            f"hour_data_points={len(activity_by_hour)}, "
            f"dow_data_points={len(activity_by_day_of_week)}, "
            f"time_series_points={len(activity_over_time)}"
        )

        return QuestAnalytics(
            average_duration_seconds=avg_duration,
            standard_deviation_seconds=stddev_duration,
            histogram=histogram,
            activity_by_hour=activity_by_hour,
            activity_by_day_of_week=activity_by_day_of_week,
            activity_over_time=activity_over_time,
            total_sessions=total_sessions,
            completed_sessions=completed_sessions,
            active_sessions=active_sessions,
        )
    except Exception as e:
        logger.error(
            f"Error in get_quest_analytics for quest_id={quest_id}: {e}", exc_info=True
        )
        raise


def get_quest_analytics_batch(
    quest_ids: list[int], lookback_days: int = 90
) -> dict[int, QuestAnalytics]:
    """Get analytics for multiple quests in a single batch operation.

    Efficiently fetches analytics for multiple quests to avoid N+1 query pattern.

    Args:
        quest_ids: List of quest IDs to fetch analytics for
        lookback_days: Number of days to look back (default 90)

    Returns:
        Dictionary mapping quest_id to QuestAnalytics object
    """
    if not quest_ids:
        return {}

    logger.info(
        f"Getting batch quest analytics for {len(quest_ids)} quests, lookback_days={lookback_days}"
    )

    try:
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    except Exception as e:
        logger.error(f"Error calculating cutoff date: {e}", exc_info=True)
        raise

    # Fetch analytics for all quests
    analytics_dict = {}
    for quest_id in quest_ids:
        try:
            analytics = get_quest_analytics(quest_id, lookback_days)
            analytics_dict[quest_id] = analytics
        except Exception as e:
            logger.error(
                f"Error getting analytics for quest_id={quest_id}: {e}", exc_info=True
            )
            # Continue with other quests, skip this one
            continue

    logger.info(f"Batch analytics completed for {len(analytics_dict)} quests")
    return analytics_dict


def _generate_dynamic_bins(
    min_duration: float, max_duration: float, num_bins: int = 8
) -> list[tuple]:
    """Generate dynamic histogram bins based on duration range.

    Args:
        min_duration: Minimum duration in seconds
        max_duration: Maximum duration in seconds
        num_bins: Target number of bins (default 8)

    Returns:
        List of tuples (bin_start, bin_end, label)
    """
    if min_duration >= max_duration or max_duration <= 0:
        # Fallback to simple range
        return [(0, float("inf"), "All durations")]

    # Calculate the range and determine appropriate bin size
    duration_range = max_duration - min_duration

    # Use nice round numbers for bin sizes based on the range
    # Derive a raw bin width from desired bin count, then snap to nearest nice boundary
    raw_bin_size = duration_range / max(1, num_bins)

    if duration_range <= 300:  # Up to 5 minutes
        # Prefer 15s, 30s, 60s bins
        candidates = [15, 30, 60]
    elif duration_range <= 600:  # Up to 10 minutes
        # Prefer 30s, 60s, 120s bins
        candidates = [30, 60, 120]
    elif duration_range <= 900:  # Up to 15 minutes
        # Prefer 60s, 120s, 180s bins
        candidates = [60, 120, 180]
    elif duration_range <= 1200:  # Up to 20 minutes
        # Prefer 60s, 120s, 180s, 240s bins
        candidates = [60, 120, 180, 240]
    elif duration_range <= 1800:  # Up to 30 minutes
        # Prefer 120s, 180s, 240s, 300s bins
        candidates = [120, 180, 240, 300]
    elif duration_range <= 2700:  # Up to 45 minutes
        # Prefer 300s, 600s bins
        candidates = [300, 600]
    elif duration_range <= 3600:  # Up to 1 hour
        # Prefer 300s, 600s, 900s bins (5, 10, 15 min)
        candidates = [300, 600, 900]
    elif duration_range <= 5400:  # Up to 1.5 hours
        # Prefer 600s, 900s, 1200s bins (10, 15, 20 min)
        candidates = [600, 900, 1200]
    elif duration_range <= 7200:  # Up to 2 hours
        # Prefer 900s, 1200s, 1800s bins (15, 20, 30 min)
        candidates = [900, 1200, 1800]
    else:
        # Prefer 30-60-120 minute bins (with 2h+ ranges)
        candidates = [1800, 3600, 7200]

    # Snap to the nearest candidate
    bin_size = min(candidates, key=lambda c: abs(c - raw_bin_size))
    # Ensure a practical minimum (allow 30s for short quests)
    bin_size = max(bin_size, 30)

    # Generate bins
    bins = []
    current = 0
    bin_count = 0

    while current < max_duration and bin_count < num_bins - 1:
        next_boundary = current + bin_size
        bins.append((current, next_boundary))
        current = next_boundary
        bin_count += 1

    # Add final bin for remaining durations
    bins.append((current, float("inf")))

    # Generate labels
    labeled_bins = []
    for bin_start, bin_end in bins:
        if bin_end == float("inf"):
            label = _format_duration_label(bin_start, is_open_ended=True)
        else:
            label = (
                f"{_format_duration_value(bin_start)}-{_format_duration_value(bin_end)}"
            )
        labeled_bins.append((bin_start, bin_end, label))

    return labeled_bins


def _format_duration_value(seconds: float) -> str:
    """Format duration value for display."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        minutes = int(seconds / 60)
        return f"{minutes}m"
    else:
        hours = seconds / 3600
        if hours == int(hours):
            return f"{int(hours)}h"
        else:
            return f"{hours:.1f}h"


def _format_duration_label(seconds: float, is_open_ended: bool = False) -> str:
    """Format duration as a readable label."""
    formatted = _format_duration_value(seconds)
    if is_open_ended:
        return f"{formatted}+"
    return formatted
