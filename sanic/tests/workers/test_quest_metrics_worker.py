from contextlib import contextmanager
from unittest.mock import MagicMock

import workers.quest_metrics_worker as metrics_worker


def test_clamp_to_smallint_handles_bounds_and_rounding():
    assert metrics_worker.clamp_to_smallint(-10) == 0
    assert metrics_worker.clamp_to_smallint(12.6) == 13
    assert metrics_worker.clamp_to_smallint(40000) == 32767


def test_extract_and_batch_quest_lengths_handles_mixed_data():
    metrics_data = {
        1: {"analytics_data": {"total_sessions": 150, "average_duration_seconds": 120.4}},
        2: {"analytics_data": {"total_sessions": 10, "average_duration_seconds": 90}},
        3: {"analytics_data": {"total_sessions": 200, "average_duration_seconds": None}},
        4: {"analytics_data": {"total_sessions": 500, "average_duration_seconds": 50000}},
        5: {"analytics_data": "invalid-shape"},
    }

    updates_with_value, updates_to_null = metrics_worker.extract_and_batch_quest_lengths(
        metrics_data=metrics_data,
        all_quest_ids=[1, 2, 3, 4, 5, 6],
        min_sessions=100,
    )

    updates_map = {quest_id: length for quest_id, length in updates_with_value}
    assert updates_map == {1: 120, 4: 32767}
    assert set(updates_to_null) == {2, 3, 5, 6}


def test_extract_and_batch_quest_lengths_empty_metrics_sets_all_to_null():
    updates_with_value, updates_to_null = metrics_worker.extract_and_batch_quest_lengths(
        metrics_data={},
        all_quest_ids=[10, 11],
        min_sessions=100,
    )

    assert updates_with_value == []
    assert updates_to_null == [10, 11]


def test_extract_and_batch_quest_lengths_single_session_below_threshold():
    updates_with_value, updates_to_null = metrics_worker.extract_and_batch_quest_lengths(
        metrics_data={
            7: {
                "analytics_data": {
                    "total_sessions": 1,
                    "average_duration_seconds": 180,
                }
            }
        },
        all_quest_ids=[7],
        min_sessions=2,
    )

    assert updates_with_value == []
    assert updates_to_null == [7]


def test_bulk_update_quest_lengths_batches_and_commits(monkeypatch):
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = None

    @contextmanager
    def _connection_ctx():
        yield conn

    monkeypatch.setattr(metrics_worker, "get_db_connection", _connection_ctx)

    metrics_worker.bulk_update_quest_lengths(
        updates_with_value=[(1, 100), (2, 200), (3, 300)],
        updates_to_null=[4, 5, 6],
        batch_size=2,
    )

    assert cursor.executemany.call_count == 4
    assert conn.commit.call_count == 4

    value_query_1, value_params_1 = cursor.executemany.call_args_list[0].args
    value_query_2, value_params_2 = cursor.executemany.call_args_list[1].args
    null_query_1, null_params_1 = cursor.executemany.call_args_list[2].args
    null_query_2, null_params_2 = cursor.executemany.call_args_list[3].args

    assert "SET length = %s" in value_query_1
    assert value_params_1 == [(100, 1), (200, 2)]
    assert value_params_2 == [(300, 3)]

    assert "SET length = NULL" in null_query_1
    assert null_params_1 == [(4,), (5,)]
    assert null_params_2 == [(6,)]
