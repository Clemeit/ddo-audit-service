from datetime import datetime, timezone
from types import SimpleNamespace

import endpoints.quests as quest_endpoints


def _quest(quest_id=1, name="Quest"):
    quest = SimpleNamespace(id=quest_id, name=name)
    quest.model_dump = lambda: {"id": quest.id, "name": quest.name}
    return quest


def test_get_quest_by_name_returns_404_when_missing(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        quest_endpoints.postgres_client,
        "get_quest_by_name",
        lambda _quest_name: None,
    )

    request = make_request(path="/v1/quests/unknown")
    response = run_async(quest_endpoints.get_quest_by_name(request, "unknown"))

    assert response.status == 404
    assert response_json(response)["message"] == "quest not found"


def test_get_quest_by_name_returns_serialized_quest(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        quest_endpoints.postgres_client,
        "get_quest_by_name",
        lambda _quest_name: _quest(quest_id=5, name="Waterworks"),
    )

    request = make_request(path="/v1/quests/Waterworks")
    response = run_async(quest_endpoints.get_quest_by_name(request, "Waterworks"))

    assert response.status == 200
    assert response_json(response)["data"]["name"] == "Waterworks"


def test_get_quest_by_id_returns_500_on_unexpected_error(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        quest_endpoints.postgres_client,
        "get_quest_by_id",
        lambda _quest_id: (_ for _ in ()).throw(RuntimeError("db failed")),
    )

    request = make_request(path="/v1/quests/10")
    response = run_async(quest_endpoints.get_quest_by_id(request, 10))

    assert response.status == 500
    assert response_json(response)["message"] == "db failed"


def test_get_all_quests_parses_force_query_parameter(
    monkeypatch, make_request, run_async, response_json
):
    captured = {}

    def _get_quests(skip_cache=False):
        captured["skip_cache"] = skip_cache
        return ([{"id": 1, "name": "Quest"}], "database", "2026-03-15T00:00:00+00:00")

    monkeypatch.setattr(quest_endpoints, "get_quests", _get_quests)

    request = make_request(path="/v1/quests")
    request.args = {"force": "true"}
    response = run_async(quest_endpoints.get_all_quests(request))

    assert response.status == 200
    payload = response_json(response)
    assert captured["skip_cache"] is True
    assert payload["source"] == "database"


def test_get_all_quests_returns_404_when_none_found(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        quest_endpoints,
        "get_quests",
        lambda skip_cache=False: ([], "cache", "2026-03-15T00:00:00+00:00"),
    )

    request = make_request(path="/v1/quests")
    request.args = {}
    response = run_async(quest_endpoints.get_all_quests(request))

    assert response.status == 404
    assert response_json(response)["message"] == "no quests found"


def test_get_all_quests_with_analytics_rejects_invalid_page(
    make_request, run_async, response_json
):
    request = make_request(path="/v1/quests/analytics")
    request.args = {"page": "abc"}

    response = run_async(quest_endpoints.get_all_quests_with_analytics(request))

    assert response.status == 400
    assert response_json(response)["message"] == "invalid page"


def test_get_all_quests_with_analytics_rejects_invalid_sort_by(
    make_request, run_async, response_json
):
    request = make_request(path="/v1/quests/analytics")
    request.args = {"sort_by": "drop table quests"}

    response = run_async(quest_endpoints.get_all_quests_with_analytics(request))

    assert response.status == 400
    assert "invalid sort_by" in response_json(response)["message"]


def test_get_all_quests_with_analytics_returns_empty_page_when_no_items(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        quest_endpoints.postgres_client,
        "get_quests_with_metrics_paginated",
        lambda page, page_size, sort_by, sort_dir: ([], 0),
    )

    request = make_request(path="/v1/quests/analytics")
    request.args = {}
    response = run_async(quest_endpoints.get_all_quests_with_analytics(request))

    assert response.status == 200
    payload = response_json(response)
    assert payload["data"] == []
    assert payload["total"] == 0


