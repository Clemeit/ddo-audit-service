from types import SimpleNamespace

import endpoints.auth as auth_endpoints
import utils.auth_cookies as auth_endpoints_cookies
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


def test_register_returns_409_for_existing_username(
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

    assert response.status == 409
    assert response_json(response)["error"] == "Username is already taken"


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
    # Refresh token must NOT appear in the JSON body — it is delivered as a cookie.
    assert "refresh_token" not in data
    assert "refresh_expires_in" not in data
    assert "refresh_token" in response.cookies
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
    data = response_json(response)["data"]
    assert data["access_token"] == "access"
    # Refresh token must NOT appear in the JSON body — it is delivered as a cookie.
    assert "refresh_token" not in data
    assert "refresh_expires_in" not in data
    assert "refresh_token" in response.cookies


def test_refresh_returns_401_when_cookie_missing(
    make_request, run_async, response_json
):
    """Refresh endpoint requires the refresh token cookie; missing → 401."""
    request = make_request(method="POST", path="/v1/auth/refresh")

    response = run_async(auth_endpoints.refresh(request))

    assert response.status == 401
    assert response_json(response)["error"] == "Invalid refresh token"
    # Cookie should be cleared (expired) in the response.
    assert "refresh_token" in response.cookies


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
        cookies={"refresh_token": "bad"},
    )

    response = run_async(auth_endpoints.refresh(request))

    assert response.status == 401
    assert response_json(response)["error"] == "Invalid refresh token"
    # Cookie should be cleared (expired) on invalid token.
    assert "refresh_token" in response.cookies


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
        cookies={"refresh_token": "any-token"},
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
        cookies={"refresh_token": "refresh-old"},
    )

    response = run_async(auth_endpoints.refresh(request))

    assert response.status == 200
    data = response_json(response)["data"]
    assert data["access_token"] == "access-new"
    # Refresh token must NOT appear in the JSON body — it is rotated via cookie.
    assert "refresh_token" not in data
    assert "refresh_expires_in" not in data
    assert "refresh_token" in response.cookies


def _cookies(response):
    """{cookie name: (value, max-age)} for every Set-Cookie on the response."""
    try:
        cookies = response.cookies.cookies
    except KeyError:  # Sanic raises when the response sets no cookies at all
        return {}
    return {cookie.key: (cookie.value, cookie.get("max-age")) for cookie in cookies}


def _assert_refresh_cookie_cleared(response):
    cookies = _cookies(response)
    assert cookies[auth_endpoints_cookies.REFRESH_COOKIE_NAME][1] == 0
    assert cookies[auth_endpoints_cookies.LEGACY_REFRESH_COOKIE_NAME][1] == 0


def test_refresh_401_includes_reason_code(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_refresh_session",
        _amock(
            lambda *args, **kwargs: (
                False,
                {"reason": auth_endpoints.auth_service.REFRESH_FAILURE_EXPIRED},
                auth_endpoints.auth_service.AUTH_ERROR_INVALID_REFRESH_TOKEN,
            )
        ),
    )
    request = make_request(
        method="POST", path="/v1/auth/refresh", cookies={"refresh_token": "old"}
    )

    response = run_async(auth_endpoints.refresh(request))

    assert response.status == 401
    assert response_json(response)["code"] == "session_expired"
    _assert_refresh_cookie_cleared(response)


def test_refresh_grace_response_leaves_cookie_alone(
    monkeypatch, make_request, run_async, response_json
):
    """A just-rotated-out token gets an access token but no Set-Cookie, so the newer
    cookie another tab just received isn't overwritten or cleared."""
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_refresh_session",
        _amock(
            lambda *args, **kwargs: (
                True,
                {"access_token": "access-grace", "token_type": "Bearer", "expires_in": 900},
                "",
            )
        ),
    )
    request = make_request(
        method="POST", path="/v1/auth/refresh", cookies={"refresh_token": "prev"}
    )

    response = run_async(auth_endpoints.refresh(request))

    assert response.status == 200
    assert response_json(response)["data"]["access_token"] == "access-grace"
    assert _cookies(response) == {}


def test_refresh_sets_cookie_with_remaining_lifetime(
    monkeypatch, make_request, run_async
):
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_refresh_session",
        _amock(
            lambda *args, **kwargs: (
                True,
                {
                    "access_token": "a",
                    "refresh_token": "rotated",
                    "refresh_expires_in": 1234,
                },
                "",
            )
        ),
    )
    request = make_request(
        method="POST", path="/v1/auth/refresh", cookies={"refresh_token": "old"}
    )

    response = run_async(auth_endpoints.refresh(request))

    cookies = _cookies(response)
    assert cookies[auth_endpoints_cookies.REFRESH_COOKIE_NAME] == ("rotated", 1234)


