from models.area import Area
import utils.areas as areas


def _area(area_id: int, name: str) -> Area:
    return Area(id=area_id, name=name)


class TestGetAreas:
    def test_returns_cached_areas_when_cache_is_fresh(self, monkeypatch):
        cached_areas = [{"id": 1, "name": "Korthos"}]
        cached_timestamp = 1000.0

        monkeypatch.setattr(
            areas.redis_client,
            "get_known_areas",
            lambda: {"areas": cached_areas, "timestamp": cached_timestamp},
        )
        monkeypatch.setattr(
            areas.postgres_client,
            "get_all_areas",
            lambda: (_ for _ in ()).throw(AssertionError("DB should not be called")),
        )
        monkeypatch.setattr(areas, "time", lambda: cached_timestamp + 30)
        monkeypatch.setattr(
            areas,
            "timestamp_to_datetime_string",
            lambda ts: f"converted-{int(ts)}",
        )

        result = areas.get_areas()

        assert result == (cached_areas, "cache", "converted-1000")

    def test_falls_back_to_database_when_cache_is_stale(self, monkeypatch):
        stale_timestamp = 2000.0
        db_areas = [_area(1, "Korthos"), _area(2, "Marketplace")]
        set_calls = []

        monkeypatch.setattr(
            areas.redis_client,
            "get_known_areas",
            lambda: {"areas": [{"id": 1}], "timestamp": stale_timestamp},
        )
        monkeypatch.setattr(
            areas.postgres_client,
            "get_all_areas",
            lambda: db_areas,
        )
        monkeypatch.setattr(
            areas.redis_client,
            "set_known_areas",
            lambda payload: set_calls.append(payload),
        )
        monkeypatch.setattr(
            areas,
            "time",
            lambda: stale_timestamp + areas.VALID_AREA_CACHE_TTL + 1,
        )
        monkeypatch.setattr(
            areas,
            "get_current_datetime_string",
            lambda: "2026-03-15T00:00:00",
        )

        result = areas.get_areas()

        assert result == (
            [area.model_dump() for area in db_areas],
            "database",
            "2026-03-15T00:00:00",
        )
        assert set_calls == [db_areas]

    def test_returns_empty_tuple_when_database_has_no_areas(self, monkeypatch):
        monkeypatch.setattr(
            areas.redis_client,
            "get_known_areas",
            lambda: {"areas": [], "timestamp": 1000.0},
        )
        monkeypatch.setattr(
            areas, "time", lambda: 1000.0 + areas.VALID_AREA_CACHE_TTL + 1
        )
        monkeypatch.setattr(areas.postgres_client, "get_all_areas", lambda: [])

        assert areas.get_areas() == ([], None, None)

    def test_returns_empty_tuple_on_exception(self, monkeypatch):
        monkeypatch.setattr(
            areas.redis_client,
            "get_known_areas",
            lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )

        assert areas.get_areas() == ([], None, None)


class TestGetValidAreaIds:
    def test_extracts_ids_from_get_areas_response(self, monkeypatch):
        monkeypatch.setattr(
            areas,
            "get_areas",
            lambda: ([{"id": 7}, {"id": 8}], "cache", "ts"),
        )

        assert areas.get_valid_area_ids() == ([7, 8], "cache", "ts")

    def test_returns_empty_result_on_error(self, monkeypatch):
        monkeypatch.setattr(
            areas,
            "get_areas",
            lambda: (_ for _ in ()).throw(RuntimeError("unexpected")),
        )

        assert areas.get_valid_area_ids() == ([], None, None)