def test_get_all_quests_with_analytics_serializes_metrics_datetime(
    monkeypatch, make_request, run_async, response_json
):
    metrics = {
        "heroic_xp_per_minute_relative": 0.9,
        "epic_xp_per_minute_relative": 1.1,
        "heroic_popularity_relative": 0.4,
        "epic_popularity_relative": 0.6,
        "analytics_data": {"total_sessions": 14},
        "updated_at": datetime(2026, 3, 15, 11, 30, tzinfo=timezone.utc),
        "total_sessions": 14,
    }

    monkeypatch.setattr(
        quest_endpoints.postgres_client,
        "get_quests_with_metrics_paginated",
        lambda page, page_size, sort_by, sort_dir: (
            [(_quest(quest_id=13, name="Sunken Sewer"), metrics)],
            1,
        ),
    )

    request = make_request(path="/v1/quests/analytics")
    request.args = {}
    response = run_async(quest_endpoints.get_all_quests_with_analytics(request))

    assert response.status == 200
    item = response_json(response)["data"][0]
    assert item["quest"]["id"] == 13
    assert item["metrics"]["updated_at"] == "2026-03-15T11:30:00+00:00"


def test_update_quests_returns_400_when_body_empty(
    make_request, run_async, response_json
):
    request = make_request(method="POST", path="/v1/quests", json_body=[])
    response = run_async(quest_endpoints.update_quests(request))

    assert response.status == 400
    assert response_json(response)["message"] == "no quests provided"


def test_update_quests_filters_invalid_entries_and_updates_database(
    monkeypatch, make_request, run_async, response_json
):
    captured = {}

    monkeypatch.setattr(
        quest_endpoints,
        "get_valid_area_ids",
        lambda: ({10, 20}, "cache", "2026-03-15T00:00:00+00:00"),
    )

    def _update_quests(quest_list):
        captured["quest_list"] = quest_list

    monkeypatch.setattr(
        quest_endpoints.postgres_client, "update_quests", _update_quests
    )

    request = make_request(
        method="POST",
        path="/v1/quests",
        json_body=[
            {
                "questid": 101,
                "altid": 201,
                "area": 10,
                "name": "Good Quest",
                "heroicnormalcr": 4,
                "epicnormalcr": 34,
                "requiredadventurepack": "Pack",
                "adventurearea": "Area",
                "questjournalgroup": "Group",
                "groupsize": "6",
                "patron": "patron",
                "heroiccasualxp": 10,
                "heroicnormalxp": 20,
                "heroichardxp": 30,
                "heroicelitexp": 40,
                "epiccasualxp": 50,
                "epicnormalxp": 60,
                "epichardxp": 70,
                "epicelitexp": 80,
                "length": 15,
                "tip": "tip",
                "isfreetovip": "1",
            },
            {
                "questid": 102,
                "altid": 202,
                "area": 10,
                "name": "DNT Internal Quest",
            },
            {
                "questid": 103,
                "altid": 203,
                "area": 999,
                "name": "Wrong Area",
            },
        ],
    )

    response = run_async(quest_endpoints.update_quests(request))

    assert response.status == 200
    assert response_json(response)["message"] == "quest updated"
    assert len(captured["quest_list"]) == 1
    assert captured["quest_list"][0].id == 101
    assert captured["quest_list"][0].name == "Good Quest"


def test_get_quest_analytics_v2_returns_404_when_quest_missing(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        quest_endpoints.postgres_client, "get_quest_by_id", lambda _id: None
    )

    request = make_request(path="/v2/quests/8/analytics")
    request.args = {}
    response = run_async(quest_endpoints.get_quest_analytics(request, 8))

    assert response.status == 404
    assert response_json(response)["message"] == "quest not found"


