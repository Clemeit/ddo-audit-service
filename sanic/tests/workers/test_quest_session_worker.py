from contextlib import contextmanager
from datetime import datetime, timezone

import services.redis as redis_service
import workers.quest_session_worker as quest_worker


def _ts(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 1, 1, hour, minute, tzinfo=timezone.utc)


def test_env_int_and_env_float_handle_valid_invalid_and_missing(monkeypatch):
    monkeypatch.setenv("QUEST_WORKER_BATCH_SIZE", "250")
    monkeypatch.setenv("QUEST_WORKER_SLEEP_SECS", "2.5")

    assert quest_worker.env_int("QUEST_WORKER_BATCH_SIZE", 10) == 250
    assert quest_worker.env_float("QUEST_WORKER_SLEEP_SECS", 1.0) == 2.5

    monkeypatch.setenv("QUEST_WORKER_BATCH_SIZE", "not-int")
    monkeypatch.setenv("QUEST_WORKER_SLEEP_SECS", "not-float")

    assert quest_worker.env_int("QUEST_WORKER_BATCH_SIZE", 10) == 10
    assert quest_worker.env_float("QUEST_WORKER_SLEEP_SECS", 1.0) == 1.0
    assert quest_worker.env_int("QUEST_WORKER_MISSING", 7) == 7
    assert quest_worker.env_float("QUEST_WORKER_MISSING_FLOAT", 9.5) == 9.5


def test_format_duration_formats_seconds_minutes_and_hours():
    assert quest_worker.format_duration(0) == "0.0s"
    assert quest_worker.format_duration(90) == "1.5m"
    assert quest_worker.format_duration(7200) == "2.0h"


def test_process_character_activities_entry_and_exit_records_completed_session(
    monkeypatch,
):
    monkeypatch.setattr(quest_worker, "QUEST_AREA_TO_ID", {100: 1})
    monkeypatch.setattr(quest_worker, "QUEST_ID_TO_AREA", {1: 100})

    activities = [
        (_ts(1), "location", 100, True, {}),
        (_ts(2), "location", 999, True, {}),
    ]

    sessions, final_session, status_seen = quest_worker.process_character_activities(
        character_id=7,
        activities=activities,
        initial_session=None,
        initial_total_level=32,
        initial_classes=[{"name": "Rogue", "level": 20}],
        initial_group_id=11,
    )

    assert status_seen is False
    assert final_session is None
    assert len(sessions) == 1
    assert sessions[0]["character_id"] == 7
    assert sessions[0]["quest_id"] == 1
    assert sessions[0]["entry_timestamp"] == _ts(1)
    assert sessions[0]["exit_timestamp"] == _ts(2)
    assert sessions[0]["entry_total_level"] == 32
    assert sessions[0]["entry_classes"] == [{"name": "Rogue", "level": 20}]
    assert sessions[0]["entry_group_id"] == 11


def test_process_character_activities_area_change_closes_old_and_opens_new(monkeypatch):
    monkeypatch.setattr(quest_worker, "QUEST_AREA_TO_ID", {100: 1, 200: 2})
    monkeypatch.setattr(quest_worker, "QUEST_ID_TO_AREA", {1: 100, 2: 200})

    activities = [
        (_ts(1), "location", 100, True, {}),
        (_ts(2), "location", 200, True, {}),
    ]

    sessions, final_session, status_seen = quest_worker.process_character_activities(
        character_id=8,
        activities=activities,
        initial_session=None,
    )

    assert status_seen is False
    assert len(sessions) == 1
    assert sessions[0]["quest_id"] == 1
    assert sessions[0]["exit_timestamp"] == _ts(2)
    assert final_session is not None
    assert final_session.quest_id == 2
    assert final_session.entry_timestamp == _ts(2)


def test_process_character_activities_status_event_clears_active_session(monkeypatch):
    monkeypatch.setattr(quest_worker, "QUEST_AREA_TO_ID", {100: 1})
    monkeypatch.setattr(quest_worker, "QUEST_ID_TO_AREA", {1: 100})

    initial_session = quest_worker.QuestSession(
        character_id=9,
        quest_id=1,
        entry_timestamp=_ts(0),
    )
    activities = [(_ts(1), "status", None, False, {"value": False})]

    sessions, final_session, status_seen = quest_worker.process_character_activities(
        character_id=9,
        activities=activities,
        initial_session=initial_session,
    )

    assert sessions == []
    assert final_session is None
    assert status_seen is True


def test_process_character_activities_ignores_duplicate_location_events(monkeypatch):
    monkeypatch.setattr(quest_worker, "QUEST_AREA_TO_ID", {100: 1})
    monkeypatch.setattr(quest_worker, "QUEST_ID_TO_AREA", {1: 100})

    activities = [
        (_ts(1), "location", 100, True, {}),
        (_ts(1, 5), "location", 100, True, {}),
        (_ts(1, 10), "location", 100, True, {}),
    ]

    sessions, final_session, status_seen = quest_worker.process_character_activities(
        character_id=10,
        activities=activities,
        initial_session=None,
    )

    assert sessions == []
    assert status_seen is False
    assert final_session is not None
    assert final_session.quest_id == 1
    assert final_session.entry_timestamp == _ts(1)


def test_process_batch_returns_unchanged_checkpoint_when_no_activities(monkeypatch):
    monkeypatch.setattr(
        quest_worker,
        "get_unprocessed_quest_activities",
        lambda *_args, **_kwargs: [],
    )

    start_ts = _ts(3)
    result = quest_worker.process_batch(start_ts, 123, batch_size=50)

    assert result == (start_ts, 123, 0, 0)


