from types import SimpleNamespace

import pytest

from models.game import PopulationDataPoint, PopulationPointInTime
import utils.population as population


def _point(ts: str, data: dict[str, tuple[float, float]]) -> PopulationPointInTime:
    return PopulationPointInTime(
        timestamp=ts,
        data={
            name: PopulationDataPoint(character_count=counts[0], lfm_count=counts[1])
            for name, counts in data.items()
        },
    )


class TestGetCachedDataWithFallback:
    def test_returns_cached_data_when_present(self, monkeypatch):
        cached = {"from": "cache"}
        set_calls = []

        monkeypatch.setattr(population.redis_client, "get_by_key", lambda key: cached)
        monkeypatch.setattr(
            population.redis_client,
            "set_by_key",
            lambda key, value, ttl=None: set_calls.append((key, value, ttl)),
        )

        result = population.get_cached_data_with_fallback(
            "population_day", lambda: {"from": "db"}, 30
        )

        assert result is cached
        assert set_calls == []

    def test_returns_empty_dict_as_valid_cached_value(self, monkeypatch):
        set_calls = []

        monkeypatch.setattr(population.redis_client, "get_by_key", lambda key: {})
        monkeypatch.setattr(
            population.redis_client,
            "set_by_key",
            lambda key, value, ttl=None: set_calls.append((key, value, ttl)),
        )

        fallback_called = False

        def fallback():
            nonlocal fallback_called
            fallback_called = True
            return {"from": "db"}

        result = population.get_cached_data_with_fallback(
            "population_day", fallback, 90
        )

        assert result == {}
        assert fallback_called is False
        assert set_calls == []


