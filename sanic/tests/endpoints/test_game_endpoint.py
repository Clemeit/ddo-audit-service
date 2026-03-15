from types import SimpleNamespace

import endpoints.game as game_endpoints


def test_get_game_info_returns_cached_data(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        game_endpoints.redis_client,
        "get_server_info_as_dict",
        lambda: {"khyber": {"character_count": 42}},
    )

    request = make_request(path="/v1/game/server-info")
    response = run_async(game_endpoints.get_game_info(request))

    assert response.status == 200
    assert response_json(response)["khyber"]["character_count"] == 42


def test_get_server_info_by_server_rejects_invalid_server(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(game_endpoints, "is_server_name_valid", lambda _server: False)

    request = make_request(path="/v1/game/server-info/bad")
    response = run_async(game_endpoints.get_server_info_by_server(request, "bad"))

    assert response.status == 400
    assert response_json(response)["message"] == "Invalid server name"


def test_patch_game_info_returns_400_for_invalid_body(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        game_endpoints,
        "ServerInfo",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("invalid")),
    )

    request = make_request(method="PATCH", path="/v1/game/server-info", json_body={})
    response = run_async(game_endpoints.patch_game_info(request))

    assert response.status == 400
    assert response_json(response)["message"] == "Invalid request body"


def test_patch_game_info_returns_500_when_merge_fails(
    monkeypatch, make_request, run_async, response_json
):
    captured = {}

    request_body = SimpleNamespace(
        model_dump=lambda: {"khyber": {"character_count": 1}}
    )
    monkeypatch.setattr(game_endpoints, "ServerInfo", lambda **_kwargs: request_body)

    monkeypatch.setattr(
        game_endpoints.redis_client,
        "merge_server_info",
        lambda _payload: (_ for _ in ()).throw(RuntimeError("merge failed")),
    )

    def _log_message(**kwargs):
        captured["message"] = kwargs["message"]
        captured["action"] = kwargs["action"]
        captured["metadata_error"] = kwargs["metadata"]["error"]

    monkeypatch.setattr(game_endpoints, "logMessage", _log_message)

    request = make_request(
        method="PATCH",
        path="/v1/game/server-info",
        json_body={"khyber": {"character_count": 1}},
    )
    response = run_async(game_endpoints.patch_game_info(request))

    assert response.status == 500
    assert response_json(response)["message"] == "merge failed"
    assert captured["message"] == "Error handling incoming game info"
    assert captured["action"] == "patch_game_info"
    assert captured["metadata_error"] == "merge failed"


def test_patch_game_info_success_ignores_heartbeat_failure(
    monkeypatch, make_request, run_async, response_json
):
    request_body = SimpleNamespace(
        model_dump=lambda: {"khyber": {"character_count": 10}}
    )
    monkeypatch.setattr(game_endpoints, "ServerInfo", lambda **_kwargs: request_body)
    monkeypatch.setattr(
        game_endpoints.redis_client, "merge_server_info", lambda _payload: None
    )
    monkeypatch.setattr(
        game_endpoints,
        "server_info_heartbeat",
        lambda: (_ for _ in ()).throw(RuntimeError("heartbeat down")),
    )

    request = make_request(
        method="PATCH",
        path="/v1/game/server-info",
        json_body={"khyber": {"character_count": 10}},
    )
    response = run_async(game_endpoints.patch_game_info(request))

    assert response.status == 200
    assert response_json(response)["message"] == "success"