def test_process_batch_filters_excluded_sessions_and_updates_redis(monkeypatch):
    monkeypatch.setattr(quest_worker, "QUEST_AREA_TO_ID", {100: 10, 200: 20})
    monkeypatch.setattr(quest_worker, "QUEST_ID_TO_AREA", {10: 100, 20: 200})
    monkeypatch.setattr(quest_worker, "EXCLUDED_QUEST_IDS", {20})

    activities = [
        (1, _ts(1), "location", 100, True, {}),
        (1, _ts(2), "location", 999, True, {}),
        (2, _ts(1), "location", 200, True, {}),
        (2, _ts(3), "location", 999, True, {}),
    ]

    monkeypatch.setattr(
        quest_worker,
        "get_unprocessed_quest_activities",
        lambda *_args, **_kwargs: activities,
    )
    monkeypatch.setattr(
        quest_worker,
        "batch_get_active_quest_session_states",
        lambda _ids: {1: None, 2: None},
    )
    monkeypatch.setattr(
        quest_worker,
        "get_characters_by_ids_as_dict",
        lambda _ids: {},
    )

    captured = {}

    def _capture_updates(set_map, clear_list):
        captured["set_map"] = dict(set_map)
        captured["clear_list"] = list(clear_list)

    def _capture_insert(sessions):
        captured["inserted"] = list(sessions)

    monkeypatch.setattr(
        quest_worker,
        "batch_update_active_quest_session_states",
        _capture_updates,
    )
    monkeypatch.setattr(quest_worker, "bulk_insert_quest_sessions", _capture_insert)

    new_ts, new_char_id, activities_processed, sessions_created = (
        quest_worker.process_batch(
            last_timestamp=_ts(0),
            max_character_id_at_timestamp=0,
            batch_size=100,
        )
    )

    assert activities_processed == 4
    assert sessions_created == 1
    assert new_ts == _ts(3)
    assert new_char_id == 2

    assert len(captured["inserted"]) == 1
    assert captured["inserted"][0]["quest_id"] == 10
    assert captured["set_map"] == {}
    assert set(captured["clear_list"]) == {1, 2}


def test_process_batch_continues_when_saved_session_state_is_invalid(monkeypatch):
    monkeypatch.setattr(quest_worker, "QUEST_AREA_TO_ID", {100: 10})
    monkeypatch.setattr(quest_worker, "QUEST_ID_TO_AREA", {10: 100})
    monkeypatch.setattr(quest_worker, "EXCLUDED_QUEST_IDS", set())

    activities = [
        (1, _ts(1), "location", 100, True, {}),
        (2, _ts(2), "location", 100, True, {}),
    ]

    monkeypatch.setattr(
        quest_worker,
        "get_unprocessed_quest_activities",
        lambda *_args, **_kwargs: activities,
    )
    monkeypatch.setattr(
        quest_worker,
        "batch_get_active_quest_session_states",
        lambda _ids: {
            1: {"quest_id": "10", "entry_timestamp": "not-a-timestamp"},
            2: None,
        },
    )
    monkeypatch.setattr(
        quest_worker,
        "get_characters_by_ids_as_dict",
        lambda _ids: {
            1: {
                "total_level": 20,
                "classes": [{"name": "Fighter", "level": 20}],
                "group_id": 7,
            },
            2: {},
        },
    )

    captured = {}

    def _capture_updates(set_map, clear_list):
        captured["set_map"] = dict(set_map)
        captured["clear_list"] = list(clear_list)

    monkeypatch.setattr(
        quest_worker,
        "batch_update_active_quest_session_states",
        _capture_updates,
    )
    monkeypatch.setattr(quest_worker, "bulk_insert_quest_sessions", lambda _rows: None)

    new_ts, new_char_id, activities_processed, sessions_created = (
        quest_worker.process_batch(
            last_timestamp=_ts(0),
            max_character_id_at_timestamp=0,
            batch_size=100,
        )
    )

    assert activities_processed == 2
    assert sessions_created == 0
    assert new_ts == _ts(2)
    assert new_char_id == 2
    assert captured["clear_list"] == []
    assert set(captured["set_map"].keys()) == {1, 2}
    assert captured["set_map"][1]["entry_total_level"] == 20
    assert captured["set_map"][1]["entry_group_id"] == 7
    assert captured["set_map"][2]["entry_total_level"] is None


def test_checkpoint_save_and_load_round_trip(monkeypatch):
    class _FakeRedisClient:
        def __init__(self):
            self.store = {}

        def get(self, key):
            return self.store.get(key)

        def setex(self, key, _ttl, value):
            self.store[key] = value

    fake_client = _FakeRedisClient()

    @contextmanager
    def _client_ctx():
        yield fake_client

    monkeypatch.setattr(redis_service, "get_redis_client", _client_ctx)

    checkpoint_ts = _ts(5)
    quest_worker.set_quest_worker_checkpoint(checkpoint_ts, 456)
    restored = quest_worker.get_quest_worker_checkpoint()

    assert restored == (checkpoint_ts, 456)


def test_clamp_future_checkpoint_resets_to_window_and_zeroes_character_id(monkeypatch):
    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(quest_worker, "datetime", _FrozenDateTime)

    future_ts = datetime(2026, 1, 3, 0, 0, tzinfo=timezone.utc)
    clamped_ts, clamped_char_id = quest_worker.clamp_future_checkpoint(
        future_ts,
        999,
        24,
    )

    assert clamped_ts == datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    assert clamped_char_id == 0


def test_clamp_future_checkpoint_keeps_valid_checkpoint(monkeypatch):
    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(quest_worker, "datetime", _FrozenDateTime)

    ts = datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc)
    same_ts, same_char_id = quest_worker.clamp_future_checkpoint(ts, 123, 24)

    assert same_ts == ts
    assert same_char_id == 123