def test_refresh_accepts_prefixed_cookie_name(monkeypatch, make_request, run_async):
    seen = []
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_refresh_session",
        _amock(lambda token: seen.append(token) or (False, None, "internal")),
    )
    request = make_request(
        method="POST",
        path="/v1/auth/refresh",
        cookies={
            auth_endpoints_cookies.REFRESH_COOKIE_NAME: "new-name",
            "refresh_token": "legacy",
        },
    )

    run_async(auth_endpoints.refresh(request))

    assert seen == ["new-name"]


def test_refresh_rejects_foreign_origin(monkeypatch, make_request, run_async):
    called = []
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_refresh_session",
        _amock(lambda token: called.append(token)),
    )
    request = make_request(
        method="POST",
        path="/v1/auth/refresh",
        headers={"Origin": "https://evil.example"},
        cookies={"refresh_token": "tok"},
    )

    response = run_async(auth_endpoints.refresh(request))

    assert response.status == 403
    assert called == []
    # A 403 must not clear the cookie: it says nothing about the session.
    assert _cookies(response) == {}


def test_refresh_allows_known_origin(monkeypatch, make_request, run_async):
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_refresh_session",
        _amock(lambda token: (False, None, "internal")),
    )
    request = make_request(
        method="POST",
        path="/v1/auth/refresh",
        headers={"Origin": "https://www.ddoaudit.com"},
        cookies={"refresh_token": "tok"},
    )

    response = run_async(auth_endpoints.refresh(request))

    assert response.status == 500


def test_logout_with_cookie_only_revokes_and_clears(
    monkeypatch, make_request, run_async, response_json
):
    revoked = []
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_logout_by_refresh_token",
        _amock(lambda token: revoked.append(token) or True),
    )
    request = make_request(
        method="POST", path="/v1/auth/logout", cookies={"refresh_token": "tok"}
    )

    response = run_async(auth_endpoints.logout(request))

    assert response.status == 200
    assert response_json(response)["data"]["message"] == "Logged out successfully"
    assert revoked == ["tok"]
    _assert_refresh_cookie_cleared(response)


def test_logout_falls_back_to_bearer_when_cookie_unknown(
    monkeypatch, make_request, run_async
):
    revoked_sessions = []
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_logout_by_refresh_token",
        _amock(lambda token: False),
    )
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_validate_access_token",
        _amock(lambda token: {"session_id": "sess-9"} if token == "acc" else None),
    )
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_logout_session",
        _amock(lambda session_id: revoked_sessions.append(session_id) or True),
    )
    request = make_request(
        method="POST",
        path="/v1/auth/logout",
        headers={"Authorization": "Bearer acc"},
        cookies={"refresh_token": "unknown"},
    )

    response = run_async(auth_endpoints.logout(request))

    assert response.status == 200
    assert revoked_sessions == ["sess-9"]


def test_logout_without_credentials_still_succeeds(
    monkeypatch, make_request, run_async
):
    """Expired access token and no cookie: nothing to revoke, but the browser is
    told to drop any cookie and the client can finish logging out."""
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_validate_access_token",
        _amock(lambda token: None),
    )
    request = make_request(
        method="POST",
        path="/v1/auth/logout",
        headers={"Authorization": "Bearer expired"},
    )

    response = run_async(auth_endpoints.logout(request))

    assert response.status == 200
    _assert_refresh_cookie_cleared(response)


def test_logout_succeeds_when_revoke_errors(monkeypatch, make_request, run_async):
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_logout_by_refresh_token",
        _amock(lambda token: None),
    )
    request = make_request(
        method="POST", path="/v1/auth/logout", cookies={"refresh_token": "tok"}
    )

    response = run_async(auth_endpoints.logout(request))

    assert response.status == 200
    _assert_refresh_cookie_cleared(response)


def test_logout_rejects_foreign_origin(monkeypatch, make_request, run_async):
    called = []
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_logout_by_refresh_token",
        _amock(lambda token: called.append(token)),
    )
    request = make_request(
        method="POST",
        path="/v1/auth/logout",
        headers={"Origin": "https://evil.example"},
        cookies={"refresh_token": "tok"},
    )

    response = run_async(auth_endpoints.logout(request))

    assert response.status == 403
    assert called == []


