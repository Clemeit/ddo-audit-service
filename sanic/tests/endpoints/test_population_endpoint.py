import endpoints.population as population_endpoints


def test_get_population_timeseries_returns_data_for_valid_period(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        population_endpoints.population_utils,
        "get_game_population_week",
        lambda: [{"server_name": "Khyber", "character_count": 10}],
    )

    request = make_request(path="/v1/population/timeseries/week")
    response = run_async(
        population_endpoints.get_population_timeseries(request, "week")
    )

    assert response.status == 200
    assert response_json(response)["data"][0]["server_name"] == "Khyber"


def test_get_population_timeseries_returns_400_for_invalid_period(
    make_request, run_async, response_json
):
    request = make_request(path="/v1/population/timeseries/invalid")
    response = run_async(
        population_endpoints.get_population_timeseries(request, "invalid")
    )

    assert response.status == 400
    assert "Invalid period" in response_json(response)["message"]


def test_get_population_totals_returns_500_when_utility_raises(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        population_endpoints.population_utils,
        "get_game_population_totals_day",
        lambda: (_ for _ in ()).throw(RuntimeError("population cache failed")),
    )

    request = make_request(path="/v1/population/totals/day")
    response = run_async(population_endpoints.get_population_totals(request, "day"))

    assert response.status == 500
    assert response_json(response)["message"] == "population cache failed"


def test_get_stats_breakdown_returns_data_for_quarter(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        population_endpoints.population_utils,
        "get_character_activity_stats_quarter",
        lambda: {"active": 123},
    )

    request = make_request(path="/v1/population/stats/quarter")
    response = run_async(population_endpoints.get_stats_breakdown(request, "quarter"))

    assert response.status == 200
    assert response_json(response)["data"]["active"] == 123


def test_get_population_by_hour_and_day_of_week_rejects_invalid_period(
    make_request, run_async, response_json
):
    request = make_request(path="/v1/population/by-hour-and-day-of-week/day")
    response = run_async(
        population_endpoints.get_population_by_hour_and_day_of_week(request, "day")
    )

    assert response.status == 400
    assert "Supported periods" in response_json(response)["message"]
