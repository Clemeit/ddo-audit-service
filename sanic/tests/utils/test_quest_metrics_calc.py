import json

import pytest

from models.quest import Quest
from models.quest_session import QuestAnalytics
import utils.quest_metrics_calc as quest_metrics


class _RedisContextManager:
    def __init__(self, client):
        self.client = client

    def __enter__(self):
        return self.client

    def __exit__(self, exc_type, exc, tb):
        return False


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


class TestCoerceToNumber:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (123, 123.0),
            (12.5, 12.5),
            ("42", 42.0),
            (" 42.5 ", 42.5),
            ("", None),
            (None, None),
            ("not-a-number", None),
            (object(), None),
        ],
    )
    def test_coercion_cases(self, value, expected):
        assert quest_metrics._coerce_to_number(value) == expected


class TestCalculateXpPerMinute:
    def test_returns_expected_xp_per_minute(self):
        assert quest_metrics.calculate_xp_per_minute(600, 300) == pytest.approx(120.0)

    @pytest.mark.parametrize(
        "xp_value,length_seconds",
        [
            (None, 300),
            (600, None),
            (600, 0),
            (600, -1),
            ("bad", 300),
        ],
    )
    def test_returns_none_for_invalid_inputs(self, xp_value, length_seconds):
        assert quest_metrics.calculate_xp_per_minute(xp_value, length_seconds) is None


class TestCalculateRelativeMetric:
    def test_returns_none_without_value_or_peers(self):
        assert quest_metrics.calculate_relative_metric(None, [1.0, 2.0]) is None
        assert quest_metrics.calculate_relative_metric(1.0, []) is None
        assert quest_metrics.calculate_relative_metric(1.0, [None, None]) is None

    def test_returns_midpoint_when_peers_are_constant(self):
        assert quest_metrics.calculate_relative_metric(10.0, [5.0, 5.0, 5.0]) == 0.5

    def test_normalizes_and_clamps(self):
        assert quest_metrics.calculate_relative_metric(
            20.0, [10.0, 30.0]
        ) == pytest.approx(0.5)
        assert quest_metrics.calculate_relative_metric(
            5.0, [10.0, 30.0]
        ) == pytest.approx(0.0)
        assert quest_metrics.calculate_relative_metric(
            50.0, [10.0, 30.0]
        ) == pytest.approx(1.0)


class TestGetXpWithFallback:
    def test_uses_difficulty_order_elite_hard_normal_casual(self):
        xp = {
            "heroic_hard": 123,
            "heroic_normal": 99,
            "heroic_casual": 50,
        }

        assert quest_metrics.get_xp_with_fallback(xp, "heroic") == 123

    def test_returns_none_without_matching_values(self):
        assert quest_metrics.get_xp_with_fallback(None, "heroic") is None
        assert quest_metrics.get_xp_with_fallback({}, "heroic") is None
        assert (
            quest_metrics.get_xp_with_fallback({"heroic_elite": None}, "heroic") is None
        )