class TestPopulationHelperFunctions:
    def test_average_hourly_data_returns_empty_for_no_input(self):
        assert population.average_hourly_data([]) == []

    def test_average_hourly_data_groups_by_hour(self):
        input_data = [
            _point("2026-03-01T10:05:00", {"Argonnessen": (10, 2)}),
            _point("2026-03-01T10:35:00", {"Argonnessen": (14, 4)}),
            _point("2026-03-01T11:05:00", {"Argonnessen": (20, 6)}),
        ]

        result = population.average_hourly_data(input_data)

        assert [p.timestamp for p in result] == [
            "2026-03-01T10:00:00Z",
            "2026-03-01T11:00:00Z",
        ]
        assert result[0].data["Argonnessen"].character_count == 12.0
        assert result[0].data["Argonnessen"].lfm_count == 3.0
        assert result[1].data["Argonnessen"].character_count == 20.0
        assert result[1].data["Argonnessen"].lfm_count == 6.0

    def test_average_daily_data_returns_empty_for_no_input(self):
        assert population.average_daily_data([]) == []

    def test_average_daily_data_groups_by_date(self):
        input_data = [
            _point("2026-03-01T10:05:00", {"Argonnessen": (10, 2)}),
            _point("2026-03-01T23:35:00", {"Argonnessen": (14, 4)}),
            _point("2026-03-02T00:05:00", {"Argonnessen": (20, 6)}),
        ]

        result = population.average_daily_data(input_data)

        assert [p.timestamp for p in result] == [
            "2026-03-01T00:00:00Z",
            "2026-03-02T00:00:00Z",
        ]
        assert result[0].data["Argonnessen"].character_count == 12.0
        assert result[0].data["Argonnessen"].lfm_count == 3.0
        assert result[1].data["Argonnessen"].character_count == 20.0
        assert result[1].data["Argonnessen"].lfm_count == 6.0

    def test_summed_population_data_points_empty(self):
        counts, summed = population.summed_population_data_points([])
        assert counts == {}
        assert summed == {}

    def test_summed_population_data_points_aggregates_by_server(self):
        input_data = [
            _point("2026-03-01T10:00:00", {"Argonnessen": (10, 1), "Sarlona": (5, 2)}),
            _point("2026-03-01T11:00:00", {"Argonnessen": (14, 3)}),
        ]

        counts, summed = population.summed_population_data_points(input_data)

        assert counts == {"Argonnessen": 2, "Sarlona": 1}
        assert summed["Argonnessen"].character_count == 24
        assert summed["Argonnessen"].lfm_count == 4
        assert summed["Sarlona"].character_count == 5
        assert summed["Sarlona"].lfm_count == 2

    def test_averaged_population_data_points_empty_returns_empty_list(self):
        # This asserts current behavior even though the annotation says dict.
        assert population.averaged_population_data_points([]) == []

    def test_averaged_population_data_points_returns_per_server_averages(self):
        input_data = [
            _point("2026-03-01T10:00:00", {"Argonnessen": (10, 1), "Sarlona": (5, 2)}),
            _point("2026-03-01T11:00:00", {"Argonnessen": (14, 3), "Sarlona": (9, 6)}),
        ]

        averaged = population.averaged_population_data_points(input_data)

        assert averaged["Argonnessen"].character_count == 12.0
        assert averaged["Argonnessen"].lfm_count == 2.0
        assert averaged["Sarlona"].character_count == 7.0
        assert averaged["Sarlona"].lfm_count == 4.0

    def test_normalize_population_data_empty_returns_empty(self):
        assert population.normalize_population_data([]) == []

    def test_normalize_population_data_normalizes_per_server(self):
        input_data = [
            _point("2026-03-01T10:00:00", {"Argonnessen": (0, 5), "Sarlona": (-10, 1)}),
            _point(
                "2026-03-01T11:00:00", {"Argonnessen": (10, 15), "Sarlona": (10, 1)}
            ),
        ]

        normalized = population.normalize_population_data(input_data)

        assert normalized[0].data["Argonnessen"].character_count == 0.0
        assert normalized[0].data["Argonnessen"].lfm_count == 0.0
        assert normalized[1].data["Argonnessen"].character_count == 1.0
        assert normalized[1].data["Argonnessen"].lfm_count == 1.0

        # Sarlona lfm values have zero range, so normalized value should be 0.
        assert normalized[0].data["Sarlona"].character_count == 0.0
        assert normalized[1].data["Sarlona"].character_count == 1.0
        assert normalized[0].data["Sarlona"].lfm_count == 0.0
        assert normalized[1].data["Sarlona"].lfm_count == 0.0

    def test_normalize_population_data_skips_invalid_server_records(self):
        valid_point = _point("2026-03-01T10:00:00", {"Argonnessen": (10, 5)})
        invalid_point = SimpleNamespace(
            timestamp="2026-03-01T11:00:00",
            data={
                "BadServer": SimpleNamespace(
                    character_count="not-a-number", lfm_count=1
                )
            },
        )

        normalized = population.normalize_population_data([valid_point, invalid_point])

        assert len(normalized) == 1
        assert normalized[0].timestamp == "2026-03-01T10:00:00"
        assert normalized[0].data["Argonnessen"].character_count == 0.0
        assert normalized[0].data["Argonnessen"].lfm_count == 0.0


