from types import SimpleNamespace

import endpoints.user as user_endpoints
from conftest import _amock


def test_get_user_profile_requires_authenticated_user_id(
    make_request, run_async, response_json
):
    request = make_request(method="GET", path="/v1/user/profile")

    response = run_async(user_endpoints.get_user_profile(request))

    assert response.status == 401
    assert response_json(response)["error"] == "Unauthorized"


def test_get_user_profile_returns_404_when_user_missing(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        user_endpoints.auth_service,
        "async_get_user_by_id",
        _amock(lambda user_id: None),
    )
    request = make_request(
        method="GET",
        path="/v1/user/profile",
        ctx=SimpleNamespace(user_id=1),
    )

    response = run_async(user_endpoints.get_user_profile(request))

    assert response.status == 404
    assert response_json(response)["error"] == "User not found"


def test_get_user_profile_returns_200_for_authenticated_user(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        user_endpoints.auth_service,
        "async_get_user_by_id",
        _amock(
            lambda user_id: {
                "id": user_id,
                "username": "profile-user",
                "created_at": "2026-03-14T00:00:00+00:00",
            }
        ),
    )
    request = make_request(
        method="GET",
        path="/v1/user/profile",
        ctx=SimpleNamespace(user_id=2),
    )

    response = run_async(user_endpoints.get_user_profile(request))

    assert response.status == 200
    assert response_json(response)["data"]["username"] == "profile-user"


def test_change_user_password_requires_auth_context(
    make_request, run_async, response_json
):
    request = make_request(
        method="PUT",
        path="/v1/user/profile/password",
        json_body={"old_password": "old", "new_password": "NewPassword1!"},
    )

    response = run_async(user_endpoints.change_user_password(request))

    assert response.status == 401
    assert response_json(response)["error"] == "Unauthorized"


def test_change_user_password_returns_400_for_validation_errors(
    make_request, run_async, response_json
):
    request = make_request(
        method="PUT",
        path="/v1/user/profile/password",
        json_body={"old_password": ""},
        ctx=SimpleNamespace(user_id=7, username="user7"),
    )

    response = run_async(user_endpoints.change_user_password(request))

    assert response.status == 400
    assert "new_password" in response_json(response)["error"]


def test_change_user_password_returns_400_for_domain_error(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        user_endpoints.auth_service,
        "async_change_password",
        _amock(lambda *args, **kwargs: (False, None, "Current password is incorrect")),
    )
    request = make_request(
        method="PUT",
        path="/v1/user/profile/password",
        json_body={"old_password": "old", "new_password": "NewPassword1!"},
        ctx=SimpleNamespace(user_id=7, username="user7"),
    )

    response = run_async(user_endpoints.change_user_password(request))

    assert response.status == 400
    assert response_json(response)["error"] == "Current password is incorrect"


def test_change_user_password_success_returns_new_tokens(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        user_endpoints.auth_service,
        "async_change_password",
        _amock(
            lambda *args, **kwargs: (
                True,
                {
                    "message": "Password changed successfully",
                    "access_token": "new-access",
                    "refresh_token": "new-refresh",
                    "token_type": "Bearer",
                    "expires_in": 900,
                    "refresh_expires_in": 2592000,
                },
                "",
            )
        ),
    )
    request = make_request(
        method="PUT",
        path="/v1/user/profile/password",
        headers={"x-real-ip": "203.0.113.9", "User-Agent": "pytest-agent"},
        json_body={"old_password": "old", "new_password": "NewPassword1!"},
        ctx=SimpleNamespace(user_id=7, username="user7"),
    )

    response = run_async(user_endpoints.change_user_password(request))

    assert response.status == 200
    data = response_json(response)["data"]
    assert data["message"] == "Password changed successfully"
    assert data["access_token"] == "new-access"


def test_get_persistent_settings_requires_authenticated_user_id(
    make_request, run_async, response_json
):
    request = make_request(method="GET", path="/v1/user/settings/persistent")

    response = run_async(user_endpoints.get_persistent_settings(request))

    assert response.status == 401
    assert response_json(response)["error"] == "Unauthorized"


