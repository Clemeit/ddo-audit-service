from types import SimpleNamespace

import endpoints.auth as auth_endpoints
from conftest import _amock


def test_register_returns_400_when_json_body_missing(
    make_request, run_async, response_json
):
    request = make_request(method="POST", path="/v1/auth/register", json_body=None)

    response = run_async(auth_endpoints.register(request))

    assert response.status == 400
    assert response_json(response)["error"] == "Invalid or missing JSON body"


def test_register_returns_400_for_validation_errors(
    make_request, run_async, response_json
):
    request = make_request(
        method="POST",
        path="/v1/auth/register",
        json_body={"username": "bad!", "password": "p"},
    )

    response = run_async(auth_endpoints.register(request))

    assert response.status == 400
    assert "username" in response_json(response)["error"]


def test_register_returns_400_for_invalid_body_format(
    make_request, run_async, response_json
):
    request = make_request(
        method="POST",
        path="/v1/auth/register",
        json_body="not-a-mapping",
    )

    response = run_async(auth_endpoints.register(request))

    assert response.status == 400
    assert response_json(response)["error"] == "Invalid request body format"


def test_register_returns_generic_400_for_existing_username(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_register_user",
        _amock(
            lambda *args, **kwargs: (
                False,
                None,
                auth_endpoints.auth_service.AUTH_ERROR_USERNAME_EXISTS,
            )
        ),
    )
    request = make_request(
        method="POST",
        path="/v1/auth/register",
        json_body={"username": "Valid1", "password": "Password1!"},
    )

    response = run_async(auth_endpoints.register(request))

    assert response.status == 400
    assert response_json(response)["error"] == "Unable to register account"


def test_register_returns_500_for_internal_registration_failure(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_register_user",
        _amock(lambda *args, **kwargs: (False, None, "internal")),
    )
    request = make_request(
        method="POST",
        path="/v1/auth/register",
        json_body={"username": "Valid2", "password": "Password1!"},
    )

    response = run_async(auth_endpoints.register(request))

    assert response.status == 500
    assert response_json(response)["error"] == "Unable to register account"


def test_register_success_forwards_client_metadata(
    monkeypatch, make_request, run_async, response_json
):
    captured = {}

    async def _register_user(
        username, password, created_ip=None, created_user_agent=None
    ):
        captured.update(
            {
                "username": username,
                "password": password,
                "created_ip": created_ip,
                "created_user_agent": created_user_agent,
            }
        )
        return (
            True,
            {
                "access_token": "access",
                "refresh_token": "refresh",
                "token_type": "Bearer",
                "expires_in": 900,
                "refresh_expires_in": 2592000,
                "user": {
                    "id": 1,
                    "username": username,
                    "created_at": "2026-03-14T00:00:00+00:00",
                },
            },
            "",
        )

    monkeypatch.setattr(
        auth_endpoints.auth_service, "async_register_user", _register_user
    )

    request = make_request(
        method="POST",
        path="/v1/auth/register",
        headers={"x-real-ip": "203.0.113.2", "User-Agent": "pytest-agent"},
        json_body={"username": "Valid3", "password": "Password1!"},
    )

    response = run_async(auth_endpoints.register(request))

    assert response.status == 201
    data = response_json(response)["data"]
    assert data["access_token"] == "access"
    assert data["refresh_token"] == "refresh"
    assert captured["created_ip"] == "203.0.113.2"
    assert captured["created_user_agent"] == "pytest-agent"


def test_login_returns_400_when_json_body_missing(
    make_request, run_async, response_json
):
    request = make_request(method="POST", path="/v1/auth/login", json_body=None)

    response = run_async(auth_endpoints.login(request))

    assert response.status == 400
    assert response_json(response)["error"] == "Invalid or missing JSON body"


def test_login_returns_401_for_invalid_credentials(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_login_user",
        _amock(
            lambda *args, **kwargs: (
                False,
                None,
                auth_endpoints.auth_service.AUTH_ERROR_INVALID_CREDENTIALS,
            )
        ),
    )
    request = make_request(
        method="POST",
        path="/v1/auth/login",
        json_body={"username": "alice", "password": "bad"},
    )

    response = run_async(auth_endpoints.login(request))

    assert response.status == 401
    assert response_json(response)["error"] == "Invalid username or password"


def test_login_returns_500_for_internal_failure(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_login_user",
        _amock(lambda *args, **kwargs: (False, None, "internal")),
    )
    request = make_request(
        method="POST",
        path="/v1/auth/login",
        json_body={"username": "alice", "password": "Password1!"},
    )

    response = run_async(auth_endpoints.login(request))

    assert response.status == 500
    assert response_json(response)["error"] == "Unable to complete login"


def test_login_success_returns_200(monkeypatch, make_request, run_async, response_json):
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_login_user",
        _amock(
            lambda *args, **kwargs: (
                True,
                {
                    "access_token": "access",
                    "refresh_token": "refresh",
                    "token_type": "Bearer",
                    "expires_in": 900,
                    "refresh_expires_in": 2592000,
                    "user": {
                        "id": 7,
                        "username": "alice",
                        "created_at": "2026-03-14T00:00:00+00:00",
                    },
                },
                "",
            )
        ),
    )

    request = make_request(
        method="POST",
        path="/v1/auth/login",
        json_body={"username": "alice", "password": "Password1!"},
    )

    response = run_async(auth_endpoints.login(request))

    assert response.status == 200
    assert response_json(response)["data"]["access_token"] == "access"