@pytest.mark.parametrize(
    "getter_name,postgres_name,postgres_arg,key,ttl",
    [
        (
            "get_unique_character_and_guild_count_breakdown_day",
            "get_unique_character_and_guild_count",
            1,
            "get_unique_character_and_guild_count_breakdown_day",
            population.REPORT_1_DAY_CACHE_TTL,
        ),
        (
            "get_unique_character_and_guild_count_breakdown_week",
            "get_unique_character_and_guild_count",
            7,
            "get_unique_character_and_guild_count_breakdown_week",
            population.REPORT_1_WEEK_CACHE_TTL,
        ),
        (
            "get_unique_character_and_guild_count_breakdown_month",
            "get_unique_character_and_guild_count",
            30,
            "get_unique_character_and_guild_count_breakdown_month",
            population.REPORT_1_MONTH_CACHE_TTL,
        ),
        (
            "get_unique_character_and_guild_count_breakdown_quarter",
            "get_unique_character_and_guild_count",
            90,
            "get_unique_character_and_guild_count_breakdown_quarter",
            population.REPORT_1_QUARTER_CACHE_TTL,
        ),
        (
            "get_unique_character_and_guild_count_breakdown_year",
            "get_unique_character_and_guild_count",
            365,
            "get_unique_character_and_guild_count_breakdown_year",
            population.REPORT_1_YEAR_CACHE_TTL,
        ),
        (
            "get_character_activity_stats_quarter",
            "get_character_activity_stats",
            90,
            "get_character_activity_stats_quarter",
            population.REPORT_1_QUARTER_CACHE_TTL,
        ),
        (
            "get_average_server_population_day",
            "get_average_population_by_server",
            1,
            "get_average_server_population_day",
            population.REPORT_1_DAY_CACHE_TTL,
        ),
        (
            "get_average_server_population_week",
            "get_average_population_by_server",
            7,
            "get_average_server_population_week",
            population.REPORT_1_WEEK_CACHE_TTL,
        ),
        (
            "get_average_server_population_month",
            "get_average_population_by_server",
            28,
            "get_average_server_population_month",
            population.REPORT_1_MONTH_CACHE_TTL,
        ),
        (
            "get_average_server_population_quarter",
            "get_average_population_by_server",
            90,
            "get_average_server_population_quarter",
            population.REPORT_1_QUARTER_CACHE_TTL,
        ),
        (
            "get_average_server_population_year",
            "get_average_population_by_server",
            365,
            "get_average_server_population_year",
            population.REPORT_1_YEAR_CACHE_TTL,
        ),
        (
            "get_hourly_server_population_day",
            "get_average_population_by_hour_per_server",
            1,
            "get_hourly_server_population_day",
            population.REPORT_1_DAY_CACHE_TTL,
        ),
        (
            "get_hourly_server_population_week",
            "get_average_population_by_hour_per_server",
            7,
            "get_hourly_server_population_week",
            population.REPORT_1_WEEK_CACHE_TTL,
        ),
        (
            "get_hourly_server_population_month",
            "get_average_population_by_hour_per_server",
            28,
            "get_hourly_server_population_month",
            population.REPORT_1_MONTH_CACHE_TTL,
        ),
        (
            "get_hourly_server_population_quarter",
            "get_average_population_by_hour_per_server",
            90,
            "get_hourly_server_population_quarter",
            population.REPORT_1_QUARTER_CACHE_TTL,
        ),
        (
            "get_hourly_server_population_year",
            "get_average_population_by_hour_per_server",
            365,
            "get_hourly_server_population_year",
            population.REPORT_1_YEAR_CACHE_TTL,
        ),
        (
            "get_daily_server_population_day",
            "get_average_population_by_day_of_week_per_server",
            1,
            "get_daily_server_population_day",
            population.REPORT_1_DAY_CACHE_TTL,
        ),
        (
            "get_daily_server_population_week",
            "get_average_population_by_day_of_week_per_server",
            7,
            "get_daily_server_population_week",
            population.REPORT_1_WEEK_CACHE_TTL,
        ),
        (
            "get_daily_server_population_month",
            "get_average_population_by_day_of_week_per_server",
            28,
            "get_daily_server_population_month",
            population.REPORT_1_MONTH_CACHE_TTL,
        ),
        (
            "get_daily_server_population_quarter",
            "get_average_population_by_day_of_week_per_server",
            90,
            "get_daily_server_population_quarter",
            population.REPORT_1_QUARTER_CACHE_TTL,
        ),
        (
            "get_daily_server_population_year",
            "get_average_population_by_day_of_week_per_server",
            365,
            "get_daily_server_population_year",
            population.REPORT_1_YEAR_CACHE_TTL,
        ),
        (
            "get_by_hour_and_day_of_week_server_population_week",
            "get_average_population_by_hour_and_day_of_week_per_server",
            7,
            "get_by_hour_and_day_of_week_server_population_week",
            population.REPORT_1_WEEK_CACHE_TTL,
        ),
        (
            "get_by_hour_and_day_of_week_server_population_month",
            "get_average_population_by_hour_and_day_of_week_per_server",
            28,
            "get_by_hour_and_day_of_week_server_population_month",
            population.REPORT_1_MONTH_CACHE_TTL,
        ),
        (
            "get_by_hour_and_day_of_week_server_population_quarter",
            "get_average_population_by_hour_and_day_of_week_per_server",
            90,
            "get_by_hour_and_day_of_week_server_population_quarter",
            population.REPORT_1_QUARTER_CACHE_TTL,
        ),
        (
            "get_by_hour_and_day_of_week_server_population_year",
            "get_average_population_by_hour_and_day_of_week_per_server",
            365,
            "get_by_hour_and_day_of_week_server_population_year",
            population.REPORT_1_YEAR_CACHE_TTL,
        ),
    ],
)
def test_simple_population_getters_delegate_to_cache_and_postgres(
    monkeypatch, getter_name, postgres_name, postgres_arg, key, ttl
):
    calls = {}
    postgres_result = {"source": getter_name}

    def fake_postgres(days):
        calls["postgres"] = days
        return postgres_result

    monkeypatch.setattr(population.postgres_client, postgres_name, fake_postgres)

    def fake_cached_fallback(cache_key, fallback_func, cache_ttl):
        calls["cache"] = (cache_key, cache_ttl)
        calls["fallback_result"] = fallback_func()
        return {"wrapped": True}

    monkeypatch.setattr(
        population, "get_cached_data_with_fallback", fake_cached_fallback
    )

    getter = getattr(population, getter_name)
    result = getter()

    assert result == {"wrapped": True}
    assert calls["postgres"] == postgres_arg
    assert calls["cache"] == (key, ttl)
    assert calls["fallback_result"] == postgres_result


