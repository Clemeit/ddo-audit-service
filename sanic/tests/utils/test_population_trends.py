from datetime import datetime, timedelta, timezone

import pytest

from models.game import PopulationDataPoint, PopulationPointInTime
import utils.population as population

UTC = timezone.utc


def _passthrough_cache(calls):
    def fake_cached_fallback(cache_key, fallback_func, cache_ttl):
        calls["cache"] = (cache_key, cache_ttl)
        return fallback_func()

    return fake_cached_fallback


def _row(t, server, players=(10.0, 5, 15), lfms=(2.0, 1, 3), samples=12):
    return {
        "t": t,
        "server_name": server,
        "samples": samples,
        "players_avg": players[0],
        "players_min": players[1],
        "players_max": players[2],
        "lfms_avg": lfms[0],
        "lfms_min": lfms[1],
        "lfms_max": lfms[2],
    }


class TestResolveTrendsQuery:
    def test_defaults(self):
        assert population.resolve_trends_query(None, None) == ("month", "day")
        assert population.resolve_trends_query("week", None) == ("week", "hour")
        assert population.resolve_trends_query("ALL", None) == ("all", "week")

    def test_accepts_allowed_bucket(self):
        assert population.resolve_trends_query("year", "Month") == ("year", "month")

    def test_rejects_unknown_range(self):
        with pytest.raises(ValueError, match="Invalid range"):
            population.resolve_trends_query("decade", None)

    def test_rejects_bucket_not_allowed_for_range(self):
        with pytest.raises(ValueError, match="Invalid bucket 'hour' for range 'year'"):
            population.resolve_trends_query("year", "hour")


class TestTruncateToBucket:
    value = datetime(2026, 11, 19, 15, 42, 7, tzinfo=UTC)  # a Thursday

    @pytest.mark.parametrize(
        "unit,expected",
        [
            ("hour", datetime(2026, 11, 19, 15, tzinfo=UTC)),
            ("day", datetime(2026, 11, 19, tzinfo=UTC)),
            ("week", datetime(2026, 11, 16, tzinfo=UTC)),
            ("month", datetime(2026, 11, 1, tzinfo=UTC)),
        ],
    )
    def test_units(self, unit, expected):
        assert population.truncate_to_bucket(self.value, unit) == expected

    def test_rejects_unknown_unit(self):
        with pytest.raises(ValueError):
            population.truncate_to_bucket(self.value, "year")


class TestBuildTrendsSeries:
    def test_groups_by_server_and_flags_in_progress_bucket(self):
        end = datetime(2026, 11, 21, 15, tzinfo=UTC)
        rows = [
            _row(datetime(2026, 11, 20), "Argonnessen"),
            _row(datetime(2026, 11, 20), "Cannith", players=(4.567, 1, 9)),
            _row(datetime(2026, 11, 21), "Argonnessen", samples=180),
        ]

        series = population.build_trends_series(rows, end, "day")

        assert list(series) == ["Argonnessen", "Cannith"]
        assert series["Argonnessen"][0] == {
            "t": "2026-11-20T00:00:00Z",
            "samples": 12,
            "players": {"avg": 10.0, "min": 5, "max": 15},
            "lfms": {"avg": 2.0, "min": 1, "max": 3},
        }
        assert series["Argonnessen"][1]["partial"] is True
        assert series["Cannith"][0]["players"]["avg"] == 4.57

    def test_hour_buckets_are_never_partial(self):
        end = datetime(2026, 11, 21, 15, tzinfo=UTC)
        series = population.build_trends_series(
            [_row(datetime(2026, 11, 21, 14), "Argonnessen")], end, "hour"
        )
        assert "partial" not in series["Argonnessen"][0]