def test_get_persistent_settings_returns_404_when_missing(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        user_endpoints.postgres_client,
        "async_get_user_settings",
        _amock(lambda user_id: None),
    )
    request = make_request(
        method="GET",
        path="/v1/user/settings/persistent",
        ctx=SimpleNamespace(user_id=14),
    )

    response = run_async(user_endpoints.get_persistent_settings(request))

    assert response.status == 404
    assert response_json(response)["error"] == "Settings not found"


def test_get_persistent_settings_returns_200(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        user_endpoints.postgres_client,
        "async_get_user_settings",
        _amock(lambda user_id: {"settings": {"theme": "light", "compact": True}}),
    )
    request = make_request(
        method="GET",
        path="/v1/user/settings/persistent",
        ctx=SimpleNamespace(user_id=14),
    )

    response = run_async(user_endpoints.get_persistent_settings(request))

    assert response.status == 200
    assert response_json(response)["data"]["settings"]["theme"] == "light"


def test_update_persistent_settings_requires_authenticated_user_id(
    make_request, run_async, response_json
):
    request = make_request(
        method="PUT",
        path="/v1/user/settings/persistent",
        json_body={"settings": {}},
    )

    response = run_async(user_endpoints.update_persistent_settings(request))

    assert response.status == 401
    assert response_json(response)["error"] == "Unauthorized"


def test_update_persistent_settings_returns_400_when_settings_missing(
    make_request, run_async, response_json
):
    request = make_request(
        method="PUT",
        path="/v1/user/settings/persistent",
        json_body={"something_else": {}},
        ctx=SimpleNamespace(user_id=9),
    )

    response = run_async(user_endpoints.update_persistent_settings(request))

    assert response.status == 400
    assert "settings" in response_json(response)["error"]


def test_update_persistent_settings_returns_400_when_settings_not_object(
    make_request, run_async, response_json
):
    request = make_request(
        method="PUT",
        path="/v1/user/settings/persistent",
        json_body={"settings": ["not", "a", "dict"]},
        ctx=SimpleNamespace(user_id=9),
    )

    response = run_async(user_endpoints.update_persistent_settings(request))

    assert response.status == 400
    assert response_json(response)["error"] == "Settings must be a JSON object"


def test_update_persistent_settings_returns_413_for_large_payload(
    make_request, run_async, response_json
):
    large_value = "x" * (1024 * 1024 + 16)
    request = make_request(
        method="PUT",
        path="/v1/user/settings/persistent",
        json_body={"settings": {"blob": large_value}},
        ctx=SimpleNamespace(user_id=9),
    )

    response = run_async(user_endpoints.update_persistent_settings(request))

    assert response.status == 413
    assert response_json(response)["error"] == "Settings too large"


def test_update_persistent_settings_returns_500_when_update_fails(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        user_endpoints.postgres_client,
        "async_update_user_settings",
        _amock(lambda user_id, settings: False),
    )
    request = make_request(
        method="PUT",
        path="/v1/user/settings/persistent",
        json_body={"settings": {"theme": "dark"}},
        ctx=SimpleNamespace(user_id=9),
    )

    response = run_async(user_endpoints.update_persistent_settings(request))

    assert response.status == 500
    assert response_json(response)["error"] == "Failed to update settings"


def test_update_persistent_settings_returns_200_when_successful(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        user_endpoints.postgres_client,
        "async_update_user_settings",
        _amock(lambda user_id, settings: True),
    )
    request = make_request(
        method="PUT",
        path="/v1/user/settings/persistent",
        json_body={"settings": {"theme": "dark", "showTips": False}},
        ctx=SimpleNamespace(user_id=9),
    )

    response = run_async(user_endpoints.update_persistent_settings(request))

    assert response.status == 200
    assert response_json(response)["data"]["settings"]["theme"] == "dark"


def test_patch_persistent_settings_requires_authenticated_user_id(
    make_request, run_async, response_json
):
    request = make_request(
        method="PATCH",
        path="/v1/user/settings/persistent",
        json_body={"settings": {}},
    )

    response = run_async(user_endpoints.patch_persistent_settings(request))

    assert response.status == 401
    assert response_json(response)["error"] == "Unauthorized"