class TestGamePopulationGetters:
    def test_get_game_population_day_uses_relative_data_and_model_dump(
        self, monkeypatch
    ):
        calls = {}
        rows = [
            _point("2026-03-01T10:00:00", {"Argonnessen": (1, 2)}),
            _point("2026-03-01T10:10:00", {"Argonnessen": (3, 4)}),
        ]

        def fake_postgres(days):
            calls["postgres"] = days
            return rows

        monkeypatch.setattr(
            population.postgres_client, "get_game_population_relative", fake_postgres
        )

        def fake_cached_fallback(cache_key, fallback_func, cache_ttl):
            calls["cache"] = (cache_key, cache_ttl)
            calls["fallback_result"] = fallback_func()
            return {"wrapped": True}

        monkeypatch.setattr(
            population, "get_cached_data_with_fallback", fake_cached_fallback
        )

        result = population.get_game_population_day()

        assert result == {"wrapped": True}
        assert calls["postgres"] == 1
        assert calls["cache"] == (
            "get_game_population_day",
            population.REPORT_1_DAY_CACHE_TTL,
        )
        assert calls["fallback_result"] == [row.model_dump() for row in rows]

    @pytest.mark.parametrize(
        "getter_name,postgres_name,average_helper,key,ttl",
        [
            (
                "get_game_population_week",
                "get_game_population_last_week",
                "average_hourly_data",
                "get_game_population_week",
                population.REPORT_1_WEEK_CACHE_TTL,
            ),
            (
                "get_game_population_month",
                "get_game_population_last_month",
                "average_daily_data",
                "get_game_population_month",
                population.REPORT_1_MONTH_CACHE_TTL,
            ),
            (
                "get_game_population_quarter",
                "get_game_population_last_quarter",
                "average_daily_data",
                "get_game_population_quarter",
                population.REPORT_1_QUARTER_CACHE_TTL,
            ),
            (
                "get_game_population_year",
                "get_game_population_last_year",
                "average_daily_data",
                "get_game_population_year",
                population.REPORT_1_YEAR_CACHE_TTL,
            ),
        ],
    )
    def test_get_game_population_period_getters_apply_expected_averaging(
        self,
        monkeypatch,
        getter_name,
        postgres_name,
        average_helper,
        key,
        ttl,
    ):
        calls = {}
        postgres_rows = [
            _point("2026-03-01T10:00:00", {"Argonnessen": (1, 2)}),
            _point("2026-03-01T10:15:00", {"Argonnessen": (3, 4)}),
        ]
        averaged_rows = [_point("2026-03-01T10:00:00Z", {"Argonnessen": (2, 3)})]

        monkeypatch.setattr(
            population.postgres_client,
            postgres_name,
            lambda: postgres_rows,
        )

        def fake_average(rows):
            calls["average_input"] = rows
            return averaged_rows

        monkeypatch.setattr(population, average_helper, fake_average)

        def fake_cached_fallback(cache_key, fallback_func, cache_ttl):
            calls["cache"] = (cache_key, cache_ttl)
            calls["fallback_result"] = fallback_func()
            return {"wrapped": True}

        monkeypatch.setattr(
            population, "get_cached_data_with_fallback", fake_cached_fallback
        )

        getter = getattr(population, getter_name)
        result = getter()

        assert result == {"wrapped": True}
        assert calls["cache"] == (key, ttl)
        assert calls["average_input"] is postgres_rows
        assert calls["fallback_result"] == [row.model_dump() for row in averaged_rows]