class TestGetPopulationTrends:
    def test_aligns_start_and_uses_bucket_ttl(self, monkeypatch):
        calls = {}
        end = datetime(2026, 11, 21, 15, tzinfo=UTC)
        monkeypatch.setattr(population, "_start_of_hour_utc", lambda: end)
        monkeypatch.setattr(
            population, "get_cached_data_with_fallback", _passthrough_cache(calls)
        )

        def fake_buckets(start, stop, unit):
            calls["query"] = (start, stop, unit)
            return []

        monkeypatch.setattr(
            population.population_db, "get_population_buckets", fake_buckets
        )
        monkeypatch.setattr(
            population.population_db,
            "get_total_population_buckets",
            lambda start, stop, unit: [
                {
                    "t": datetime(2026, 11, 16),
                    "hours": 87,
                    "players_avg": 100.555,
                    "players_min": 40.0,
                    "players_max": 180.0,
                    "lfms_avg": 5.0,
                    "lfms_min": 1.0,
                    "lfms_max": 9.0,
                }
            ],
        )

        result = population.get_population_trends("year", "week")

        # 365 days back is Fri 2025-11-21; aligned down to Monday 2025-11-17.
        assert calls["query"] == (datetime(2025, 11, 17, tzinfo=UTC), end, "week")
        assert calls["cache"] == (
            "population_trends_year_week",
            population.POPULATION_TRENDS_CACHE_TTL,
        )
        assert result["start"] == "2025-11-17T00:00:00Z"
        assert result["end"] == "2026-11-21T15:00:00Z"
        assert result["series"] == {}
        assert result["total"] == [
            {
                "t": "2026-11-16T00:00:00Z",
                "hours": 87,
                "players": {"avg": 100.56, "min": 40.0, "max": 180.0},
                "lfms": {"avg": 5.0, "min": 1.0, "max": 9.0},
                "partial": True,
            }
        ]

    def test_all_range_starts_at_first_rollup_bucket(self, monkeypatch):
        calls = {}
        monkeypatch.setattr(
            population,
            "_start_of_hour_utc",
            lambda: datetime(2026, 11, 21, 15, tzinfo=UTC),
        )
        monkeypatch.setattr(
            population, "get_cached_data_with_fallback", _passthrough_cache(calls)
        )
        monkeypatch.setattr(
            population.population_db,
            "get_first_rollup_bucket",
            lambda: datetime(2025, 5, 21, 3, tzinfo=UTC),
        )
        monkeypatch.setattr(
            population.population_db,
            "get_population_buckets",
            lambda start, stop, unit: calls.setdefault("start", start) and [],
        )
        monkeypatch.setattr(
            population.population_db,
            "get_total_population_buckets",
            lambda start, stop, unit: [],
        )

        result = population.get_population_trends("all", "month")

        assert calls["start"] == datetime(2025, 5, 1, tzinfo=UTC)
        assert result["start"] == "2025-05-01T00:00:00Z"

    def test_all_range_is_empty_when_rollup_is_empty(self, monkeypatch):
        calls = {}
        end = datetime(2026, 11, 21, 15, tzinfo=UTC)
        monkeypatch.setattr(population, "_start_of_hour_utc", lambda: end)
        monkeypatch.setattr(
            population, "get_cached_data_with_fallback", _passthrough_cache(calls)
        )
        monkeypatch.setattr(
            population.population_db, "get_first_rollup_bucket", lambda: None
        )
        monkeypatch.setattr(
            population.population_db,
            "get_population_buckets",
            lambda start, stop, unit: calls.setdefault("window", (start, stop)) and [],
        )
        monkeypatch.setattr(
            population.population_db,
            "get_total_population_buckets",
            lambda start, stop, unit: [],
        )

        population.get_population_trends("all", "week")

        # Start aligns down to the week containing `end`; no epoch-long empty range.
        assert calls["window"] == (datetime(2026, 11, 16, tzinfo=UTC), end)

    def test_hour_buckets_use_short_ttl(self, monkeypatch):
        calls = {}
        monkeypatch.setattr(
            population,
            "_start_of_hour_utc",
            lambda: datetime(2026, 11, 21, 15, tzinfo=UTC),
        )
        monkeypatch.setattr(
            population, "get_cached_data_with_fallback", _passthrough_cache(calls)
        )
        monkeypatch.setattr(
            population.population_db,
            "get_population_buckets",
            lambda start, stop, unit: calls.setdefault("start", start) and [],
        )
        monkeypatch.setattr(
            population.population_db,
            "get_total_population_buckets",
            lambda start, stop, unit: [],
        )

        population.get_population_trends("week", "hour")
        assert calls["cache"][1] == population.POPULATION_TRENDS_HOURLY_CACHE_TTL