def test_patch_persistent_settings_returns_400_when_settings_missing(
    make_request, run_async, response_json
):
    request = make_request(
        method="PATCH",
        path="/v1/user/settings/persistent",
        json_body={"something_else": {}},
        ctx=SimpleNamespace(user_id=9),
    )

    response = run_async(user_endpoints.patch_persistent_settings(request))

    assert response.status == 400
    assert "settings" in response_json(response)["error"]


def test_patch_persistent_settings_returns_400_when_settings_not_object(
    make_request, run_async, response_json
):
    request = make_request(
        method="PATCH",
        path="/v1/user/settings/persistent",
        json_body={"settings": ["not", "a", "dict"]},
        ctx=SimpleNamespace(user_id=9),
    )

    response = run_async(user_endpoints.patch_persistent_settings(request))

    assert response.status == 400
    assert response_json(response)["error"] == "Settings must be a JSON object"


def test_patch_persistent_settings_returns_413_for_large_payload(
    make_request, run_async, response_json
):
    large_value = "x" * (1024 * 1024 + 16)
    request = make_request(
        method="PATCH",
        path="/v1/user/settings/persistent",
        json_body={"settings": {"blob": large_value}},
        ctx=SimpleNamespace(user_id=9),
    )

    response = run_async(user_endpoints.patch_persistent_settings(request))

    assert response.status == 413
    assert response_json(response)["error"] == "Settings too large"


def test_patch_persistent_settings_returns_500_when_patch_fails(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        user_endpoints.postgres_client,
        "async_patch_user_settings",
        _amock(lambda user_id, settings_patch: None),
    )
    request = make_request(
        method="PATCH",
        path="/v1/user/settings/persistent",
        json_body={"settings": {"theme": "dark"}},
        ctx=SimpleNamespace(user_id=9),
    )

    response = run_async(user_endpoints.patch_persistent_settings(request))

    assert response.status == 500
    assert response_json(response)["error"] == "Failed to patch settings"


def test_patch_persistent_settings_returns_200_when_successful(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        user_endpoints.postgres_client,
        "async_patch_user_settings",
        _amock(lambda user_id, settings_patch: {"theme": "dark", "compact": True}),
    )
    request = make_request(
        method="PATCH",
        path="/v1/user/settings/persistent",
        json_body={"settings": {"theme": "dark"}},
        ctx=SimpleNamespace(user_id=9),
    )

    response = run_async(user_endpoints.patch_persistent_settings(request))

    assert response.status == 200
    assert response_json(response)["data"]["settings"]["theme"] == "dark"
    assert response_json(response)["data"]["settings"]["compact"] is True


def test_delete_persistent_settings_requires_authenticated_user_id(
    make_request, run_async, response_json
):
    request = make_request(method="DELETE", path="/v1/user/settings/persistent")

    response = run_async(user_endpoints.delete_persistent_settings(request))

    assert response.status == 401
    assert response_json(response)["error"] == "Unauthorized"


def test_delete_persistent_settings_returns_404_when_missing(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        user_endpoints.postgres_client,
        "async_delete_user_settings",
        _amock(lambda user_id: False),
    )
    request = make_request(
        method="DELETE",
        path="/v1/user/settings/persistent",
        ctx=SimpleNamespace(user_id=9),
    )

    response = run_async(user_endpoints.delete_persistent_settings(request))

    assert response.status == 404
    assert response_json(response)["error"] == "Settings not found"


def test_delete_persistent_settings_returns_500_when_delete_fails(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        user_endpoints.postgres_client,
        "async_delete_user_settings",
        _amock(lambda user_id: None),
    )
    request = make_request(
        method="DELETE",
        path="/v1/user/settings/persistent",
        ctx=SimpleNamespace(user_id=9),
    )

    response = run_async(user_endpoints.delete_persistent_settings(request))

    assert response.status == 500
    assert response_json(response)["error"] == "Failed to delete settings"


def test_delete_persistent_settings_returns_200_when_successful(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        user_endpoints.postgres_client,
        "async_delete_user_settings",
        _amock(lambda user_id: True),
    )
    request = make_request(
        method="DELETE",
        path="/v1/user/settings/persistent",
        ctx=SimpleNamespace(user_id=9),
    )

    response = run_async(user_endpoints.delete_persistent_settings(request))

    assert response.status == 200
    assert response_json(response)["data"]["deleted"] is True
