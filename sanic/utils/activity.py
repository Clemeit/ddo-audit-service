from datetime import datetime, timedelta
from typing import List, Optional, Any
from collections import Counter

# Tunable constants
_MAX_LEVEL = 34
_SUSPICIOUS_LEVELS = {1, 4, 7, 15}
_DEFAULT_BANK_LOCATION_IDS = [1879058850, 1879063720, 1879065023]

# Component weights (should sum to 1.0)
_WEIGHT_LEVEL = 0.4
_WEIGHT_LOCATION = 0.3
_WEIGHT_SESSION = 0.3

# Verdict tuning
_ACTIVE_THRESHOLD = 0.35  # score >= this is considered active
_UNCERTAIN_BAND = 0.10  # +/- band around threshold for low confidence


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


def _timespan_to_score_days(days: float) -> float:
    """
    Map a span (in days) to a 0..1 score where short bursts score low and
    longer spans score high. Saturates around ~1 month.
    """
    if days <= 1:
        return _scale(days, 0, 1, 0.0, 0.2)
    elif days <= 7:
        return _scale(days, 1, 7, 0.2, 0.6)
    elif days <= 30:
        return _scale(days, 7, 30, 0.6, 1.0)
    else:
        return 1.0


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
    character: dict,
    activities: List[dict],
    bank_location_ids: Optional[List[int]] = None,
) -> dict[str, Any]:
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
        "is_active": False,  # verdict
        "confidence": 0.0,  # 0..1 confidence in verdict
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
    # - Otherwise, reward observed level increases.
    # - Boost when increases are spread over time; penalize tight bursts.
    level_score = 0.5  # neutral baseline
    current_level = character.get("total_level")
    level_increases = 0

    # Collect timestamps when a level increase occurred
    increase_ts: List[datetime] = []
    for i in range(1, len(level_events)):
        prev_lvl = level_events[i - 1][1]
        curr_lvl = level_events[i][1]
        if curr_lvl > prev_lvl:
            level_increases += 1
            increase_ts.append(level_events[i][0])

    # Compute the span (in days) of level activity
    span_days = 0.0
    if len(increase_ts) >= 2:
        span_days = (increase_ts[-1] - increase_ts[0]).total_seconds() / (24 * 3600)
    elif len(level_events) >= 2:
        # Fallback to span of all level events if no detected increases
        span_days = (level_events[-1][0] - level_events[0][0]).total_seconds() / (
            24 * 3600
        )
    time_spread_score = _timespan_to_score_days(span_days)

    if current_level is not None:
        if current_level >= _MAX_LEVEL:
            # Max level: do not factor level progression (neutral)
            level_score = 0.5
        else:
            if level_increases > 0:
                # Any increase is a signal of active play; cap benefits after ~3 increases.
                progress_score = _clamp01(_scale(level_increases, 0, 3, 0.3, 1.0))
                # Blend in time spread so tight, bursty increases score lower.
                level_score = _clamp01(0.7 * progress_score + 0.3 * time_spread_score)
            else:
                # No increases
                if current_level in _SUSPICIOUS_LEVELS:
                    level_score = 0.1  # heavy penalty
                else:
                    level_score = 0.3  # moderate penalty
                # Further penalize if all level events are in a short time block (<= ~1 day)
                if len(level_events) >= 2 and span_days <= 1.0:
                    level_score = max(0.05, level_score - 0.1)

    # ---------------------
    # Location activity factor (volume > diversity)
    # ---------------------
    # Prioritize how many location activity events exist (volume), with
    # diversity and transitions as a secondary signal. Still penalize
    # heavy concentration in known bank hubs.
    if not location_events:
        location_score = 0.1  # No location changes observed
    else:
        locations = [loc for _, loc in location_events]
        n = len(locations)
        counts = Counter(locations)
        unique_count = len(counts)

        # Volume: more events => higher confidence of active play
        # Saturate around ~40 events.
        volume_score = _scale(n, 1, 40, 0.1, 1.0)

        # Diversity: combine unique locations and actual transitions
        diversity_ratio = unique_count / n  # uniqueness
        transitions = sum(1 for i in range(1, n) if locations[i] != locations[i - 1])
        transition_ratio = transitions / max(1, n - 1)  # movement between locations
        diversity_score = 0.5 * diversity_ratio + 0.5 * transition_ratio

        # Combine with stronger weight on volume
        location_score = 0.8 * volume_score + 0.2 * diversity_score

        # Penalize dominance of a bank hub
        top_loc, top_count = counts.most_common(1)[0]
        if top_loc in (bank_location_ids or []):
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

    score = round(_clamp01(score), 3)

    # Verdict and confidence
    is_active = score >= _ACTIVE_THRESHOLD
    # Confidence from margin to threshold
    margin_conf = _clamp01(abs(score - _ACTIVE_THRESHOLD) / _UNCERTAIN_BAND)
    # Evidence completeness (how many components had data)
    evidence = 0
    evidence += 1 if (current_level is not None or level_events) else 0
    evidence += 1 if location_events else 0
    evidence += 1 if avg_session is not None else 0
    evidence_weight = evidence / 3.0
    confidence = round(_clamp01(0.5 * margin_conf + 0.5 * evidence_weight), 3)

    result.update(
        {
            "score": score,
            "level_score": round(level_score, 3),
            "location_score": round(location_score, 3),
            "session_score": round(session_score, 3),
            "is_active": is_active,
            "confidence": confidence,
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