def test_get_quest_analytics_v2_returns_cached_metrics_when_available(
    monkeypatch, make_request, run_async, response_json
):
    cached_metrics = {
        "heroic_xp_per_minute_relative": 0.9,
        "epic_xp_per_minute_relative": 1.0,
        "heroic_popularity_relative": 0.7,
        "epic_popularity_relative": 0.6,
        "analytics_data": {"total_sessions": 99},
        "updated_at": datetime(2026, 3, 15, 12, 0, tzinfo=timezone.utc),
    }

    monkeypatch.setattr(
        quest_endpoints.postgres_client, "get_quest_by_id", lambda _id: _quest()
    )
    monkeypatch.setattr(
        quest_endpoints.postgres_client,
        "get_quest_metrics",
        lambda _id: cached_metrics,
    )
    monkeypatch.setattr(
        quest_endpoints,
        "get_quest_metrics_single",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("should not recalculate when cached")
        ),
    )

    request = make_request(path="/v2/quests/8/analytics")
    request.args = {}
    response = run_async(quest_endpoints.get_quest_analytics(request, 8))

    assert response.status == 200
    payload = response_json(response)
    assert payload["cached"] is True
    assert payload["data"]["analytics_data"]["total_sessions"] == 99


def test_get_quest_analytics_v2_refresh_recalculates_and_upserts(
    monkeypatch, make_request, run_async, response_json
):
    captured = {}

    quest_metrics = {
        "heroic_xp_per_minute_relative": 1.2,
        "epic_xp_per_minute_relative": 1.1,
        "heroic_popularity_relative": 0.5,
        "epic_popularity_relative": 0.4,
        "analytics_data": {"total_sessions": 11},
    }

    monkeypatch.setattr(
        quest_endpoints.postgres_client, "get_quest_by_id", lambda _id: _quest()
    )
    monkeypatch.setattr(
        quest_endpoints.postgres_client,
        "get_quest_metrics",
        lambda _id: {"unused": True},
    )

    def _get_single(quest_id, force_refresh=False, cached_metrics=None):
        captured["single_args"] = (quest_id, force_refresh, cached_metrics)
        return quest_metrics

    def _upsert(
        quest_id,
        heroic_xp,
        epic_xp,
        heroic_popularity,
        epic_popularity,
        analytics_data,
    ):
        captured["upsert_args"] = (
            quest_id,
            heroic_xp,
            epic_xp,
            heroic_popularity,
            epic_popularity,
            analytics_data,
        )

    monkeypatch.setattr(quest_endpoints, "get_quest_metrics_single", _get_single)
    monkeypatch.setattr(
        quest_endpoints.postgres_client, "upsert_quest_metrics", _upsert
    )

    request = make_request(path="/v2/quests/8/analytics")
    request.args = {"refresh": "true"}
    response = run_async(quest_endpoints.get_quest_analytics(request, 8))

    assert response.status == 200
    payload = response_json(response)
    assert payload["cached"] is False
    assert captured["single_args"] == (8, True, None)
    assert captured["upsert_args"][0] == 8


def test_get_quest_analytics_v2_returns_404_for_insufficient_metrics(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        quest_endpoints.postgres_client, "get_quest_by_id", lambda _id: _quest()
    )
    monkeypatch.setattr(
        quest_endpoints.postgres_client, "get_quest_metrics", lambda _id: None
    )
    monkeypatch.setattr(
        quest_endpoints,
        "get_quest_metrics_single",
        lambda quest_id, force_refresh=False, cached_metrics=None: None,
    )

    request = make_request(path="/v2/quests/8/analytics")
    request.args = {}
    response = run_async(quest_endpoints.get_quest_analytics(request, 8))

    assert response.status == 404
    assert response_json(response)["message"] == "insufficient data for metrics"


def test_get_all_quests_v2_parses_force_query_parameter(
    monkeypatch, make_request, run_async, response_json
):
    captured = {}

    def _get_quests_with_metrics(skip_cache=False):
        captured["skip_cache"] = skip_cache
        return (
            [{"id": 1, "name": "Quest v2"}],
            "database",
            "2026-03-15T00:00:00+00:00",
        )

    monkeypatch.setattr(
        quest_endpoints,
        "get_quests_with_metrics",
        _get_quests_with_metrics,
    )

    request = make_request(path="/v2/quests")
    request.args = {"force": "true"}
    response = run_async(quest_endpoints.get_all_quests_v2(request))

    assert response.status == 200
    payload = response_json(response)
    assert captured["skip_cache"] is True
    assert payload["source"] == "database"
