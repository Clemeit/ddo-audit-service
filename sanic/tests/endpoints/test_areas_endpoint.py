from types import SimpleNamespace

import endpoints.areas as area_endpoints


def _area(area_id=1, name="Area"):
    area = SimpleNamespace(id=area_id, name=name)
    area.model_dump = lambda: {"id": area.id, "name": area.name}
    return area


def test_get_all_areas_parses_force_query_parameter(
    monkeypatch, make_request, run_async, response_json
):
    captured = {}

    def _get_areas(skip_cache=False):
        captured["skip_cache"] = skip_cache
        return ([{"id": 1, "name": "Korthos"}], "database", "2026-03-15T00:00:00+00:00")

    monkeypatch.setattr(area_endpoints, "get_areas", _get_areas)

    request = make_request(path="/v1/areas")
    request.args = {"force": "true"}
    response = run_async(area_endpoints.get_all_areas(request))

    assert response.status == 200
    payload = response_json(response)
    assert captured["skip_cache"] is True
    assert payload["source"] == "database"


def test_get_all_areas_returns_404_when_none_found(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        area_endpoints,
        "get_areas",
        lambda skip_cache=False: ([], "cache", "2026-03-15T00:00:00+00:00"),
    )

    request = make_request(path="/v1/areas")
    request.args = {}
    response = run_async(area_endpoints.get_all_areas(request))

    assert response.status == 404
    assert response_json(response)["message"] == "no areas found"


def test_get_area_by_id_returns_serialized_area(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        area_endpoints.postgres_client,
        "get_area_by_id",
        lambda _area_id: _area(area_id=7, name="Stormreach"),
    )

    request = make_request(path="/v1/areas/7")
    response = run_async(area_endpoints.get_area_by_id(request, 7))

    assert response.status == 200
    assert response_json(response)["data"]["name"] == "Stormreach"


def test_get_area_by_id_returns_404_when_missing(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        area_endpoints.postgres_client,
        "get_area_by_id",
        lambda _area_id: None,
    )

    request = make_request(path="/v1/areas/999")
    response = run_async(area_endpoints.get_area_by_id(request, 999))

    assert response.status == 404
    assert response_json(response)["message"] == "area not found"


def test_get_area_by_name_returns_500_on_database_error(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        area_endpoints.postgres_client,
        "get_area_by_name",
        lambda: (_ for _ in ()).throw(RuntimeError("db failed")),
    )

    request = make_request(path="/v1/areas/stormreach")
    response = run_async(area_endpoints.get_area_by_name(request))

    assert response.status == 500
    assert response_json(response)["message"] == "db failed"


def test_update_areas_returns_400_when_body_empty(
    make_request, run_async, response_json
):
    request = make_request(method="POST", path="/v1/areas", json_body=[])
    response = run_async(area_endpoints.update_areas(request))

    assert response.status == 400
    assert response_json(response)["message"] == "no areas provided"


def test_update_areas_converts_payload_and_persists(
    monkeypatch, make_request, run_async, response_json
):
    captured = {}

    def _update_areas(area_list):
        captured["area_list"] = area_list

    monkeypatch.setattr(area_endpoints.postgres_client, "update_areas", _update_areas)

    request = make_request(
        method="POST",
        path="/v1/areas",
        json_body=[
            {
                "areaid": "10",
                "name": "Public Space",
                "ispublicspace": "1",
                "region": "Eberron",
            },
            {
                "areaid": "11",
                "name": "Private Space",
                "ispublicspace": "0",
                "region": "Forgotten",
            },
        ],
    )

    response = run_async(area_endpoints.update_areas(request))

    assert response.status == 200
    assert response_json(response)["message"] == "areas updated"
    assert len(captured["area_list"]) == 2
    assert captured["area_list"][0].is_public is True
    assert captured["area_list"][1].is_public is False
