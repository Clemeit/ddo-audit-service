import pytest

import utils.demographics as demographics


class TestGetCachedDataWithFallback:
    def test_returns_cached_data_when_present(self, monkeypatch):
        cached = {"human": 12}
        set_calls = []

        monkeypatch.setattr(demographics.redis_client, "get_by_key", lambda key: cached)
        monkeypatch.setattr(
            demographics.redis_client,
            "set_by_key",
            lambda key, value, ttl=None: set_calls.append((key, value, ttl)),
        )

        fallback_calls = {"count": 0}

        def fallback_func():
            fallback_calls["count"] += 1
            return {"fresh": 1}

        result = demographics.get_cached_data_with_fallback(
            "race_distribution", fallback_func, 60
        )

        assert result is cached
        assert fallback_calls["count"] == 0
        assert set_calls == []

    def test_regenerates_and_caches_when_missing(self, monkeypatch):
        set_calls = []
        fresh = {"elf": 5}

        monkeypatch.setattr(demographics.redis_client, "get_by_key", lambda key: None)
        monkeypatch.setattr(
            demographics.redis_client,
            "set_by_key",
            lambda key, value, ttl=None: set_calls.append((key, value, ttl)),
        )

        result = demographics.get_cached_data_with_fallback(
            "gender_distribution",
            lambda: fresh,
            3600,
        )

        assert result == fresh
        assert set_calls == [("gender_distribution", fresh, 3600)]

    def test_empty_cached_dict_is_treated_as_cache_miss(self, monkeypatch):
        set_calls = []

        monkeypatch.setattr(demographics.redis_client, "get_by_key", lambda key: {})
        monkeypatch.setattr(
            demographics.redis_client,
            "set_by_key",
            lambda key, value, ttl=None: set_calls.append((key, value, ttl)),
        )

        result = demographics.get_cached_data_with_fallback(
            "class_count", lambda: {"1": 3}, 120
        )

        assert result == {"1": 3}
        assert set_calls == [("class_count", {"1": 3}, 120)]


@pytest.mark.parametrize(
    "getter_name,postgres_func_name,key_prefix",
    [
        ("get_race_distribution", "get_race_distribution", "race_distribution"),
        ("get_gender_distribution", "get_gender_distribution", "gender_distribution"),
        (
            "get_total_level_distribution",
            "get_total_level_distribution",
            "total_level_distribution",
        ),
        (
            "get_class_count_distribution",
            "get_class_count_distribution",
            "class_count_distribution",
        ),
        (
            "get_primary_class_distribution",
            "get_primary_class_distribution",
            "primary_class_distribution",
        ),
        (
            "get_guild_affiliation_distribution",
            "get_guild_affiliation_distribution",
            "guild_affiliation_distribution",
        ),
    ],
)
def test_distribution_getters_use_days_ttl_and_fallback(
    monkeypatch, getter_name, postgres_func_name, key_prefix
):
    calls = {}

    def fake_postgres(days, activity_level):
        calls["postgres"] = (days, activity_level)
        return {"source": postgres_func_name}

    monkeypatch.setattr(demographics.postgres_client, postgres_func_name, fake_postgres)

    def fake_cached_fallback(key, fallback_func, cache_ttl):
        calls["cache"] = (key, cache_ttl)
        calls["fallback_result"] = fallback_func()
        return {"ok": True}

    monkeypatch.setattr(
        demographics,
        "get_cached_data_with_fallback",
        fake_cached_fallback,
    )

    getter = getattr(demographics, getter_name)
    period = demographics.ReportLookback.week
    result = getter(period, activity_level="active")

    expected_days = demographics.report_lookback_map[period]["days"]
    expected_ttl = demographics.report_lookback_map[period]["cache_ttl"]

    assert result == {"ok": True}
    assert calls["postgres"] == (expected_days, "active")
    assert calls["cache"] == (f"{key_prefix}_{period}_active", expected_ttl)
    assert calls["fallback_result"] == {"source": postgres_func_name}


@pytest.mark.parametrize(
    "getter_name",
    [
        "get_race_distribution",
        "get_gender_distribution",
        "get_total_level_distribution",
        "get_class_count_distribution",
        "get_primary_class_distribution",
        "get_guild_affiliation_distribution",
    ],
)
def test_distribution_getters_raise_for_invalid_period(getter_name):
    getter = getattr(demographics, getter_name)

    with pytest.raises(ValueError, match="Invalid period"):
        getter("1d", activity_level="all")