@pytest.mark.parametrize(
    "getter_name,postgres_name,postgres_arg,key,ttl",
    [
        (
            "get_game_population_totals_day",
            "get_game_population_relative",
            1,
            "get_game_population_totals_day",
            population.REPORT_1_DAY_CACHE_TTL,
        ),
        (
            "get_game_population_totals_week",
            "get_game_population_last_week",
            None,
            "get_game_population_totals_week",
            population.REPORT_1_WEEK_CACHE_TTL,
        ),
        (
            "get_game_population_totals_month",
            "get_game_population_last_month",
            None,
            "get_game_population_totals_month",
            population.REPORT_1_MONTH_CACHE_TTL,
        ),
        (
            "get_game_population_totals_quarter",
            "get_game_population_last_quarter",
            None,
            "get_game_population_totals_quarter",
            population.REPORT_1_QUARTER_CACHE_TTL,
        ),
        (
            "get_game_population_totals_year",
            "get_game_population_last_year",
            None,
            "get_game_population_totals_year",
            population.REPORT_1_YEAR_CACHE_TTL,
        ),
    ],
)
def test_game_population_totals_getters_use_summed_data(
    monkeypatch, getter_name, postgres_name, postgres_arg, key, ttl
):
    calls = {}
    postgres_rows = [
        _point("2026-03-01T10:00:00", {"Argonnessen": (1, 2)}),
    ]

    if postgres_arg is None:
        monkeypatch.setattr(
            population.postgres_client,
            postgres_name,
            lambda: postgres_rows,
        )
    else:
        monkeypatch.setattr(
            population.postgres_client,
            postgres_name,
            lambda days: postgres_rows,
        )

    summed_data = {
        "Argonnessen": PopulationDataPoint(character_count=10, lfm_count=4),
        "Sarlona": PopulationDataPoint(character_count=5, lfm_count=2),
    }

    def fake_summed(rows):
        calls["summed_input"] = rows
        return {"Argonnessen": 2, "Sarlona": 1}, summed_data

    monkeypatch.setattr(population, "summed_population_data_points", fake_summed)

    def fake_cached_fallback(cache_key, fallback_func, cache_ttl):
        calls["cache"] = (cache_key, cache_ttl)
        calls["fallback_result"] = fallback_func()
        return {"wrapped": True}

    monkeypatch.setattr(
        population, "get_cached_data_with_fallback", fake_cached_fallback
    )

    getter = getattr(population, getter_name)
    result = getter()

    assert result == {"wrapped": True}
    assert calls["cache"] == (key, ttl)
    assert calls["summed_input"] is postgres_rows
    assert calls["fallback_result"] == {
        "Argonnessen": {"character_count": 10.0, "lfm_count": 4.0},
        "Sarlona": {"character_count": 5.0, "lfm_count": 2.0},
    }
