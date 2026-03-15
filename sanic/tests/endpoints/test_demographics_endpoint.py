import endpoints.demographics as demographics_endpoints
from utils.demographics import ReportLookback


def test_validate_activity_level_accepts_supported_values():
    assert demographics_endpoints.validate_activity_level("all") == "all"
    assert demographics_endpoints.validate_activity_level("ACTIVE") == "active"
    assert demographics_endpoints.validate_activity_level("inactive") == "inactive"


def test_get_population_race_returns_400_for_invalid_activity_level(
    make_request, run_async, response_json
):
    request = make_request(path="/v1/demographics/race/day")
    request.args = {"activity_level": "unknown"}

    response = run_async(demographics_endpoints.get_population_race(request, "day"))

    assert response.status == 400
    assert "Invalid activity_level" in response_json(response)["message"]


def test_get_population_gender_returns_400_for_invalid_period(
    make_request, run_async, response_json
):
    request = make_request(path="/v1/demographics/gender/bad")
    request.args = {"activity_level": "all"}

    response = run_async(demographics_endpoints.get_population_gender(request, "bad"))

    assert response.status == 400
    assert "Invalid period" in response_json(response)["message"]


def test_get_population_total_level_delegates_to_util_with_lookback_and_activity(
    monkeypatch, make_request, run_async, response_json
):
    captured = {}

    def _distribution(lookback, activity_level):
        captured["lookback"] = lookback
        captured["activity_level"] = activity_level
        return {"levels": [{"level": 32, "count": 9}]}

    monkeypatch.setattr(
        demographics_endpoints.demographics_utils,
        "get_total_level_distribution",
        _distribution,
    )

    request = make_request(path="/v1/demographics/total-level/month")
    request.args = {"activity_level": "active"}

    response = run_async(
        demographics_endpoints.get_population_total_level(request, "month")
    )

    assert response.status == 200
    assert captured["lookback"] == ReportLookback.month
    assert captured["activity_level"] == "active"
    assert response_json(response)["data"]["levels"][0]["count"] == 9


def test_get_guild_affiliation_demographics_returns_500_on_failure(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        demographics_endpoints.demographics_utils,
        "get_guild_affiliation_distribution",
        lambda _lookback, _activity: (_ for _ in ()).throw(
            RuntimeError("demographics query failed")
        ),
    )

    request = make_request(path="/v1/demographics/guild-affiliated/year")
    request.args = {"activity_level": "inactive"}

    response = run_async(
        demographics_endpoints.get_guild_affiliation_demographics(request, "year")
    )

    assert response.status == 500
    assert response_json(response)["message"] == "demographics query failed"
