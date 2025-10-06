from datetime import datetime, timedelta
from typing import List, Optional
from collections import Counter

# Tunable constants
_MAX_LEVEL = 34
_SUSPICIOUS_LEVELS = {1, 4, 7, 15}
_DEFAULT_BANK_LOCATION_IDS = [10, 11, 12, 13]

# Component weights (should sum to 1.0)
_WEIGHT_LEVEL = 0.4
_WEIGHT_LOCATION = 0.3
_WEIGHT_SESSION = 0.3


def _parse_ts(ts: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _scale(
    value: float,
    in_min: float,
    in_max: float,
    out_min: float = 0.0,
    out_max: float = 1.0,
) -> float:
    if in_max <= in_min:
        return out_min
    t = (value - in_min) / (in_max - in_min)
    return out_min + (out_max - out_min) * _clamp01(t)


def _extract_activity_streams(activities: List[dict]):
    """Split mixed activity records into typed streams."""
    status_events = []  # List[tuple[datetime, bool]]
    location_events = []  # List[tuple[datetime, int]]
    level_events = []  # List[tuple[datetime, int]]

    for a in activities:
        ts = _parse_ts(a.get("timestamp"))
        if not ts:
            continue
        data = a.get("data") or {}
        if "status" in data:
            status_events.append((ts, bool(data["status"])))
        elif "location_id" in data:
            try:
                location_events.append((ts, int(data["location_id"])))
            except Exception:
                pass
        elif "total_level" in data:
            try:
                level_events.append((ts, int(data["total_level"])))
            except Exception:
                pass

    status_events.sort(key=lambda x: x[0])  # chronological
    location_events.sort(key=lambda x: x[0])  # chronological
    level_events.sort(key=lambda x: x[0])  # chronological
    return status_events, location_events, level_events


def calculate_active_playstyle_score(
    activities: List[dict],
    bank_location_ids: Optional[List[int]] = None,
) -> dict[str, float]:
    """
    Calculate how actively a character is played vs being used as storage.

    Returns:
        Float between 0.0 (likely a bank/mule character) and 1.0 (actively played)
    """
    result = {
        "score": 0.0,
        "level_score": 0.0,
        "location_score": 0.0,
        "session_score": 0.0,
        "weights": {
            "level": _WEIGHT_LEVEL,
            "location": _WEIGHT_LOCATION,
            "session": _WEIGHT_SESSION,
        },
    }

    if not activities:
        return result

    bank_location_ids = bank_location_ids or _DEFAULT_BANK_LOCATION_IDS
    status_events, location_events, level_events = _extract_activity_streams(activities)

    # ---------------------
    # Level activity factor
    # ---------------------
    # - If max level: treat level factor as neutral (not considered).
    # - Otherwise, reward any observed level increases.
    # - Heavier penalty if sitting at suspicious levels w/o any increases.
    level_score = 0.5  # neutral baseline
    current_level = level_events[-1][1] if level_events else None
    level_increases = 0
    for i in range(1, len(level_events)):
        if level_events[i][1] > level_events[i - 1][1]:
            level_increases += 1

    if current_level is not None:
        if current_level >= _MAX_LEVEL:
            # Max level: do not factor level progression (neutral)
            level_score = 0.5
        else:
            if level_increases > 0:
                # Any increase is a strong signal of active play
                # Cap minor additional benefit after 3 increases
                level_score = _clamp01(_scale(level_increases, 0, 3, 0.3, 1.0))
            else:
                # No increases
                # Slightly above zero to avoid overpowering other signals
                level_score = 0.1

    # ---------------------
    # Location diversity factor
    # ---------------------
    # Characters who stay in one location score lower, especially if that location
    # is one of the configured bank hubs.
    if not location_events:
        location_score = 0.5  # neutral if we have no data
    else:
        locations = [loc for _, loc in location_events]
        n = len(locations)
        counts = Counter(locations)
        unique_count = len(counts)
        diversity_ratio = unique_count / n  # 1.0 => all different, 0.0 => impossible
        # Penalize high concentration in a known bank location
        top_loc, top_count = counts.most_common(1)[0]
        location_score = diversity_ratio
        if top_loc in (bank_location_ids or []):
            # Reduce score if the most common location is a bank hub
            # The reduction scales with how dominant that location is.
            dominance = top_count / n  # 0..1
            location_score *= 1.0 - 0.4 * dominance  # up to 40% reduction
        location_score = _clamp01(location_score)

    # ---------------------
    # Session duration factor
    # ---------------------
    avg_session = calculate_average_session_duration(activities)
    if avg_session is None:
        session_score = 0.5  # neutral if unknown
    else:
        mins = avg_session.total_seconds() / 60.0
        # Map duration to score:
        #  0-10m  -> ~0.0..0.3
        # 10-30m  -> ~0.3..0.6
        # 30-60m  -> ~0.6..0.85
        # 60-120m -> ~0.85..1.0
        if mins <= 10:
            session_score = _scale(mins, 0, 10, 0.0, 0.3)
        elif mins <= 30:
            session_score = _scale(mins, 10, 30, 0.3, 0.6)
        elif mins <= 60:
            session_score = _scale(mins, 30, 60, 0.6, 0.85)
        elif mins <= 120:
            session_score = _scale(mins, 60, 120, 0.85, 1.0)
        else:
            session_score = 1.0

    # Combine components
    score = (
        _WEIGHT_LEVEL * level_score
        + _WEIGHT_LOCATION * location_score
        + _WEIGHT_SESSION * session_score
    )

    # Extra penalty if sitting at suspicious levels with no level increases
    if (
        current_level is not None
        and current_level in _SUSPICIOUS_LEVELS
        and level_increases == 0
        and (current_level < _MAX_LEVEL)  # do not penalize capped characters
    ):
        score -= 0.2

    score = round(_clamp01(score), 3)
    result.update(
        {
            "score": score,
            "level_score": round(level_score, 3),
            "location_score": round(location_score, 3),
            "session_score": round(session_score, 3),
        }
    )
    return result


def calculate_average_session_duration(
    activities: List[dict],
) -> Optional[timedelta]:
    """
    Calculate the average duration of play sessions from CHARACTER_ACTIVITY_STATUS events.

    Args:
        activities: Mixed activity list; status events are detected by data.status.

    Returns:
        Average session duration as timedelta, or None if no complete sessions found
    """
    # Extract and sort status events chronologically
    status_events, _, _ = _extract_activity_streams(activities)
    if len(status_events) < 2:
        return None

    session_durations: List[timedelta] = []
    session_start: Optional[datetime] = None
    last_status: Optional[bool] = None

    for ts, status in status_events:
        # Start a new session when transitioning from logged out -> logged in
        if status and (last_status is None or last_status is False):
            session_start = ts

        # Close a session on transition from logged in -> logged out
        elif not status and last_status is True and session_start is not None:
            if ts >= session_start:
                session_durations.append(ts - session_start)
            session_start = None

        last_status = status

    if not session_durations:
        return None

    # Average duration
    total = sum(session_durations, timedelta())
    return total / len(session_durations)