class TestGetQuestMetricsSingle:
    def test_returns_none_when_quest_not_found(self, monkeypatch):
        monkeypatch.setattr(quest_metrics, "get_quest_by_id", lambda quest_id: None)

        assert quest_metrics.get_quest_metrics_single(999) is None

    def test_returns_none_when_sessions_below_threshold(self, monkeypatch):
        quest = _quest(1, heroic_cr=5, xp={"heroic_elite": 600}, length=600)

        monkeypatch.setattr(quest_metrics, "get_quest_by_id", lambda quest_id: quest)
        monkeypatch.setattr(
            quest_metrics,
            "get_quest_metrics",
            lambda quest_id: {"analytics_data": {"total_sessions": 50}},
        )

        assert quest_metrics.get_quest_metrics_single(1) is None

    def test_calculates_relative_metrics_from_cached_analytics(self, monkeypatch):
        target = _quest(1, heroic_cr=5, xp={"heroic_elite": 600}, length=600)
        peer_a = _quest(2, heroic_cr=5, xp={"heroic_elite": 1200}, length=600)
        peer_b = _quest(3, heroic_cr=5, xp={"heroic_elite": 2400}, length=600)
        peer_bulk_calls = []

        monkeypatch.setattr(quest_metrics, "get_quest_by_id", lambda quest_id: target)
        monkeypatch.setattr(
            quest_metrics, "get_all_quests", lambda: [target, peer_a, peer_b]
        )
        monkeypatch.setattr(
            quest_metrics,
            "get_quest_analytics",
            lambda quest_id, lookback_days: (_ for _ in ()).throw(
                AssertionError(
                    "Live analytics should not be used when cached data exists"
                )
            ),
        )

        def _bulk(peer_ids):
            peer_bulk_calls.append(set(peer_ids))
            return {
                2: {"analytics_data": {"total_sessions": 100}},
                3: {"analytics_data": {"total_sessions": 400}},
            }

        monkeypatch.setattr(quest_metrics, "get_quest_metrics_bulk", _bulk)

        result = quest_metrics.get_quest_metrics_single(
            1,
            cached_metrics={"analytics_data": {"total_sessions": 200}},
        )

        assert peer_bulk_calls == [{2, 3}]
        assert result is not None
        assert result["heroic_xp_per_minute_relative"] == pytest.approx(0.0)
        assert result["heroic_popularity_relative"] == pytest.approx(1.0 / 3.0)
        assert result["epic_xp_per_minute_relative"] is None
        assert result["analytics_data"]["total_sessions"] == 200

    def test_force_refresh_uses_live_analytics(self, monkeypatch):
        quest = _quest(1, heroic_cr=5, xp={"heroic_elite": 600}, length=600)

        monkeypatch.setattr(quest_metrics, "get_quest_by_id", lambda quest_id: quest)
        monkeypatch.setattr(
            quest_metrics,
            "get_quest_metrics",
            lambda quest_id: (_ for _ in ()).throw(
                AssertionError(
                    "Cached metrics lookup should be skipped on force_refresh"
                )
            ),
        )
        monkeypatch.setattr(
            quest_metrics,
            "get_quest_analytics",
            lambda quest_id, lookback_days: QuestAnalytics(total_sessions=150),
        )
        monkeypatch.setattr(quest_metrics, "get_all_quests", lambda: [quest])
        monkeypatch.setattr(
            quest_metrics, "get_quest_metrics_bulk", lambda peer_ids: {}
        )

        result = quest_metrics.get_quest_metrics_single(
            1,
            force_refresh=True,
            cached_metrics={"analytics_data": {"total_sessions": 1}},
        )

        assert result is not None
        assert result["analytics_data"]["total_sessions"] == 150


class TestComputeAllQuestAnalyticsPass1:
    def test_collects_analytics_and_stores_in_redis(self, monkeypatch):
        quests = [_quest(1), _quest(2)]
        sleep_calls = []

        class FakeRedisClient:
            def __init__(self):
                self.hset_calls = []
                self.expire_calls = []

            def hset(self, key, field, value):
                self.hset_calls.append((key, field, value))

            def expire(self, key, ttl):
                self.expire_calls.append((key, ttl))

        fake_redis = FakeRedisClient()

        monkeypatch.setattr(quest_metrics.os, "getenv", lambda key, default=None: "0")
        monkeypatch.setattr(
            quest_metrics,
            "get_quest_analytics",
            lambda quest_id, lookback_days: QuestAnalytics(
                total_sessions=quest_id * 100
            ),
        )
        monkeypatch.setattr(
            quest_metrics,
            "get_redis_client",
            lambda: _RedisContextManager(fake_redis),
        )
        monkeypatch.setattr(
            quest_metrics.time, "sleep", lambda seconds: sleep_calls.append(seconds)
        )

        result = quest_metrics.compute_all_quest_analytics_pass1(quests)

        assert set(result.keys()) == {1, 2}
        assert result[1]["total_sessions"] == 100
        assert result[2]["total_sessions"] == 200
        assert sleep_calls == []
        assert len(fake_redis.hset_calls) == 2

        for key, field, value in fake_redis.hset_calls:
            assert key == quest_metrics.REDIS_QUEST_ANALYTICS_CACHE_KEY
            assert field in {"1", "2"}
            assert json.loads(value)["total_sessions"] in {100, 200}

        assert fake_redis.expire_calls == [
            (quest_metrics.REDIS_QUEST_ANALYTICS_CACHE_KEY, 86400)
        ]


