from types import SimpleNamespace

from conftest import _amock
import endpoints.guilds as guild_endpoints


def test_get_guilds_by_name_deprecated_rejects_invalid_name(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        guild_endpoints.guild_utils, "validate_guild_name", lambda _name: False
    )

    request = make_request(path="/v1/guilds/by-name/%40bad")
    response = run_async(
        guild_endpoints.get_guilds_by_name_deprecated(request, "%40bad")
    )

    assert response.status == 400
    assert "Guild name must be" in response_json(response)["message"]


def test_get_all_guilds_returns_400_for_invalid_page(
    make_request, run_async, response_json
):
    request = make_request(path="/v1/guilds")
    request.args = {"page": "0"}

    response = run_async(guild_endpoints.get_all_guilds(request))

    assert response.status == 400
    assert response_json(response)["message"] == "Invalid page number."


def test_get_all_guilds_filters_results_by_name_and_server(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        guild_endpoints.guild_utils,
        "async_get_all_guilds",
        _amock(
            lambda: [
                {"guild_name": "Stormwatch", "server_name": "Khyber"},
                {"guild_name": "Raiders", "server_name": "Orien"},
            ]
        ),
    )

    request = make_request(path="/v1/guilds")
    request.args = {"page": "1", "name": "storm", "server": "khyber"}

    response = run_async(guild_endpoints.get_all_guilds(request))

    assert response.status == 200
    payload = response_json(response)
    assert payload["total"] == 1
    assert payload["data"][0]["guild_name"] == "Stormwatch"


def test_get_guild_by_server_and_name_rejects_invalid_server(
    make_request, run_async, response_json
):
    request = make_request(path="/v1/guilds/notaserver/MyGuild")

    response = run_async(
        guild_endpoints.get_guild_by_server_name_and_guild_name(
            request, "notaserver", "MyGuild"
        )
    )

    assert response.status == 400
    assert response_json(response)["message"] == "Invalid server name."


def test_get_guild_by_server_and_name_returns_404_when_not_found(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        guild_endpoints.guild_utils, "validate_guild_name", lambda _name: True
    )
    monkeypatch.setattr(
        guild_endpoints.postgres_client,
        "async_get_guild_by_server_name_and_guild_name",
        _amock(lambda _server_name, _guild_name: None),
    )

    request = make_request(path="/v1/guilds/khyber/MyGuild")

    response = run_async(
        guild_endpoints.get_guild_by_server_name_and_guild_name(
            request, "khyber", "MyGuild"
        )
    )

    assert response.status == 404
    assert response_json(response)["data"] is None


def test_get_guild_by_server_and_name_returns_data_without_auth(
    monkeypatch, make_request, run_async, response_json
):
    guild_data = {"guild_name": "Stormwatch", "server_name": "Khyber"}

    monkeypatch.setattr(
        guild_endpoints.guild_utils, "validate_guild_name", lambda _name: True
    )
    monkeypatch.setattr(
        guild_endpoints.postgres_client,
        "async_get_guild_by_server_name_and_guild_name",
        _amock(lambda _server_name, _guild_name: dict(guild_data)),
    )
    monkeypatch.setattr(
        guild_endpoints.redis_client,
        "get_online_characters_by_server_and_guild_name_as_dict",
        lambda _server_name, _guild_name: {"online": [1, 2]},
    )

    request = make_request(path="/v1/guilds/khyber/Stormwatch")

    response = run_async(
        guild_endpoints.get_guild_by_server_name_and_guild_name(
            request, "khyber", "Stormwatch"
        )
    )

    assert response.status == 200
    payload = response_json(response)["data"]
    assert payload["guild_name"] == "Stormwatch"
    assert payload["online_characters"]["online"] == [1, 2]


def test_get_guild_by_server_and_name_returns_400_for_bad_member_query_params(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        guild_endpoints.guild_utils, "validate_guild_name", lambda _name: True
    )
    monkeypatch.setattr(
        guild_endpoints.postgres_client,
        "async_get_guild_by_server_name_and_guild_name",
        _amock(
            lambda _server_name, _guild_name: {
                "guild_name": "Stormwatch",
                "server_name": "Khyber",
            }
        ),
    )
    monkeypatch.setattr(
        guild_endpoints.redis_client,
        "get_online_characters_by_server_and_guild_name_as_dict",
        lambda _server_name, _guild_name: {},
    )

    request = make_request(
        path="/v1/guilds/khyber/Stormwatch",
        headers={"Authorization": "token"},
    )
    request.args = {"page": "1", "page_size": "999", "sort_by": "id"}

    response = run_async(
        guild_endpoints.get_guild_by_server_name_and_guild_name(
            request, "khyber", "Stormwatch"
        )
    )

    assert response.status == 400
    assert (
        response_json(response)["message"] == "Invalid page number, page size, or sort."
    )


def test_get_guild_by_server_and_name_hydrates_member_data_when_verified_member(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        guild_endpoints.guild_utils, "validate_guild_name", lambda _name: True
    )
    monkeypatch.setattr(
        guild_endpoints.postgres_client,
        "async_get_guild_by_server_name_and_guild_name",
        _amock(
            lambda _server_name, _guild_name: {
                "guild_name": "Stormwatch",
                "server_name": "Khyber",
            }
        ),
    )
    monkeypatch.setattr(
        guild_endpoints.redis_client,
        "get_online_characters_by_server_and_guild_name_as_dict",
        lambda _server_name, _guild_name: {"online": [10]},
    )
    monkeypatch.setattr(
        guild_endpoints.postgres_client,
        "async_get_character_id_by_access_token",
        _amock(lambda _token: 55),
    )
    monkeypatch.setattr(
        guild_endpoints.postgres_client,
        "async_get_character_by_id",
        _amock(
            lambda _character_id: SimpleNamespace(
                guild_name="Stormwatch", server_name="Khyber"
            )
        ),
    )
    monkeypatch.setattr(
        guild_endpoints.postgres_client,
        "async_get_character_ids_by_server_and_guild",
        _amock(lambda _server_name, _guild_name, _page, _page_size, _sort_by: [55, 56]),
    )

    request = make_request(
        path="/v1/guilds/khyber/Stormwatch",
        headers={"Authorization": "token"},
    )
    request.args = {"page": "2", "page_size": "25", "sort_by": "id"}

    response = run_async(
        guild_endpoints.get_guild_by_server_name_and_guild_name(
            request, "khyber", "Stormwatch"
        )
    )

    assert response.status == 200
    payload = response_json(response)["data"]
    assert payload["is_member"] is True
    assert payload["member_ids"] == [55, 56]