class TestComparisonWindows:
    def test_mid_month(self):
        end = datetime(2026, 11, 21, 15, tzinfo=UTC)
        windows = population.comparison_windows(end)

        assert windows["week"] == (
            (end - timedelta(days=7), end),
            (end - timedelta(days=14), end - timedelta(days=7)),
        )
        assert windows["year_over_year"] == (
            (datetime(2026, 11, 1, tzinfo=UTC), end),
            (datetime(2025, 11, 1, tzinfo=UTC), datetime(2025, 11, 21, 15, tzinfo=UTC)),
        )

    def test_first_hours_of_month_compare_previous_full_month(self):
        end = datetime(2026, 12, 1, 5, tzinfo=UTC)
        current, previous = population.comparison_windows(end)["year_over_year"]
        assert current == (
            datetime(2026, 11, 1, tzinfo=UTC),
            datetime(2026, 12, 1, tzinfo=UTC),
        )
        assert previous == (
            datetime(2025, 11, 1, tzinfo=UTC),
            datetime(2025, 12, 1, tzinfo=UTC),
        )

    def test_leap_day_falls_back_to_feb_28(self):
        end = datetime(2028, 2, 29, 12, tzinfo=UTC)
        _, previous = population.comparison_windows(end)["year_over_year"]
        assert previous[1] == datetime(2027, 2, 28, 12, tzinfo=UTC)


class TestSummarizeWindow:
    def test_returns_none_without_data(self, monkeypatch):
        monkeypatch.setattr(
            population.population_db,
            "get_population_window",
            lambda s, e: ({}, 0, None),
        )
        start = datetime(2026, 11, 1, tzinfo=UTC)
        assert population.summarize_window(start, start + timedelta(days=1)) is None

    def test_totals_and_coverage(self, monkeypatch):
        monkeypatch.setattr(
            population.population_db,
            "get_population_window",
            lambda s, e: (
                {
                    "Argonnessen": {"players": 100.123, "lfms": 10.0},
                    "Cannith": {"players": 50.0, "lfms": 4.5},
                },
                12,
                # Combined total comes from hourly sums, not the sum of server averages.
                {"players": 140.004, "lfms": 13.0},
            ),
        )
        start = datetime(2026, 11, 1, tzinfo=UTC)

        result = population.summarize_window(start, start + timedelta(days=1))

        assert result["coverage"] == 0.5
        assert result["total"] == {"players": 140.0, "lfms": 13.0}
        assert result["servers"]["Argonnessen"]["players"] == 100.12
        assert result["start"] == "2026-11-01T00:00:00Z"


def test_summary_handles_missing_previous_period(monkeypatch):
    calls = {}
    end = datetime(2026, 11, 21, 15, tzinfo=UTC)
    monkeypatch.setattr(population, "_start_of_hour_utc", lambda: end)
    monkeypatch.setattr(
        population, "get_cached_data_with_fallback", _passthrough_cache(calls)
    )
    one_year_ago = end - timedelta(days=300)
    monkeypatch.setattr(
        population,
        "summarize_window",
        lambda start, stop: None if start < one_year_ago else {"total": {}},
    )
    db = population.population_db
    monkeypatch.setattr(
        db,
        "get_average_total_by_weekday_and_hour",
        lambda s, e: ([{"day": 0, "players": 1.234}], [{"hour": 3, "players": 2.0}]),
    )
    monkeypatch.setattr(
        db,
        "get_peak_total_hour",
        lambda s, e: {"timestamp": "2026-01-01T20:00:00Z", "players": 999.999},
    )
    monkeypatch.setattr(db, "get_peak_server_snapshot", lambda s, e: None)

    result = population.get_population_trends_summary()

    assert calls["cache"][0] == "population_trends_summary"
    assert result["comparisons"]["year_over_year"]["previous"] is None
    assert result["comparisons"]["week"]["current"] == {"total": {}}
    assert result["records"]["peak_hour_all_time"]["players"] == 1000.0
    assert result["records"]["peak_server_all_time"] is None
    assert result["records"]["last_90_days_by_weekday"] == [{"day": 0, "players": 1.23}]


