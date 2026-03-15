from datetime import datetime

import utils.quest_sessions as quest_sessions


class TestGetQuestIdForArea:
    def test_delegates_to_postgres_client(self, monkeypatch):
        monkeypatch.setattr(
            quest_sessions.postgres_client,
            "get_quest_id_for_area",
            lambda area_id: 777 if area_id == 123 else None,
        )

        assert quest_sessions.get_quest_id_for_area(123) == 777


class TestIsQuestArea:
    def test_returns_true_when_quest_id_exists(self, monkeypatch):
        monkeypatch.setattr(quest_sessions, "get_quest_id_for_area", lambda area_id: 5)

        assert quest_sessions.is_quest_area(1) is True

    def test_returns_false_when_no_quest_id(self, monkeypatch):
        monkeypatch.setattr(
            quest_sessions, "get_quest_id_for_area", lambda area_id: None
        )

        assert quest_sessions.is_quest_area(1) is False


class TestProcessLocationActivity:
    def test_no_current_session_and_non_quest_area_does_nothing(self, monkeypatch):
        monkeypatch.setattr(
            quest_sessions, "get_quest_id_for_area", lambda area_id: None
        )

        ts = datetime(2026, 3, 15, 12, 0, 0)
        to_close, to_open = quest_sessions.process_location_activity(42, 999, ts, None)

        assert to_close is None
        assert to_open is None

    def test_enters_quest_when_no_active_session(self, monkeypatch):
        monkeypatch.setattr(quest_sessions, "get_quest_id_for_area", lambda area_id: 11)

        ts = datetime(2026, 3, 15, 12, 30, 0)
        to_close, to_open = quest_sessions.process_location_activity(42, 100, ts, None)

        assert to_close is None
        assert to_open == {
            "character_id": 42,
            "quest_id": 11,
            "entry_timestamp": ts,
        }

    def test_stays_in_same_quest_without_open_or_close(self, monkeypatch):
        mapping = {100: 11, 101: 11}
        monkeypatch.setattr(
            quest_sessions,
            "get_quest_id_for_area",
            lambda area_id: mapping.get(area_id),
        )

        ts = datetime(2026, 3, 15, 13, 0, 0)
        current_session = {"id": 9, "quest_id": 11, "entry_timestamp": ts}

        to_close, to_open = quest_sessions.process_location_activity(
            42, 101, ts, current_session
        )

        assert to_close is None
        assert to_open is None

    def test_switching_quests_closes_old_and_opens_new(self, monkeypatch):
        mapping = {100: 11, 200: 22}
        monkeypatch.setattr(
            quest_sessions,
            "get_quest_id_for_area",
            lambda area_id: mapping.get(area_id),
        )

        ts = datetime(2026, 3, 15, 13, 30, 0)
        current_session = {"id": 9, "quest_id": 11, "entry_timestamp": ts}

        to_close, to_open = quest_sessions.process_location_activity(
            42, 200, ts, current_session
        )

        assert to_close == {"session_id": 9, "exit_timestamp": ts}
        assert to_open == {
            "character_id": 42,
            "quest_id": 22,
            "entry_timestamp": ts,
        }

    def test_leaving_quest_closes_current_without_opening_new(self, monkeypatch):
        monkeypatch.setattr(
            quest_sessions,
            "get_quest_id_for_area",
            lambda area_id: None,
        )

        ts = datetime(2026, 3, 15, 14, 0, 0)
        current_session = {"id": 9, "quest_id": 11, "entry_timestamp": ts}

        to_close, to_open = quest_sessions.process_location_activity(
            42, None, ts, current_session
        )

        assert to_close == {"session_id": 9, "exit_timestamp": ts}
        assert to_open is None
