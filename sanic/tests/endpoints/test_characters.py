from types import SimpleNamespace

import endpoints.characters as character_endpoints
from tests.conftest import _amock


def _db_character(character_id=1, name="Character", is_anonymous=False):
    character = SimpleNamespace(
        id=character_id,
        name=name,
        is_online=True,
        is_anonymous=is_anonymous,
    )
    character.model_dump = lambda: {
        "id": character.id,
        "name": character.name,
        "is_online": character.is_online,
        "is_anonymous": character.is_anonymous,
    }
    return character


def test_get_characters_by_server_rejects_invalid_server(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(character_endpoints, "is_server_name_valid", lambda _s: False)

    request = make_request(path="/v1/characters/bad")
    response = run_async(character_endpoints.get_characters_by_server(request, "bad"))

    assert response.status == 400
    assert response_json(response)["message"] == "Invalid server name"


def test_get_characters_by_server_returns_data(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(character_endpoints, "is_server_name_valid", lambda _s: True)
    monkeypatch.setattr(
        character_endpoints.redis_client,
        "get_characters_by_server_name_as_dict",
        lambda _server_name: {1: {"name": "Alice"}},
    )

    request = make_request(path="/v1/characters/Khyber")
    response = run_async(
        character_endpoints.get_characters_by_server(request, "Khyber")
    )

    assert response.status == 200
    assert response_json(response)["data"]["1"]["name"] == "Alice"


def test_get_character_by_id_prefers_cache(
    make_request, run_async, response_json, monkeypatch
):
    monkeypatch.setattr(
        character_endpoints.redis_client,
        "get_character_by_id_as_dict",
        lambda _character_id: {"id": 7, "name": "Cached"},
    )

    request = make_request(path="/v1/characters/7")
    response = run_async(character_endpoints.get_character_by_id(request, 7))

    assert response.status == 200
    payload = response_json(response)
    assert payload["source"] == "cache"
    assert payload["data"]["is_online"] is True


def test_get_character_by_id_falls_back_to_database(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        character_endpoints.redis_client,
        "get_character_by_id_as_dict",
        lambda _character_id: None,
    )
    monkeypatch.setattr(
        character_endpoints.postgres_client,
        "async_get_character_by_id",
        _amock(lambda _character_id: _db_character(character_id=11, name="Persisted")),
    )

    request = make_request(path="/v1/characters/11")
    response = run_async(character_endpoints.get_character_by_id(request, 11))

    assert response.status == 200
    payload = response_json(response)
    assert payload["source"] == "database"
    assert payload["data"]["is_online"] is False


def test_get_character_by_id_returns_404_when_missing(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        character_endpoints.redis_client,
        "get_character_by_id_as_dict",
        lambda _character_id: None,
    )
    monkeypatch.setattr(
        character_endpoints.postgres_client,
        "async_get_character_by_id",
        _amock(lambda _character_id: None),
    )

    request = make_request(path="/v1/characters/999")
    response = run_async(character_endpoints.get_character_by_id(request, 999))

    assert response.status == 404
    assert response_json(response)["message"] == "Character not found"


def test_get_characters_by_ids_rejects_non_numeric_input(
    make_request, run_async, response_json
):
    request = make_request(path="/v1/characters/ids/1,a")
    response = run_async(character_endpoints.get_characters_by_ids(request, "1,a"))

    assert response.status == 400
    assert response_json(response)["message"] == "Invalid character IDs"


def test_get_characters_by_ids_merges_cache_and_database(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        character_endpoints.redis_client,
        "get_characters_by_ids_as_dict",
        lambda _ids: {1: {"id": 1, "name": "Cached One"}},
    )
    monkeypatch.setattr(
        character_endpoints.postgres_client,
        "async_get_characters_by_ids",
        _amock(lambda _ids: [_db_character(character_id=2, name="Persisted Two")]),
    )

    request = make_request(path="/v1/characters/ids/1,2")
    response = run_async(character_endpoints.get_characters_by_ids(request, "1,2"))

    assert response.status == 200
    data = response_json(response)["data"]
    assert set(data.keys()) == {"1", "2"}
    assert data["1"]["is_online"] is True
    assert data["2"]["is_online"] is False


def test_get_character_by_server_and_name_returns_403_for_anonymous(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(character_endpoints, "is_server_name_valid", lambda _s: True)
    monkeypatch.setattr(character_endpoints, "is_character_name_valid", lambda _n: True)
    monkeypatch.setattr(
        character_endpoints.redis_client,
        "get_character_by_name_and_server_name_as_dict",
        lambda _name, _server_name: None,
    )
    monkeypatch.setattr(
        character_endpoints.postgres_client,
        "async_get_character_by_name_and_server",
        _amock(
            lambda _name, _server_name: _db_character(
                character_id=8, name="Anonymous", is_anonymous=True
            )
        ),
    )

    request = make_request(path="/v1/characters/Khyber/Anonymous")
    response = run_async(
        character_endpoints.get_character_by_server_name_and_character_name(
            request, "Khyber", "Anonymous"
        )
    )

    assert response.status == 403
    assert response_json(response)["message"] == "Character is anonymous"


def test_get_character_playstyle_score_rejects_invalid_character_id(
    make_request, run_async, response_json
):
    request = make_request(path="/v1/characters/playstyle-score/0")
    response = run_async(character_endpoints.get_character_playstyle_score(request, 0))

    assert response.status == 400
    assert response_json(response)["message"] == "Invalid character ID"


def test_get_character_playstyle_score_returns_calculated_score(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        character_endpoints.redis_client,
        "get_character_by_id_as_dict",
        lambda _character_id: {"id": 10, "name": "Score Me"},
    )
    monkeypatch.setattr(
        character_endpoints.postgres_client,
        "async_get_all_character_activity_by_character_id",
        _amock(lambda _character_id: [{"kind": "login"}]),
    )
    monkeypatch.setattr(
        character_endpoints,
        "calculate_active_playstyle_score",
        lambda character, activities: 0.73,
    )

    request = make_request(path="/v1/characters/playstyle-score/10")
    response = run_async(character_endpoints.get_character_playstyle_score(request, 10))

    assert response.status == 200
    assert response_json(response)["data"] == 0.73


def test_set_characters_returns_400_for_invalid_request_body(
    monkeypatch, make_request, run_async, response_json
):
    def _invalid_model(**_kwargs):
        raise ValueError("invalid")

    monkeypatch.setattr(character_endpoints, "CharacterRequestApiModel", _invalid_model)

    request = make_request(method="POST", path="/v1/characters", json_body={"bad": 1})
    response = run_async(character_endpoints.set_characters(request))

    assert response.status == 400
    assert response_json(response)["message"] == "Invalid request body"


def test_set_characters_success_calls_handler_with_set_type(
    monkeypatch, make_request, run_async, response_json
):
    captured = {}

    monkeypatch.setattr(
        character_endpoints,
        "CharacterRequestApiModel",
        lambda **kwargs: SimpleNamespace(model_dump=lambda: kwargs),
    )

    def _handle(request_body, request_type):
        captured["request_body"] = request_body
        captured["request_type"] = request_type

    monkeypatch.setattr(
        character_endpoints, "handle_incoming_characters", _amock(_handle)
    )
    monkeypatch.setattr(
        character_endpoints,
        "character_collections_heartbeat",
        lambda: (_ for _ in ()).throw(RuntimeError("heartbeat down")),
    )

    request = make_request(method="POST", path="/v1/characters", json_body={"x": 1})
    response = run_async(character_endpoints.set_characters(request))

    assert response.status == 200
    assert response_json(response)["message"] == "success"
    assert captured["request_type"] == character_endpoints.CharacterRequestType.set


def test_update_characters_returns_500_when_business_layer_fails(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        character_endpoints,
        "CharacterRequestApiModel",
        lambda **kwargs: SimpleNamespace(model_dump=lambda: kwargs),
    )

    def _raise(_request_body, _request_type):
        raise RuntimeError("processing failed")

    monkeypatch.setattr(
        character_endpoints,
        "handle_incoming_characters",
        _amock(_raise),
    )

    request = make_request(
        method="PATCH", path="/v1/characters", json_body={"events": [1]}
    )
    response = run_async(character_endpoints.update_characters(request))

    assert response.status == 500
    assert response_json(response)["message"] == "processing failed"