class TestLegacyLongRangeGetters:
    @pytest.mark.parametrize(
        "getter_name,days,key,ttl",
        [
            (
                "get_game_population_quarter",
                90,
                "get_game_population_quarter",
                population.REPORT_1_QUARTER_CACHE_TTL,
            ),
            (
                "get_game_population_year",
                365,
                "get_game_population_year",
                population.REPORT_1_YEAR_CACHE_TTL,
            ),
        ],
    )
    def test_timeseries_reads_daily_rollup(self, monkeypatch, getter_name, days, key, ttl):
        calls = {}
        today = datetime(2026, 11, 21, tzinfo=UTC)
        monkeypatch.setattr(population, "_start_of_today_utc", lambda: today)
        monkeypatch.setattr(
            population, "get_cached_data_with_fallback", _passthrough_cache(calls)
        )
        point = PopulationPointInTime(
            timestamp="2026-11-20T00:00:00Z",
            data={"Argonnessen": PopulationDataPoint(character_count=5, lfm_count=1)},
        )

        def fake_daily(start, end):
            calls["window"] = (start, end)
            return [point]

        monkeypatch.setattr(
            population.population_db, "get_daily_population_points", fake_daily
        )

        result = getattr(population, getter_name)()

        assert calls["window"] == (today - timedelta(days=days), today)
        assert calls["cache"] == (key, ttl)
        assert result == [point.model_dump()]

    @pytest.mark.parametrize(
        "getter_name,days",
        [
            ("get_game_population_totals_quarter", 90),
            ("get_game_population_totals_year", 365),
        ],
    )
    def test_totals_read_rollup(self, monkeypatch, getter_name, days):
        calls = {}
        today = datetime(2026, 11, 21, tzinfo=UTC)
        monkeypatch.setattr(population, "_start_of_today_utc", lambda: today)
        monkeypatch.setattr(
            population, "get_cached_data_with_fallback", _passthrough_cache(calls)
        )
        totals = {"Argonnessen": {"character_count": 120, "lfm_count": 12}}

        def fake_totals(start, end):
            calls["window"] = (start, end)
            return totals

        monkeypatch.setattr(population.population_db, "get_population_totals", fake_totals)

        assert getattr(population, getter_name)() == totals
        assert calls["window"] == (today - timedelta(days=days), today)


class FakeRedis:
    def __init__(self, keys):
        self.keys = set(keys)
        self.deleted = []

    def scan_iter(self, match):
        prefix = match.rstrip("*")
        return [k for k in sorted(self.keys) if k.startswith(prefix)]

    def delete(self, *keys):
        self.deleted.extend(keys)
        return len([k for k in keys if k in self.keys])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_invalidate_long_range_population_caches(monkeypatch):
    fake = FakeRedis(
        [
            "get_game_population_year",
            "population_trends_all_week",
            "population_trends_year_day",
            "get_game_population_week",
        ]
    )
    monkeypatch.setattr(population.redis_client, "get_redis_client", lambda: fake)

    removed = population.invalidate_long_range_population_caches()

    assert removed == 3
    assert "get_game_population_week" not in fake.deleted
    assert {"population_trends_all_week", "population_trends_year_day"} <= set(
        fake.deleted
    )
    assert "get_game_population_totals_quarter" in fake.deleted
