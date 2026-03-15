import endpoints.activity as activity_endpoints


def test_get_activity_returns_401_when_authorization_missing(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        activity_endpoints,
        "verify_authorization",
        lambda _request, _character_id: (_ for _ in ()).throw(
            activity_endpoints.AuthorizationError("Authorization required")
        ),
    )

    request = make_request(path="/v1/activity/1/status")
    request.args = {}
    response = run_async(
        activity_endpoints.get_activity_by_character_id_and_activity_type(
            request, 1, "status"
        )
    )

    assert response.status == 401
    assert response_json(response)["message"] == "Authorization required"


def test_get_activity_returns_400_for_invalid_activity_type(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        activity_endpoints,
        "verify_authorization",
        lambda _request, _character_id: None,
    )

    request = make_request(path="/v1/activity/1/invalid")
    request.args = {}
    response = run_async(
        activity_endpoints.get_activity_by_character_id_and_activity_type(
            request, 1, "not-a-real-type"
        )
    )

    assert response.status == 400
    assert response_json(response)["message"] == "Invalid activity type"


def test_get_activity_returns_400_for_invalid_date_format(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        activity_endpoints,
        "verify_authorization",
        lambda _request, _character_id: None,
    )

    request = make_request(path="/v1/activity/1/status")
    request.args = {"start_date": "03-15-2026"}
    response = run_async(
        activity_endpoints.get_activity_by_character_id_and_activity_type(
            request, 1, "status"
        )
    )

    assert response.status == 400
    assert response_json(response)["message"] == "start_date must be YYYY-MM-DD"


def test_get_activity_returns_400_for_limit_above_max(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        activity_endpoints,
        "verify_authorization",
        lambda _request, _character_id: None,
    )

    request = make_request(path="/v1/activity/1/status")
    request.args = {"limit": "501"}
    response = run_async(
        activity_endpoints.get_activity_by_character_id_and_activity_type(
            request, 1, "status"
        )
    )

    assert response.status == 400
    assert response_json(response)["message"] == "limit must be <= 500"


def test_get_activity_success_passes_parsed_filters_to_postgres(
    monkeypatch, make_request, run_async, response_json
):
    captured = {}

    monkeypatch.setattr(
        activity_endpoints,
        "verify_authorization",
        lambda _request, _character_id: None,
    )

    def _get_activity(character_id, activity_type, start_date, end_date, limit):
        captured["character_id"] = character_id
        captured["activity_type"] = activity_type
        captured["start_date"] = start_date
        captured["end_date"] = end_date
        captured["limit"] = limit
        return [{"value": "ok"}]

    monkeypatch.setattr(
        activity_endpoints.postgres_client,
        "get_character_activity_by_type_and_character_id",
        _get_activity,
    )

    request = make_request(path="/v1/activity/15/status")
    request.args = {
        "start_date": "2026-03-01",
        "end_date": "2026-03-15",
        "limit": "25",
    }

    response = run_async(
        activity_endpoints.get_activity_by_character_id_and_activity_type(
            request, 15, "status"
        )
    )

    assert response.status == 200
    assert captured["character_id"] == 15
    assert captured["activity_type"] == activity_endpoints.CharacterActivityType.STATUS
    assert captured["start_date"].strftime("%Y-%m-%d") == "2026-03-01"
    assert captured["end_date"].strftime("%Y-%m-%d") == "2026-03-15"
    assert captured["limit"] == 25
    assert response_json(response)["data"][0]["value"] == "ok"


def test_get_recent_quests_returns_403_on_verification_error(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        activity_endpoints,
        "verify_authorization",
        lambda _request, _character_id: (_ for _ in ()).throw(
            activity_endpoints.VerificationError("This character has not been verified")
        ),
    )

    request = make_request(path="/v1/activity/15/quests")
    response = run_async(
        activity_endpoints.get_recent_quests_by_character_id(request, 15)
    )

    assert response.status == 403
    assert response_json(response)["message"] == "This character has not been verified"


def test_get_raid_activity_returns_400_when_character_ids_missing(
    make_request, run_async, response_json
):
    request = make_request(path="/v1/activity/raids")
    request.args = {}

    response = run_async(activity_endpoints.get_raid_activity_by_character_ids(request))

    assert response.status == 400
    assert (
        response_json(response)["message"]
        == "character_ids query parameter is required"
    )


def test_get_raid_activity_returns_400_for_non_integer_ids(
    make_request, run_async, response_json
):
    request = make_request(path="/v1/activity/raids")
    request.args = {"character_ids": "1,two,3"}

    response = run_async(activity_endpoints.get_raid_activity_by_character_ids(request))

    assert response.status == 400
    assert "comma-separated list of integers" in response_json(response)["message"]


def test_get_raid_activity_returns_data_for_valid_ids(
    monkeypatch, make_request, run_async, response_json
):
    captured = {}

    def _get_raid_activity(character_ids):
        captured["character_ids"] = character_ids
        return [{"raid_name": "VoN 6"}]

    monkeypatch.setattr(
        activity_endpoints.postgres_client,
        "get_recent_raid_activity_by_character_ids",
        _get_raid_activity,
    )

    request = make_request(path="/v1/activity/raids")
    request.args = {"character_ids": "1, 2, 3"}

    response = run_async(activity_endpoints.get_raid_activity_by_character_ids(request))

    assert response.status == 200
    assert captured["character_ids"] == [1, 2, 3]
    assert response_json(response)["data"][0]["raid_name"] == "VoN 6"


def test_verify_authorization_raises_for_invalid_access_token(
    monkeypatch, make_request
):
    monkeypatch.setattr(
        activity_endpoints.postgres_client,
        "get_access_token_by_character_id",
        lambda _character_id: "expected-token",
    )

    request = make_request(
        path="/v1/activity/1/status", headers={"Authorization": "bad"}
    )

    try:
        activity_endpoints.verify_authorization(request, 1)
    except activity_endpoints.AuthorizationError as exc:
        assert str(exc) == "Invalid access token"
    else:
        raise AssertionError("AuthorizationError was not raised")
