import app as app_module
import middleware.jwt as jwt_middleware_module
import middleware.rate_limit as rate_limit_module
from conftest import _amock


def test_jwt_middleware_skips_unprotected_routes(monkeypatch, make_request, run_async):
    monkeypatch.setattr(
        jwt_middleware_module, "is_jwt_protected", lambda request: False
    )
    request = make_request(method="GET", path="/v1/game/info")

    response = run_async(jwt_middleware_module.jwt_middleware(request))

    assert response is None


def test_jwt_middleware_rejects_missing_authorization(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(jwt_middleware_module, "is_jwt_protected", lambda request: True)
    request = make_request(method="GET", path="/v1/user/profile")

    response = run_async(jwt_middleware_module.jwt_middleware(request))

    assert response.status == 401
    assert response_json(response)["error"] == "Unauthorized"


def test_jwt_middleware_rejects_invalid_bearer_format(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(jwt_middleware_module, "is_jwt_protected", lambda request: True)
    request = make_request(
        method="GET",
        path="/v1/user/profile",
        headers={"Authorization": "Token abc"},
    )

    response = run_async(jwt_middleware_module.jwt_middleware(request))

    assert response.status == 401
    assert response_json(response)["error"] == "Unauthorized"


def test_jwt_middleware_rejects_invalid_token_payload(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(jwt_middleware_module, "is_jwt_protected", lambda request: True)

    async def _mock_async_validate(token):
        return None

    monkeypatch.setattr(
        jwt_middleware_module.auth_service,
        "async_validate_access_token",
        _mock_async_validate,
    )
    request = make_request(
        method="GET",
        path="/v1/user/profile",
        headers={"Authorization": "Bearer invalid"},
    )

    response = run_async(jwt_middleware_module.jwt_middleware(request))

    assert response.status == 401
    assert response_json(response)["error"] == "Unauthorized"


def test_jwt_middleware_attaches_context_for_valid_token(
    monkeypatch, make_request, run_async
):
    monkeypatch.setattr(jwt_middleware_module, "is_jwt_protected", lambda request: True)

    async def _mock_async_validate(token):
        return {
            "user_id": 88,
            "username": "user88",
            "session_id": "session-88",
            "auth_version": 6,
        }

    monkeypatch.setattr(
        jwt_middleware_module.auth_service,
        "async_validate_access_token",
        _mock_async_validate,
    )
    request = make_request(
        method="GET",
        path="/v1/user/profile",
        headers={"Authorization": "Bearer valid"},
    )

    response = run_async(jwt_middleware_module.jwt_middleware(request))

    assert response is None
    assert request.ctx.user_id == 88
    assert request.ctx.username == "user88"
    assert request.ctx.session_id == "session-88"
    assert request.ctx.auth_version == 6


def test_jwt_middleware_rejects_payload_missing_session_id(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(jwt_middleware_module, "is_jwt_protected", lambda request: True)

    async def _mock_async_validate(token):
        return {
            "user_id": 88,
            "username": "user88",
            "session_id": None,
            "auth_version": 6,
        }

    monkeypatch.setattr(
        jwt_middleware_module.auth_service,
        "async_validate_access_token",
        _mock_async_validate,
    )
    request = make_request(
        method="GET",
        path="/v1/user/profile",
        headers={"Authorization": "Bearer valid"},
    )

    response = run_async(jwt_middleware_module.jwt_middleware(request))

    assert response.status == 401
    assert response_json(response)["error"] == "Unauthorized"


def test_rate_limit_middleware_allows_auth_request_under_limit(
    monkeypatch, make_request, run_async
):
    monkeypatch.setattr(
        rate_limit_module, "get_client_ip", lambda request: "198.51.100.5"
    )

    async def _mock_async_check(*args, **kwargs):
        return (True, 120)

    monkeypatch.setattr(
        rate_limit_module,
        "_async_increment_and_check_limit",
        _mock_async_check,
    )
    request = make_request(method="POST", path="/v1/auth/login")

    response = run_async(rate_limit_module.rate_limit_middleware(request))

    assert response is None


def test_rate_limit_middleware_blocks_auth_request_over_limit(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        rate_limit_module, "get_client_ip", lambda request: "198.51.100.5"
    )

    async def _mock_async_check(*args, **kwargs):
        return (False, 55)

    monkeypatch.setattr(
        rate_limit_module,
        "_async_increment_and_check_limit",
        _mock_async_check,
    )
    request = make_request(method="POST", path="/v1/auth/login")

    response = run_async(rate_limit_module.rate_limit_middleware(request))

    assert response.status == 429
    assert response_json(response)["retry_after"] == 55
    assert response.headers["Retry-After"] == "55"


def test_rate_limit_middleware_blocks_user_request_over_limit(
    monkeypatch, make_request, run_async, response_json
):

    async def _mock_async_check(*args, **kwargs):
        return (False, 88)

    monkeypatch.setattr(
        rate_limit_module,
        "_async_increment_and_check_limit",
        _mock_async_check,
    )
    request = make_request(
        method="PUT",
        path="/v1/user/profile/password",
        ctx=type("Ctx", (), {"user_id": 12})(),
    )

    response = run_async(rate_limit_module.rate_limit_middleware(request))

    assert response.status == 429
    assert response_json(response)["retry_after"] == 88
    assert response.headers["Retry-After"] == "88"


def test_rate_limit_middleware_fail_open_on_errors(
    monkeypatch, make_request, run_async
):
    async def _raise_error(*args, **kwargs):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(
        rate_limit_module, "get_client_ip", lambda request: "198.51.100.5"
    )
    monkeypatch.setattr(
        rate_limit_module, "_async_increment_and_check_limit", _raise_error
    )
    request = make_request(method="POST", path="/v1/auth/refresh")

    response = run_async(rate_limit_module.rate_limit_middleware(request))

    assert response is None


def test_check_api_key_skips_open_methods(monkeypatch, make_request, run_async):
    monkeypatch.setattr(app_module, "is_method_open", lambda request: True)
    monkeypatch.setattr(app_module, "is_route_open", lambda request: False)
    monkeypatch.setattr(app_module, "is_jwt_protected", lambda request: False)
    request = make_request(method="GET", path="/v1/game/info")

    response = run_async(app_module.check_api_key(request))

    assert response is None


def test_check_api_key_skips_open_routes(monkeypatch, make_request, run_async):
    monkeypatch.setattr(app_module, "is_method_open", lambda request: False)
    monkeypatch.setattr(app_module, "is_route_open", lambda request: True)
    monkeypatch.setattr(app_module, "is_jwt_protected", lambda request: False)
    request = make_request(method="POST", path="/v1/auth/login")

    response = run_async(app_module.check_api_key(request))

    assert response is None


def test_check_api_key_skips_jwt_protected_routes(monkeypatch, make_request, run_async):
    monkeypatch.setattr(app_module, "is_method_open", lambda request: False)
    monkeypatch.setattr(app_module, "is_route_open", lambda request: False)
    monkeypatch.setattr(app_module, "is_jwt_protected", lambda request: True)
    request = make_request(method="POST", path="/v1/auth/logout")

    response = run_async(app_module.check_api_key(request))

    assert response is None


def test_check_api_key_requires_authorization_header(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(app_module, "is_method_open", lambda request: False)
    monkeypatch.setattr(app_module, "is_route_open", lambda request: False)
    monkeypatch.setattr(app_module, "is_jwt_protected", lambda request: False)
    request = make_request(method="POST", path="/v1/private")

    response = run_async(app_module.check_api_key(request))

    assert response.status == 401
    assert response_json(response)["error"] == "API key required"


def test_check_api_key_rejects_invalid_header_format(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(app_module, "is_method_open", lambda request: False)
    monkeypatch.setattr(app_module, "is_route_open", lambda request: False)
    monkeypatch.setattr(app_module, "is_jwt_protected", lambda request: False)
    request = make_request(
        method="POST",
        path="/v1/private",
        headers={"Authorization": "Token bad"},
    )

    response = run_async(app_module.check_api_key(request))

    assert response.status == 401
    assert response_json(response)["error"] == "Invalid API key format"


def test_check_api_key_rejects_mismatched_key(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(app_module, "is_method_open", lambda request: False)
    monkeypatch.setattr(app_module, "is_route_open", lambda request: False)
    monkeypatch.setattr(app_module, "is_jwt_protected", lambda request: False)
    monkeypatch.setattr(app_module, "API_KEY", "expected-key")
    request = make_request(
        method="POST",
        path="/v1/private",
        headers={"Authorization": "Bearer wrong-key"},
    )

    response = run_async(app_module.check_api_key(request))

    assert response.status == 403
    assert response_json(response)["error"] == "Invalid API key"


def test_check_api_key_allows_valid_key(monkeypatch, make_request, run_async):
    monkeypatch.setattr(app_module, "is_method_open", lambda request: False)
    monkeypatch.setattr(app_module, "is_route_open", lambda request: False)
    monkeypatch.setattr(app_module, "is_jwt_protected", lambda request: False)
    monkeypatch.setattr(app_module, "API_KEY", "expected-key")
    request = make_request(
        method="POST",
        path="/v1/private",
        headers={"Authorization": "Bearer expected-key"},
    )

    response = run_async(app_module.check_api_key(request))

    assert response is None


def test_rate_limit_refresh_keys_on_session_and_ip(
    monkeypatch, make_request, run_async
):
    """Users behind one NAT address get separate per-session buckets, with a looser
    shared per-IP ceiling on top."""
    keys = []

    async def _record(key, limit, window):
        keys.append((key, limit))
        return (True, window)

    monkeypatch.setattr(rate_limit_module, "get_client_ip", lambda request: "203.0.113.9")
    monkeypatch.setattr(rate_limit_module, "_async_increment_and_check_limit", _record)

    for cookie in ("tab-a-token", "tab-b-token"):
        request = make_request(
            method="POST", path="/v1/auth/refresh", cookies={"refresh_token": cookie}
        )
        assert run_async(rate_limit_module.rate_limit_middleware(request)) is None

    session_keys = {k for k, limit in keys if k.startswith("rate_limit:refresh:")}
    ip_keys = {k for k, limit in keys if k.startswith("rate_limit:refresh_ip:")}
    assert len(session_keys) == 2
    assert ip_keys == {"rate_limit:refresh_ip:203.0.113.9"}
    assert all("tab-a-token" not in k and "tab-b-token" not in k for k, _ in keys)


def test_rate_limit_logout_is_limited_by_ip(
    monkeypatch, make_request, run_async, response_json
):
    async def _over(key, limit, window):
        assert key == "rate_limit:logout:203.0.113.9"
        return (False, 30)

    monkeypatch.setattr(rate_limit_module, "get_client_ip", lambda request: "203.0.113.9")
    monkeypatch.setattr(rate_limit_module, "_async_increment_and_check_limit", _over)

    response = run_async(
        rate_limit_module.rate_limit_middleware(
            make_request(method="POST", path="/v1/auth/logout")
        )
    )

    assert response.status == 429


class _FakeLoginFailureRedis:
    def __init__(self, count=None, ttl=120):
        self.count = count
        self._ttl = ttl
        self.deleted = []

    async def get(self, key):
        return self.count

    async def ttl(self, key):
        return self._ttl

    async def delete(self, key):
        self.deleted.append(key)


def test_login_failure_throttle(monkeypatch, run_async):
    limit = rate_limit_module.LOGIN_FAILURE_LIMIT["requests"]
    fake = _FakeLoginFailureRedis(count=str(limit))
    monkeypatch.setattr(
        rate_limit_module.redis_client, "get_async_redis_client", _amock(lambda: fake)
    )

    assert run_async(rate_limit_module.async_get_login_failure_retry_after("Alice")) == 120
    fake.count = str(limit - 1)
    assert run_async(rate_limit_module.async_get_login_failure_retry_after("alice")) is None

    run_async(rate_limit_module.async_clear_login_failures("ALICE"))
    assert fake.deleted == ["rate_limit:login_fail:alice"]


def test_login_failure_throttle_fails_open(monkeypatch, run_async):
    async def _down():
        raise RuntimeError("redis down")

    monkeypatch.setattr(rate_limit_module.redis_client, "get_async_redis_client", _down)

    assert run_async(rate_limit_module.async_get_login_failure_retry_after("a")) is None


def test_check_api_key_rejects_wrong_key_and_empty_configured_key(
    monkeypatch, make_request, run_async
):
    monkeypatch.setattr(app_module, "is_method_open", lambda request: False)
    monkeypatch.setattr(app_module, "is_route_open", lambda request: False)
    monkeypatch.setattr(app_module, "is_jwt_protected", lambda request: False)

    monkeypatch.setattr(app_module, "API_KEY", "real-key")
    wrong = make_request(method="POST", path="/x", headers={"Authorization": "Bearer nope"})
    right = make_request(method="POST", path="/x", headers={"Authorization": "Bearer real-key"})
    assert run_async(app_module.check_api_key(wrong)).status == 403
    assert run_async(app_module.check_api_key(right)) is None

    monkeypatch.setattr(app_module, "API_KEY", "")
    blank = make_request(method="POST", path="/x", headers={"Authorization": "Bearer "})
    assert run_async(app_module.check_api_key(blank)).status == 403
