from types import SimpleNamespace

import endpoints.lfms as lfm_endpoints


def test_get_lfms_by_server_rejects_invalid_server(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(lfm_endpoints, "is_server_name_valid", lambda _s: False)

    request = make_request(path="/v1/lfms/bad")
    response = run_async(lfm_endpoints.get_lfms_by_server(request, "bad"))

    assert response.status == 400
    assert response_json(response)["message"] == "Invalid server name"


def test_get_lfms_by_server_returns_data(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(lfm_endpoints, "is_server_name_valid", lambda _s: True)
    monkeypatch.setattr(
        lfm_endpoints.redis_client,
        "get_lfms_by_server_name_as_dict",
        lambda _server_name: {100: {"leader_name": "GroupLead"}},
    )

    request = make_request(path="/v1/lfms/Khyber")
    response = run_async(lfm_endpoints.get_lfms_by_server(request, "Khyber"))

    assert response.status == 200
    assert response_json(response)["data"]["100"]["leader_name"] == "GroupLead"


def test_get_all_lfms_returns_500_when_cache_lookup_fails(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        lfm_endpoints.redis_client,
        "get_all_lfms_as_dict",
        lambda: (_ for _ in ()).throw(RuntimeError("redis down")),
    )

    request = make_request(path="/v1/lfms")
    response = run_async(lfm_endpoints.get_all_lfms(request))

    assert response.status == 500
    assert response_json(response)["message"] == "redis down"


def test_set_lfms_returns_400_for_invalid_request_body(
    monkeypatch, make_request, run_async, response_json
):
    def _invalid_model(**_kwargs):
        raise ValueError("invalid")

    monkeypatch.setattr(lfm_endpoints, "LfmRequestApiModel", _invalid_model)

    request = make_request(method="POST", path="/v1/lfms", json_body={"bad": 1})
    response = run_async(lfm_endpoints.set_lfms(request))

    assert response.status == 400
    assert response_json(response)["message"] == "Invalid request body"


def test_set_lfms_success_calls_business_layer_with_set_type(
    monkeypatch, make_request, run_async, response_json
):
    captured = {}

    monkeypatch.setattr(
        lfm_endpoints,
        "LfmRequestApiModel",
        lambda **kwargs: SimpleNamespace(model_dump=lambda: kwargs),
    )

    def _handle(request_body, request_type):
        captured["request_body"] = request_body
        captured["request_type"] = request_type

    monkeypatch.setattr(lfm_endpoints, "handle_incoming_lfms", _handle)
    monkeypatch.setattr(
        lfm_endpoints,
        "lfm_collections_heartbeat",
        lambda: (_ for _ in ()).throw(RuntimeError("heartbeat down")),
    )

    request = make_request(method="POST", path="/v1/lfms", json_body={"batch": []})
    response = run_async(lfm_endpoints.set_lfms(request))

    assert response.status == 200
    assert response_json(response)["message"] == "success"
    assert captured["request_type"] == lfm_endpoints.LfmRequestType.set


def test_update_lfms_returns_500_when_business_layer_fails(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        lfm_endpoints,
        "LfmRequestApiModel",
        lambda **kwargs: SimpleNamespace(model_dump=lambda: kwargs),
    )
    monkeypatch.setattr(
        lfm_endpoints,
        "handle_incoming_lfms",
        lambda _request_body, _request_type: (_ for _ in ()).throw(
            RuntimeError("lfm processing failed")
        ),
    )

    request = make_request(
        method="PATCH", path="/v1/lfms", json_body={"events": [1, 2, 3]}
    )
    response = run_async(lfm_endpoints.update_lfms(request))

    assert response.status == 500
    assert response_json(response)["message"] == "lfm processing failed"