class TestComputeAllQuestRelativeMetricsPass2:
    def test_raises_when_analytics_cache_is_missing_and_cleans_up(self, monkeypatch):
        class FakeRedisClient:
            def __init__(self):
                self.deleted_keys = []

            def hgetall(self, key):
                return {}

            def delete(self, key):
                self.deleted_keys.append(key)

        fake_redis = FakeRedisClient()
        monkeypatch.setattr(
            quest_metrics,
            "get_redis_client",
            lambda: _RedisContextManager(fake_redis),
        )

        with pytest.raises(RuntimeError):
            quest_metrics.compute_all_quest_relative_metrics_pass2([])

        assert fake_redis.deleted_keys == [
            quest_metrics.REDIS_QUEST_ANALYTICS_CACHE_KEY
        ]

    def test_calculates_metrics_and_cleans_cache(self, monkeypatch):
        quest_a = _quest(1, heroic_cr=5, xp={"heroic_elite": 600}, length=600)
        quest_b = _quest(2, heroic_cr=5, xp={"heroic_elite": 1200}, length=600)

        cached = {
            b"1": json.dumps({"total_sessions": 200}).encode("utf-8"),
            b"2": json.dumps({"total_sessions": 400}).encode("utf-8"),
        }

        class FakeRedisClient:
            def __init__(self):
                self.deleted_keys = []

            def hgetall(self, key):
                return cached

            def delete(self, key):
                self.deleted_keys.append(key)

        fake_redis = FakeRedisClient()
        monkeypatch.setattr(
            quest_metrics,
            "get_redis_client",
            lambda: _RedisContextManager(fake_redis),
        )

        result = quest_metrics.compute_all_quest_relative_metrics_pass2(
            [quest_a, quest_b]
        )

        assert set(result.keys()) == {1, 2}
        assert result[1]["heroic_xp_per_minute_relative"] == pytest.approx(0.0)
        assert result[2]["heroic_xp_per_minute_relative"] == pytest.approx(1.0)
        assert result[1]["heroic_popularity_relative"] == pytest.approx(0.0)
        assert result[2]["heroic_popularity_relative"] == pytest.approx(1.0)
        assert result[1]["analytics_data"]["total_sessions"] == 200
        assert result[2]["analytics_data"]["total_sessions"] == 400
        assert fake_redis.deleted_keys == [
            quest_metrics.REDIS_QUEST_ANALYTICS_CACHE_KEY
        ]


class TestGetAllQuestMetricsData:
    def test_runs_passes_and_returns_final_metrics(self, monkeypatch):
        quests = [_quest(1)]
        call_order = []

        monkeypatch.setattr(
            quest_metrics,
            "compute_all_quest_analytics_pass1",
            lambda all_quests: call_order.append(("pass1", all_quests)),
        )
        monkeypatch.setattr(
            quest_metrics,
            "compute_all_quest_relative_metrics_pass2",
            lambda all_quests: (
                call_order.append(("pass2", all_quests)),
                {1: {"heroic_xp_per_minute_relative": 0.8}},
            )[1],
        )

        result = quest_metrics.get_all_quest_metrics_data(quests)

        assert result == {1: {"heroic_xp_per_minute_relative": 0.8}}
        assert call_order == [
            ("pass1", quests),
            ("pass2", quests),
        ]