def test_delete_account_passes_password_through(monkeypatch, make_request, run_async):
    captured = {}

    def _delete(user_id, **kwargs):
        captured.update(kwargs, user_id=user_id)
        return True, ""

    monkeypatch.setattr(
        auth_endpoints.auth_service, "async_delete_user_account", _amock(_delete)
    )
    request = make_request(
        method="DELETE",
        path="/v1/auth/account",
        json_body={"password": "Secret1!"},
        ctx=SimpleNamespace(user_id=42),
    )

    response = run_async(auth_endpoints.delete_account(request))

    assert response.status == 200
    assert captured["user_id"] == 42
    assert captured["password"] == "Secret1!"


def test_delete_account_wrong_password_is_400_not_401(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_delete_user_account",
        _amock(
            lambda user_id, **kwargs: (
                False,
                auth_endpoints.auth_service.AUTH_ERROR_INVALID_CREDENTIALS,
            )
        ),
    )
    request = make_request(
        method="DELETE",
        path="/v1/auth/account",
        json_body={"password": "wrong"},
        ctx=SimpleNamespace(user_id=42),
    )

    response = run_async(auth_endpoints.delete_account(request))

    assert response.status == 400
    assert response_json(response)["error"] == "Password is incorrect"
    assert _cookies(response) == {}


def test_delete_account_password_required_when_enforced(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(auth_endpoints, "ACCOUNT_DELETE_REQUIRE_PASSWORD", True)
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_delete_user_account",
        _amock(
            lambda user_id, password=None, require_password=False: (
                (False, auth_endpoints.auth_service.AUTH_ERROR_PASSWORD_REQUIRED)
                if require_password and password is None
                else (True, "")
            )
        ),
    )
    request = make_request(
        method="DELETE",
        path="/v1/auth/account",
        json_body=None,
        ctx=SimpleNamespace(user_id=42),
    )

    response = run_async(auth_endpoints.delete_account(request))

    assert response.status == 400
    assert "password" in response_json(response)["error"].lower()


def test_login_refuses_throttled_username_before_checking_password(
    monkeypatch, make_request, run_async, response_json
):
    called = []
    monkeypatch.setattr(
        auth_endpoints,
        "async_get_login_failure_retry_after",
        _amock(lambda username: 300),
    )
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_login_user",
        _amock(lambda *args, **kwargs: called.append(args)),
    )
    request = make_request(
        method="POST",
        path="/v1/auth/login",
        json_body={"username": "alice", "password": "pw"},
    )

    response = run_async(auth_endpoints.login(request))

    assert response.status == 429
    assert response.headers["Retry-After"] == "300"
    assert called == []


def test_login_records_failure_and_clears_on_success(
    monkeypatch, make_request, run_async
):
    events = []
    monkeypatch.setattr(
        auth_endpoints, "async_get_login_failure_retry_after", _amock(lambda u: None)
    )
    monkeypatch.setattr(
        auth_endpoints,
        "async_record_login_failure",
        _amock(lambda u: events.append(("fail", u))),
    )
    monkeypatch.setattr(
        auth_endpoints,
        "async_clear_login_failures",
        _amock(lambda u: events.append(("clear", u))),
    )
    results = iter(
        [
            (False, None, auth_endpoints.auth_service.AUTH_ERROR_INVALID_CREDENTIALS),
            (True, {"access_token": "a", "refresh_token": "r"}, ""),
        ]
    )
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_login_user",
        _amock(lambda *args, **kwargs: next(results)),
    )

    for _ in range(2):
        run_async(
            auth_endpoints.login(
                make_request(
                    method="POST",
                    path="/v1/auth/login",
                    json_body={"username": "alice", "password": "pw"},
                )
            )
        )

    assert events == [("fail", "alice"), ("clear", "alice")]


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
            lambda user_id, **kwargs: (
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
            lambda user_id, **kwargs: (
                False,
                auth_endpoints.auth_service.AUTH_ERROR_INTERNAL,
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

    assert response.status == 500
    assert response_json(response)["error"] == "Failed to delete account"


def test_delete_account_returns_200_when_successful(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        auth_endpoints.auth_service,
        "async_delete_user_account",
        _amock(lambda user_id, **kwargs: (True, "")),
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
    # Refresh cookie should be cleared (expired Set-Cookie) in the response.
    assert "refresh_token" in response.cookies
