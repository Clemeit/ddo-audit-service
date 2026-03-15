from datetime import datetime, timezone

import pytest

import business.quests as quests_business
from models.quest_session import QuestAnalytics


def test_get_quest_analytics_returns_empty_model_for_unknown_quest(monkeypatch):
    monkeypatch.setattr(
        quests_business.postgres_client,
        "get_quest_analytics_raw",
        lambda _quest_id, _cutoff_date: None,
    )

    analytics = quests_business.get_quest_analytics(quest_id=404, lookback_days=30)

    assert analytics.total_sessions == 0
    assert analytics.completed_sessions == 0
    assert analytics.active_sessions == 0
    assert analytics.histogram == []
    assert analytics.activity_by_hour == []
    assert analytics.activity_by_day_of_week == []
    assert analytics.activity_over_time == []


def test_get_quest_analytics_known_quest_processes_histogram_and_activity(monkeypatch):
    now = datetime(2026, 3, 15, 12, 0, 0, tzinfo=timezone.utc)
    captured = {}

    raw_analytics = (
        125.5,
        30.25,
        60.0,
        90.0,
        180.0,
        300.0,
        16,
        14,
        2,
        [(1, 3), (2, 5), (3, 99), ("malformed",)],
        [(0, 2), (13, 4), (1,)],
        [(0, 7), (6, 2), (8, 5), (2,)],
        [(now, 10), (None, 1)],
    )

    def _get_raw(quest_id, cutoff_date):
        captured["quest_id"] = quest_id
        captured["cutoff_date"] = cutoff_date
        return raw_analytics

    def _generate_bins(min_duration, max_duration, num_bins):
        captured["bin_args"] = (min_duration, max_duration, num_bins)
        return [
            (0, 120, "0s-2m"),
            (120, float("inf"), "2m+"),
        ]

    monkeypatch.setattr(
        quests_business.postgres_client,
        "get_quest_analytics_raw",
        _get_raw,
    )
    monkeypatch.setattr(quests_business, "_generate_dynamic_bins", _generate_bins)

    analytics = quests_business.get_quest_analytics(quest_id=99, lookback_days=30)

    assert captured["quest_id"] == 99
    assert captured["cutoff_date"].tzinfo is not None
    assert captured["bin_args"] == (60.0, 300.0, 5)

    assert analytics.average_duration_seconds == 125.5
    assert analytics.standard_deviation_seconds == 30.25
    assert analytics.total_sessions == 16
    assert analytics.completed_sessions == 14
    assert analytics.active_sessions == 2

    assert analytics.histogram == [
        {"bin_start": 0, "bin_end": 120, "count": 3},
        {"bin_start": 120, "bin_end": None, "count": 5},
    ]
    assert analytics.activity_by_hour == [
        {"hour": 0, "count": 2},
        {"hour": 13, "count": 4},
    ]
    assert analytics.activity_by_day_of_week == [
        {"day": 0, "day_name": "Monday", "count": 7},
        {"day": 6, "day_name": "Sunday", "count": 2},
    ]
    assert analytics.activity_over_time == [{"date": "2026-03-15", "count": 10}]


def test_get_quest_analytics_no_sessions_uses_zero_defaults(monkeypatch):
    raw_analytics = (
        None,
        None,
        0.0,
        None,
        None,
        0.0,
        0,
        0,
        0,
        [(1, 0)],
        [],
        [],
        [],
    )

    monkeypatch.setattr(
        quests_business.postgres_client,
        "get_quest_analytics_raw",
        lambda _quest_id, _cutoff_date: raw_analytics,
    )

    analytics = quests_business.get_quest_analytics(quest_id=7)

    assert analytics.average_duration_seconds is None
    assert analytics.standard_deviation_seconds is None
    assert analytics.total_sessions == 0
    assert analytics.completed_sessions == 0
    assert analytics.active_sessions == 0
    assert analytics.histogram == [{"bin_start": 0, "bin_end": None, "count": 0}]


def test_get_quest_analytics_raises_when_data_source_fails(monkeypatch):
    def _raise_error(_quest_id, _cutoff_date):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        quests_business.postgres_client,
        "get_quest_analytics_raw",
        _raise_error,
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        quests_business.get_quest_analytics(quest_id=1)


def test_get_quest_analytics_batch_returns_empty_for_empty_ids():
    assert quests_business.get_quest_analytics_batch([]) == {}


def test_get_quest_analytics_batch_skips_ids_that_raise(monkeypatch):
    calls = []

    def _get_quest_analytics(quest_id, lookback_days):
        calls.append((quest_id, lookback_days))
        if quest_id == 2:
            raise RuntimeError("bad quest")
        return QuestAnalytics(total_sessions=quest_id)

    monkeypatch.setattr(quests_business, "get_quest_analytics", _get_quest_analytics)

    results = quests_business.get_quest_analytics_batch([1, 2, 3], lookback_days=45)

    assert calls == [(1, 45), (2, 45), (3, 45)]
    assert set(results.keys()) == {1, 3}
    assert results[1].total_sessions == 1
    assert results[3].total_sessions == 3


@pytest.mark.parametrize(
    "min_duration,max_duration",
    [(0, 0), (50, 10), (-5, 0)],
)
def test_generate_dynamic_bins_returns_single_bucket_for_invalid_range(
    min_duration, max_duration
):
    bins = quests_business._generate_dynamic_bins(
        min_duration, max_duration, num_bins=8
    )

    assert bins == [(0, float("inf"), "All durations")]


def test_generate_dynamic_bins_short_range_uses_thirty_second_steps():
    bins = quests_business._generate_dynamic_bins(0, 240, num_bins=8)

    assert len(bins) == 8
    assert bins[0] == (0, 30, "0s-30s")
    assert bins[1] == (30, 60, "30s-1m")
    assert bins[-1] == (210, float("inf"), "3m+")


def test_generate_dynamic_bins_large_range_caps_bin_size_to_ten_minutes():
    bins = quests_business._generate_dynamic_bins(0, 20000, num_bins=2)

    assert bins[0] == (0, 600, "0s-10m")
    assert bins[1] == (600, float("inf"), "10m+")


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (59, "59s"),
        (60, "1m"),
        (3599, "59m"),
        (3600, "1h"),
        (5400, "1.5h"),
    ],
)
def test_format_duration_value_formats_seconds_minutes_and_hours(seconds, expected):
    assert quests_business._format_duration_value(seconds) == expected


def test_format_duration_label_handles_open_and_closed_ranges():
    assert quests_business._format_duration_label(300, is_open_ended=False) == "5m"
    assert quests_business._format_duration_label(300, is_open_ended=True) == "5m+"
