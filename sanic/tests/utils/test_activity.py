from datetime import datetime, timedelta

import pytest

from utils.activity import (
    _clamp01,
    _extract_activity_streams,
    _parse_ts,
    _scale,
    _timespan_to_score_days,
    calculate_active_playstyle_score,
    calculate_average_session_duration,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_BASE_TS = datetime(2026, 1, 1, 0, 0, 0)


def _activity(*, days: int = 0, minutes: int = 0, data: dict | None = None) -> dict:
    ts = _BASE_TS + timedelta(days=days, minutes=minutes)
    return {
        "timestamp": ts.isoformat(),
        "data": data or {},
    }


# ===========================================================================
# _parse_ts
# ===========================================================================


class TestParseTs:
    def test_valid_iso_timestamp(self):
        assert _parse_ts("2026-01-01T12:30:45") == datetime(2026, 1, 1, 12, 30, 45)

    @pytest.mark.parametrize("raw", ["", "not-a-date", "2026/01/01", None])
    def test_invalid_timestamp_returns_none(self, raw):
        assert _parse_ts(raw) is None


# ===========================================================================
# _clamp01
# ===========================================================================


class TestClamp01:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (-5.0, 0.0),
            (0.0, 0.0),
            (0.42, 0.42),
            (1.0, 1.0),
            (2.5, 1.0),
        ],
    )
    def test_clamps_to_unit_interval(self, value, expected):
        assert _clamp01(value) == pytest.approx(expected)


# ===========================================================================
# _scale
# ===========================================================================


class TestScale:
    def test_scales_midpoint_between_ranges(self):
        assert _scale(5, 0, 10, 0, 100) == pytest.approx(50)

    def test_clamps_below_input_range(self):
        assert _scale(-1, 0, 10, 0, 100) == pytest.approx(0)

    def test_clamps_above_input_range(self):
        assert _scale(99, 0, 10, 0, 100) == pytest.approx(100)

    def test_invalid_input_range_returns_out_min(self):
        assert _scale(5, 10, 10, 0.3, 0.9) == pytest.approx(0.3)
        assert _scale(5, 10, 9, 0.3, 0.9) == pytest.approx(0.3)


# ===========================================================================
# _timespan_to_score_days
# ===========================================================================


class TestTimespanToScoreDays:
    @pytest.mark.parametrize(
        "days,expected",
        [
            (0, 0.0),
            (1, 0.2),
            (4, 0.4),
            (7, 0.6),
            (30, 1.0),
            (45, 1.0),
        ],
    )
    def test_piecewise_mapping(self, days, expected):
        assert _timespan_to_score_days(days) == pytest.approx(expected)


# ===========================================================================
# _extract_activity_streams
# ===========================================================================


class TestExtractActivityStreams:
    def test_splits_streams_filters_invalid_and_sorts(self):
        activities = [
            _activity(days=2, data={"location_id": "300"}),
            {"timestamp": "bad-ts", "data": {"status": True}},
            _activity(days=3, data={"status": False, "location_id": 999}),
            _activity(days=1, data={"total_level": "12"}),
            _activity(days=0, data={"status": 1}),
            _activity(days=4, data={"location_id": "not-an-int"}),
            _activity(days=5, data={"total_level": "not-an-int"}),
            _activity(days=6, data={"other": "ignored"}),
        ]

        status_events, location_events, level_events = _extract_activity_streams(
            activities
        )

        assert [status for _, status in status_events] == [True, False]
        assert [loc for _, loc in location_events] == [300]
        assert [lvl for _, lvl in level_events] == [12]
        assert status_events[0][0] < status_events[1][0]


# ===========================================================================
# calculate_average_session_duration
# ===========================================================================


class TestCalculateAverageSessionDuration:
    def test_returns_none_with_insufficient_status_events(self):
        activities = [_activity(data={"status": True})]
        assert calculate_average_session_duration(activities) is None

    def test_averages_complete_sessions(self):
        activities = [
            _activity(minutes=60, data={"status": False}),
            _activity(minutes=0, data={"status": True}),
            _activity(minutes=90, data={"location_id": 500}),
            _activity(minutes=180, data={"status": False}),
            _activity(minutes=120, data={"status": True}),
        ]

        avg = calculate_average_session_duration(activities)
        assert avg == timedelta(minutes=60)

    def test_ignores_incomplete_open_session(self):
        activities = [
            _activity(minutes=0, data={"status": True}),
            _activity(minutes=10, data={"status": False}),
            _activity(minutes=20, data={"status": True}),
        ]

        avg = calculate_average_session_duration(activities)
        assert avg == timedelta(minutes=10)

    def test_returns_none_when_no_complete_sessions(self):
        activities = [
            _activity(minutes=0, data={"status": False}),
            _activity(minutes=20, data={"status": True}),
            _activity(minutes=40, data={"status": True}),
        ]
        assert calculate_average_session_duration(activities) is None


