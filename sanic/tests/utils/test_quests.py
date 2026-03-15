from models.quest import Quest
import utils.quests as quests


def _quest(
    quest_id: int,
    *,
    name: str = "Quest",
    heroic_cr: int | None = None,
    epic_cr: int | None = None,
    xp: dict | None = None,
    length: int | None = None,
) -> Quest:
    return Quest(
        id=quest_id,
        name=name,
        heroic_normal_cr=heroic_cr,
        epic_normal_cr=epic_cr,
        xp=xp,
        length=length,
    )


class TestGetValidQuestIds:
    def test_extracts_ids_from_quest_payload(self, monkeypatch):
        monkeypatch.setattr(
            quests,
            "get_quests",
            lambda: ([{"id": 11, "name": "A"}, {"id": 22, "name": "B"}], "cache", "ts"),
        )

        assert quests.get_valid_quest_ids() == ([11, 22], "cache", "ts")

    def test_returns_empty_result_when_get_quests_errors(self, monkeypatch):
        monkeypatch.setattr(
            quests,
            "get_quests",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        assert quests.get_valid_quest_ids() == ([], None, None)


class TestGetQuests:
    def test_returns_cached_quests_when_cache_is_fresh(self, monkeypatch):
        cached_timestamp = 500.0
        cached_quests = [{"id": 1, "name": "Cached"}]

        monkeypatch.setattr(
            quests.redis_client,
            "get_known_quests",
            lambda: {"quests": cached_quests, "timestamp": cached_timestamp},
        )
        monkeypatch.setattr(
            quests.postgres_client,
            "get_all_quests",
            lambda: (_ for _ in ()).throw(AssertionError("DB should not be called")),
        )
        monkeypatch.setattr(quests, "time", lambda: cached_timestamp + 5)
        monkeypatch.setattr(
            quests,
            "timestamp_to_datetime_string",
            lambda ts: f"converted-{int(ts)}",
        )

        assert quests.get_quests() == (cached_quests, "cache", "converted-500")

    def test_fetches_from_database_when_cache_is_stale(self, monkeypatch):
        stale_timestamp = 700.0
        db_quests = [_quest(1, name="A"), _quest(2, name="B")]
        set_calls = []

        monkeypatch.setattr(
            quests.redis_client,
            "get_known_quests",
            lambda: {"quests": [{"id": 1}], "timestamp": stale_timestamp},
        )
        monkeypatch.setattr(quests.postgres_client, "get_all_quests", lambda: db_quests)
        monkeypatch.setattr(
            quests.redis_client,
            "set_known_quests",
            lambda payload: set_calls.append(payload),
        )
        monkeypatch.setattr(
            quests,
            "time",
            lambda: stale_timestamp + quests.VALID_QUEST_CACHE_TTL + 1,
        )
        monkeypatch.setattr(
            quests,
            "get_current_datetime_string",
            lambda: "2026-03-15T00:00:00",
        )

        result = quests.get_quests()

        assert result == (
            [quest.model_dump() for quest in db_quests],
            "database",
            "2026-03-15T00:00:00",
        )
        assert set_calls == [db_quests]

    def test_skip_cache_bypasses_cache_lookup(self, monkeypatch):
        db_quests = [_quest(9, name="SkipCache")]

        monkeypatch.setattr(
            quests.redis_client,
            "get_known_quests",
            lambda: (_ for _ in ()).throw(AssertionError("Cache should not be read")),
        )
        monkeypatch.setattr(quests.postgres_client, "get_all_quests", lambda: db_quests)
        monkeypatch.setattr(
            quests.redis_client, "set_known_quests", lambda payload: None
        )
        monkeypatch.setattr(
            quests,
            "get_current_datetime_string",
            lambda: "2026-03-15T00:00:00",
        )

        assert quests.get_quests(skip_cache=True) == (
            [quest.model_dump() for quest in db_quests],
            "database",
            "2026-03-15T00:00:00",
        )

    def test_returns_empty_tuple_when_database_has_no_rows(self, monkeypatch):
        monkeypatch.setattr(
            quests.redis_client,
            "get_known_quests",
            lambda: {"quests": [], "timestamp": 1000.0},
        )
        monkeypatch.setattr(
            quests, "time", lambda: 1000.0 + quests.VALID_QUEST_CACHE_TTL + 1
        )
        monkeypatch.setattr(quests.postgres_client, "get_all_quests", lambda: [])

        assert quests.get_quests() == ([], None, None)

    def test_returns_empty_tuple_on_exception(self, monkeypatch):
        monkeypatch.setattr(
            quests.redis_client,
            "get_known_quests",
            lambda: (_ for _ in ()).throw(RuntimeError("redis unavailable")),
        )

        assert quests.get_quests() == ([], None, None)


class TestGetQuestsWithMetrics:
    def test_returns_cached_metrics_when_cache_is_fresh(self, monkeypatch):
        cached_timestamp = 1200.0
        cached_quests = [{"id": 1, "heroic_xp_per_minute_relative": 0.9}]

        monkeypatch.setattr(
            quests.redis_client,
            "get_quests_with_metrics",
            lambda: {"quests": cached_quests, "timestamp": cached_timestamp},
        )
        monkeypatch.setattr(
            quests.postgres_client,
            "get_all_quests_with_metrics",
            lambda: (_ for _ in ()).throw(AssertionError("DB should not be called")),
        )
        monkeypatch.setattr(quests, "time", lambda: cached_timestamp + 30)
        monkeypatch.setattr(
            quests,
            "timestamp_to_datetime_string",
            lambda ts: f"converted-{int(ts)}",
        )

        assert quests.get_quests_with_metrics() == (
            cached_quests,
            "cache",
            "converted-1200",
        )

    def test_flattens_metrics_from_database_and_caches_v2_models(self, monkeypatch):
        quest_a = _quest(
            1,
            name="Quest A",
            heroic_cr=5,
            xp={"heroic_elite": 1200},
            length=600,
        )
        quest_b = _quest(2, name="Quest B")

        metrics_for_a = {
            "heroic_xp_per_minute_relative": 0.8,
            "epic_xp_per_minute_relative": 0.2,
            "heroic_popularity_relative": 0.7,
            "epic_popularity_relative": 0.1,
        }

        set_calls = []
        monkeypatch.setattr(
            quests.redis_client,
            "get_quests_with_metrics",
            lambda: {"quests": [{"id": 0}], "timestamp": None},
        )
        monkeypatch.setattr(
            quests.postgres_client,
            "get_all_quests_with_metrics",
            lambda: [(quest_a, metrics_for_a), (quest_b, None)],
        )
        monkeypatch.setattr(
            quests.redis_client,
            "set_quests_with_metrics",
            lambda payload: set_calls.append(payload),
        )
        monkeypatch.setattr(
            quests,
            "get_current_datetime_string",
            lambda: "2026-03-15T01:23:45",
        )

        quests_payload, source, timestamp = quests.get_quests_with_metrics()

        assert source == "database"
        assert timestamp == "2026-03-15T01:23:45"
        assert quests_payload[0]["heroic_xp_per_minute_relative"] == 0.8
        assert quests_payload[0]["epic_popularity_relative"] == 0.1
        assert quests_payload[1]["heroic_xp_per_minute_relative"] is None
        assert quests_payload[1]["epic_popularity_relative"] is None

        assert len(set_calls) == 1
        cached_models = set_calls[0]
        assert len(cached_models) == 2
        assert [model.model_dump() for model in cached_models] == quests_payload

    def test_returns_empty_when_database_has_no_data(self, monkeypatch):
        monkeypatch.setattr(
            quests.redis_client,
            "get_quests_with_metrics",
            lambda: {"quests": [], "timestamp": 999.0},
        )
        monkeypatch.setattr(
            quests.postgres_client,
            "get_all_quests_with_metrics",
            lambda: [],
        )
        monkeypatch.setattr(
            quests,
            "time",
            lambda: 999.0 + quests.VALID_QUEST_CACHE_TTL + 1,
        )

        assert quests.get_quests_with_metrics() == ([], None, None)

    def test_returns_empty_on_exception(self, monkeypatch):
        monkeypatch.setattr(
            quests.redis_client,
            "get_quests_with_metrics",
            lambda: (_ for _ in ()).throw(RuntimeError("cache failure")),
        )

        assert quests.get_quests_with_metrics() == ([], None, None)
