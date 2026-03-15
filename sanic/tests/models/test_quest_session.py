from datetime import datetime

import pytest
from pydantic import ValidationError

from models.quest_session import QuestAnalytics, QuestMetrics, QuestSession


def test_quest_session_parses_datetime_fields_and_model_dump():
    session = QuestSession(
        character_id=10,
        quest_id=20,
        entry_timestamp="2026-03-15T10:00:00Z",
        exit_timestamp="2026-03-15T10:45:00Z",
        duration_seconds=2700.0,
    )

    assert isinstance(session.entry_timestamp, datetime)
    assert isinstance(session.exit_timestamp, datetime)
    assert session.model_dump()["duration_seconds"] == 2700.0


def test_quest_session_missing_required_fields():
    with pytest.raises(ValidationError):
        QuestSession(quest_id=20, entry_timestamp="2026-03-15T10:00:00Z")

    with pytest.raises(ValidationError):
        QuestSession(character_id=10, entry_timestamp="2026-03-15T10:00:00Z")


def test_quest_analytics_defaults_and_list_factories_not_shared():
    first = QuestAnalytics()
    second = QuestAnalytics()

    first.histogram.append({"bin_start": 0, "bin_end": 60, "count": 1})

    assert first.total_sessions == 0
    assert first.completed_sessions == 0
    assert first.active_sessions == 0
    assert second.histogram == []


def test_quest_analytics_model_dump_and_optional_none():
    analytics = QuestAnalytics(
        average_duration_seconds=None,
        standard_deviation_seconds=None,
        activity_by_hour=[{"hour": 10, "count": 4}],
        total_sessions=5,
    )

    dumped = analytics.model_dump()
    assert dumped["average_duration_seconds"] is None
    assert dumped["activity_by_hour"] == [{"hour": 10, "count": 4}]
    assert dumped["total_sessions"] == 5


def test_quest_metrics_nested_analytics_and_validation():
    metrics = QuestMetrics(
        quest_id=1,
        analytics_data=QuestAnalytics(total_sessions=7),
        updated_at="2026-03-15T12:00:00Z",
    )

    dumped = metrics.model_dump()
    assert dumped["quest_id"] == 1
    assert dumped["analytics_data"]["total_sessions"] == 7
    assert dumped["heroic_xp_per_minute_relative"] is None

    with pytest.raises(ValidationError):
        QuestMetrics(quest_id=1, updated_at="2026-03-15T12:00:00Z")
