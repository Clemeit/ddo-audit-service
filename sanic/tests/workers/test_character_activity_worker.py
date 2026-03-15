from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import MagicMock

import workers.character_activity_worker as activity_worker


def _mock_connection_with_rows(rows):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = None
    cursor.fetchall.return_value = rows

    @contextmanager
    def _connection_ctx():
        yield conn

    return conn, cursor, _connection_ctx


def test_fetch_character_batch_returns_ids_and_normalized_total_level(monkeypatch):
    conn, cursor, connection_ctx = _mock_connection_with_rows([(101, None), (102, 18)])
    monkeypatch.setattr(activity_worker, "get_db_connection", connection_ctx)

    result = activity_worker.fetch_character_batch(
        last_id=10,
        shard_count=4,
        shard_index=1,
        batch_size=2,
    )

    assert result == [(101, 0), (102, 18)]
    cursor.execute.assert_called_once()
    assert cursor.execute.call_args[0][1] == (10, 4, 1, 2)
    assert conn.cursor.call_count == 1


def test_fetch_activities_for_ids_returns_empty_dict_for_empty_ids():
    assert activity_worker.fetch_activities_for_ids([], lookback_days=30) == {}


def test_fetch_activities_for_ids_normalizes_rows_and_skips_invalid(monkeypatch):
    ts = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    rows = [
        (ts, 1, "status", {"value": True}),
        (ts, 1, "location", {"value": "401"}),
        (
            "2026-01-02T00:00:00+00:00",
            1,
            "total_level",
            {"total_level": 32, "classes": [{"name": "Barbarian", "level": 20}]},
        ),
        (ts, 1, "location", {"value": "not-an-int"}),
        (ts, 2, "status", {}),
        (ts, 2, "total_level", {"classes": [{"name": "Wizard", "level": 1}]}),
    ]
    _conn, cursor, connection_ctx = _mock_connection_with_rows(rows)
    monkeypatch.setattr(activity_worker, "get_db_connection", connection_ctx)
    monkeypatch.setattr(
        activity_worker,
        "datetime_to_datetime_string",
        lambda _value: "2026-01-01T00:00:00+00:00",
    )

    result = activity_worker.fetch_activities_for_ids([1, 2], lookback_days=90)

    cursor.execute.assert_called_once()
    assert cursor.execute.call_args[0][1] == ([1, 2], 90)

    assert len(result[1]) == 3
    assert result[1][0]["data"]["status"] is True
    assert result[1][1]["data"]["location_id"] == 401
    assert result[1][2]["data"]["total_level"] == 32
    assert result[1][2]["data"]["classes"][0]["name"] == "Barbarian"

    assert len(result[2]) == 1
    assert result[2][0]["data"]["classes"][0]["name"] == "Wizard"


def test_compute_updates_marks_active_and_handles_scoring_errors(monkeypatch):
    def _score(character, activities):
        if character["id"] == 1:
            assert activities == [{"data": {"status": True}}]
            return {"is_active": True}
        raise RuntimeError("scoring failed")

    monkeypatch.setattr(activity_worker, "calculate_active_playstyle_score", _score)

    updates = activity_worker.compute_updates(
        chars=[(1, 20), (2, 12)],
        activities={
            1: [{"data": {"status": True}}],
            2: [{"data": {"status": False}}],
        },
    )

    assert updates[0][0] == 1
    assert updates[0][1] is True
    assert updates[1][0] == 2
    assert updates[1][1] is False
    assert updates[0][2].tzinfo is not None
    assert updates[1][2].tzinfo is not None