# ===========================================================================
# calculate_active_playstyle_score
# ===========================================================================


class TestCalculateActivePlaystyleScore:
    def test_returns_defaults_for_empty_activities(self):
        result = calculate_active_playstyle_score({"total_level": 10}, [])

        assert result["score"] == 0.0
        assert result["level_score"] == 0.0
        assert result["location_score"] == 0.0
        assert result["session_score"] == 0.0
        assert result["is_active"] is False
        assert result["confidence"] == 0.0
        assert result["weights"] == {
            "level": 0.4,
            "location": 0.3,
            "session": 0.3,
        }

    def test_high_activity_profile_scores_active(self):
        activities = [
            _activity(days=0, data={"total_level": 5}),
            _activity(days=10, data={"total_level": 6}),
            _activity(days=20, data={"total_level": 7}),
            _activity(days=2, minutes=0, data={"status": True}),
            _activity(days=2, minutes=90, data={"status": False}),
        ]

        for i in range(40):
            loc = 100 if i % 2 == 0 else 101
            activities.append(_activity(days=1, minutes=i, data={"location_id": loc}))

        result = calculate_active_playstyle_score({"total_level": 10}, activities)

        assert result["is_active"] is True
        assert result["score"] > 0.8
        assert result["level_score"] > 0.8
        assert result["location_score"] > 0.8
        assert result["session_score"] > 0.9
        assert result["confidence"] == pytest.approx(1.0)

    def test_bank_mule_like_profile_scores_inactive(self):
        activities = [
            _activity(minutes=0, data={"status": True}),
            _activity(minutes=5, data={"status": False}),
        ]

        for i in range(5):
            activities.append(_activity(minutes=i, data={"location_id": 1879058850}))

        result = calculate_active_playstyle_score({"total_level": 1}, activities)

        assert result["is_active"] is False
        assert result["score"] < 0.2
        assert result["level_score"] == pytest.approx(0.1)
        assert result["location_score"] < 0.2
        assert result["session_score"] < 0.2

    def test_max_level_character_gets_neutral_level_floor(self):
        activities = [_activity(data={"location_id": 42})]
        result = calculate_active_playstyle_score({"total_level": 34}, activities)
        assert result["level_score"] == pytest.approx(0.5)

    def test_short_level_burst_is_penalized_vs_spread_progress(self):
        short_burst = [
            _activity(days=0, data={"total_level": 5}),
            _activity(days=0, minutes=720, data={"total_level": 6}),
        ]
        spread_progress = [
            _activity(days=0, data={"total_level": 5}),
            _activity(days=5, data={"total_level": 6}),
        ]

        short_result = calculate_active_playstyle_score(
            {"total_level": 10}, short_burst
        )
        spread_result = calculate_active_playstyle_score(
            {"total_level": 10}, spread_progress
        )

        assert short_result["level_score"] < spread_result["level_score"]
        assert short_result["score"] < spread_result["score"]

    def test_custom_bank_location_ids_change_location_penalty(self):
        activities = []
        for i in range(10):
            loc = 999 if i < 8 else 1000
            activities.append(_activity(minutes=i, data={"location_id": loc}))

        penalized = calculate_active_playstyle_score(
            {"total_level": 10},
            activities,
            bank_location_ids=[999],
        )
        unpenalized = calculate_active_playstyle_score(
            {"total_level": 10},
            activities,
            bank_location_ids=[12345],
        )

        assert penalized["location_score"] < unpenalized["location_score"]
        assert penalized["score"] < unpenalized["score"]

    def test_malformed_activity_timestamps_do_not_crash_scoring(self):
        activities = [{"timestamp": "definitely-not-a-ts", "data": {"status": True}}]
        result = calculate_active_playstyle_score({"total_level": 10}, activities)

        assert result["is_active"] is False
        assert result["level_score"] == pytest.approx(0.3)
        assert result["location_score"] == pytest.approx(0.1)
        assert result["session_score"] == pytest.approx(0.5)
        assert result["score"] == pytest.approx(0.3)