def test_refresh_returns_400_when_json_body_missing(
    make_request, run_async, response_json
):
    request = make_request(method="POST", path="/v1/auth/refresh", json_body=None)

    response = run_async(auth_endpoints.refresh(request))

    assert response.status == 400
    assert response_json(response)["error"] == "Invalid or missing JSON body"


def test_refresh_returns_401_for_invalid_refresh_token(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_refresh_session",
        _amock(
            lambda *args, **kwargs: (
                False,
                None,
                auth_endpoints.auth_service.AUTH_ERROR_INVALID_REFRESH_TOKEN,
            )
        ),
    )
    request = make_request(
        method="POST",
        path="/v1/auth/refresh",
        json_body={"refresh_token": "bad"},
    )

    response = run_async(auth_endpoints.refresh(request))

    assert response.status == 401
    assert response_json(response)["error"] == "Invalid refresh token"


def test_refresh_returns_500_for_internal_failure(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_refresh_session",
        _amock(lambda *args, **kwargs: (False, None, "internal")),
    )
    request = make_request(
        method="POST",
        path="/v1/auth/refresh",
        json_body={"refresh_token": "any-token"},
    )

    response = run_async(auth_endpoints.refresh(request))

    assert response.status == 500
    assert response_json(response)["error"] == "Unable to refresh session"


def test_refresh_success_returns_200(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_refresh_session",
        _amock(
            lambda *args, **kwargs: (
                True,
                {
                    "access_token": "access-new",
                    "refresh_token": "refresh-new",
                    "token_type": "Bearer",
                    "expires_in": 900,
                    "refresh_expires_in": 2592000,
                },
                "",
            )
        ),
    )
    request = make_request(
        method="POST",
        path="/v1/auth/refresh",
        json_body={"refresh_token": "refresh-old"},
    )

    response = run_async(auth_endpoints.refresh(request))

    assert response.status == 200
    assert response_json(response)["data"]["refresh_token"] == "refresh-new"


def test_logout_returns_401_without_session_id(make_request, run_async, response_json):
    request = make_request(method="POST", path="/v1/auth/logout", json_body={})

    response = run_async(auth_endpoints.logout(request))

    assert response.status == 401
    assert response_json(response)["error"] == "Unauthorized"


def test_logout_returns_500_when_session_revoke_fails(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_logout_session",
        _amock(lambda session_id: False),
    )
    request = make_request(
        method="POST",
        path="/v1/auth/logout",
        json_body={},
        ctx=SimpleNamespace(session_id="session-fail"),
    )

    response = run_async(auth_endpoints.logout(request))

    assert response.status == 500
    assert response_json(response)["error"] == "Failed to log out"


def test_logout_success_returns_200(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_logout_session",
        _amock(lambda session_id: True),
    )
    request = make_request(
        method="POST",
        path="/v1/auth/logout",
        json_body={},
        ctx=SimpleNamespace(session_id="session-ok"),
    )

    response = run_async(auth_endpoints.logout(request))

    assert response.status == 200
    assert response_json(response)["data"]["message"] == "Logged out successfully"


def test_delete_account_returns_401_without_user_id(
    make_request, run_async, response_json
):
    request = make_request(method="DELETE", path="/v1/auth/account", json_body={})

    response = run_async(auth_endpoints.delete_account(request))

    assert response.status == 401
    assert response_json(response)["error"] == "Unauthorized"


def test_delete_account_returns_404_when_user_missing(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_delete_user_account",
        _amock(
            lambda user_id: (
                False,
                auth_endpoints.auth_service.AUTH_ERROR_USER_NOT_FOUND,
            )
        ),
    )
    request = make_request(
        method="DELETE",
        path="/v1/auth/account",
        json_body={},
        ctx=SimpleNamespace(user_id=42),
    )

    response = run_async(auth_endpoints.delete_account(request))

    assert response.status == 404
    assert response_json(response)["error"] == "User not found"


def test_delete_account_returns_500_on_internal_error(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_delete_user_account",
        _amock(
            lambda user_id: (False, auth_endpoints.auth_service.AUTH_ERROR_INTERNAL)
        ),
    )
    request = make_request(
        method="DELETE",
        path="/v1/auth/account",
        json_body={},
        ctx=SimpleNamespace(user_id=42),
    )

    response = run_async(auth_endpoints.delete_account(request))

    assert response.status == 500
    assert response_json(response)["error"] == "Failed to delete account"


def test_delete_account_returns_200_when_successful(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_delete_user_account",
        _amock(lambda user_id: (True, "")),
    )
    request = make_request(
        method="DELETE",
        path="/v1/auth/account",
        json_body={},
        ctx=SimpleNamespace(user_id=42),
    )

    response = run_async(auth_endpoints.delete_account(request))

    assert response.status == 200
    assert response_json(response)["data"]["message"] == "Account deleted successfully"
