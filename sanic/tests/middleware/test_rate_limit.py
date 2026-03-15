from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import middleware.rate_limit as rate_limit_module


@contextmanager
def _redis_client_context(client):
    yield client


def test_increment_and_check_limit_initial_request_sets_ttl(monkeypatch):
    mock_client = MagicMock()
    mock_client.incr.return_value = 1
    mock_client.ttl.return_value = -1
    monkeypatch.setattr(
        rate_limit_module.redis_client,
        "get_redis_client",
        lambda: _redis_client_context(mock_client),
    )

    allowed, retry_after = rate_limit_module._increment_and_check_limit(
        "rate_limit:auth:127.0.0.1:/v1/auth/login",
        limit=5,
        window=900,
    )

    assert allowed is True
    assert retry_after == 900
    mock_client.expire.assert_called_once_with(
        "rate_limit:auth:127.0.0.1:/v1/auth/login", 900
    )


def test_increment_and_check_limit_keeps_existing_ttl(monkeypatch):
    mock_client = MagicMock()
    mock_client.incr.return_value = 3
    mock_client.ttl.return_value = 240
    monkeypatch.setattr(
        rate_limit_module.redis_client,
        "get_redis_client",
        lambda: _redis_client_context(mock_client),
    )

    allowed, retry_after = rate_limit_module._increment_and_check_limit(
        "rate_limit:auth:127.0.0.1:/v1/auth/login",
        limit=5,
        window=900,
    )

    assert allowed is True
    assert retry_after == 240
    mock_client.expire.assert_not_called()


def test_increment_and_check_limit_allows_when_count_equals_limit(monkeypatch):
    mock_client = MagicMock()
    mock_client.incr.return_value = 5
    mock_client.ttl.return_value = 180
    monkeypatch.setattr(
        rate_limit_module.redis_client,
        "get_redis_client",
        lambda: _redis_client_context(mock_client),
    )

    allowed, retry_after = rate_limit_module._increment_and_check_limit(
        "rate_limit:user:99:user:profile:password",
        limit=5,
        window=900,
    )

    assert allowed is True
    assert retry_after == 180


def test_increment_and_check_limit_blocks_when_count_exceeds_limit(monkeypatch):
    mock_client = MagicMock()
    mock_client.incr.return_value = 6
    mock_client.ttl.return_value = 179
    monkeypatch.setattr(
        rate_limit_module.redis_client,
        "get_redis_client",
        lambda: _redis_client_context(mock_client),
    )

    allowed, retry_after = rate_limit_module._increment_and_check_limit(
        "rate_limit:user:99:user:profile:password",
        limit=5,
        window=900,
    )

    assert allowed is False
    assert retry_after == 179


def test_increment_and_check_limit_resets_ttl_when_missing(monkeypatch):
    mock_client = MagicMock()
    mock_client.incr.return_value = 2
    mock_client.ttl.return_value = None
    monkeypatch.setattr(
        rate_limit_module.redis_client,
        "get_redis_client",
        lambda: _redis_client_context(mock_client),
    )

    allowed, retry_after = rate_limit_module._increment_and_check_limit(
        "rate_limit:auth:127.0.0.1:/v1/auth/refresh",
        limit=5,
        window=900,
    )

    assert allowed is True
    assert retry_after == 900
    mock_client.expire.assert_called_once_with(
        "rate_limit:auth:127.0.0.1:/v1/auth/refresh", 900
    )


def test_increment_and_check_limit_runtime_error_fails_open(monkeypatch):
    def _raise_runtime_error():
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(
        rate_limit_module.redis_client,
        "get_redis_client",
        _raise_runtime_error,
    )

    allowed, retry_after = rate_limit_module._increment_and_check_limit(
        "rate_limit:auth:127.0.0.1:/v1/auth/register",
        limit=5,
        window=900,
    )

    assert allowed is True
    assert retry_after is None


def test_rate_limit_middleware_auth_endpoint_builds_ip_key(
    monkeypatch, make_request, run_async
):
    captured = {}

    def _capture_limit_call(rate_limit_key, limit, window):
        captured["rate_limit_key"] = rate_limit_key
        captured["limit"] = limit
        captured["window"] = window
        return True, 45

    monkeypatch.setattr(
        rate_limit_module,
        "get_client_ip",
        lambda request: "203.0.113.10",
    )
    monkeypatch.setattr(
        rate_limit_module, "_increment_and_check_limit", _capture_limit_call
    )
    request = make_request(method="POST", path="/v2/auth/login")

    response = run_async(rate_limit_module.rate_limit_middleware(request))

    assert response is None
    assert captured["rate_limit_key"] == "rate_limit:auth:203.0.113.10:/v2/auth/login"
    assert captured["limit"] == rate_limit_module.AUTH_RATE_LIMIT["requests"]
    assert captured["window"] == rate_limit_module.AUTH_RATE_LIMIT["window"]


def test_rate_limit_middleware_auth_endpoint_blocks_with_default_retry_after(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        rate_limit_module,
        "get_client_ip",
        lambda request: "203.0.113.10",
    )
    monkeypatch.setattr(
        rate_limit_module,
        "_increment_and_check_limit",
        lambda *args, **kwargs: (False, None),
    )
    request = make_request(method="POST", path="/v1/auth/refresh")

    response = run_async(rate_limit_module.rate_limit_middleware(request))

    assert response.status == 429
    assert (
        response_json(response)["error"]
        == "Rate limit exceeded. Try again in 15 minutes."
    )
    assert response.headers["Retry-After"] == str(
        rate_limit_module.AUTH_RATE_LIMIT["window"]
    )


def test_rate_limit_middleware_user_endpoint_normalizes_key(
    monkeypatch, make_request, run_async
):
    captured = {}

    def _capture_limit_call(rate_limit_key, limit, window):
        captured["rate_limit_key"] = rate_limit_key
        captured["limit"] = limit
        captured["window"] = window
        return True, 900

    monkeypatch.setattr(
        rate_limit_module, "_increment_and_check_limit", _capture_limit_call
    )
    request = make_request(
        method="PATCH",
        path="/v2/user/settings/persistent",
        ctx=SimpleNamespace(user_id=77),
    )

    response = run_async(rate_limit_module.rate_limit_middleware(request))

    assert response is None
    assert captured["rate_limit_key"] == "rate_limit:user:77:user:settings:persistent"
    assert captured["limit"] == rate_limit_module.USER_RATE_LIMIT["requests"]
    assert captured["window"] == rate_limit_module.USER_RATE_LIMIT["window"]


def test_rate_limit_middleware_user_endpoint_skips_when_missing_user_id(
    monkeypatch, make_request, run_async
):
    called = {"value": False}

    def _track_call(*args, **kwargs):
        called["value"] = True
        return True, 0

    monkeypatch.setattr(rate_limit_module, "_increment_and_check_limit", _track_call)
    request = make_request(method="PUT", path="/v1/user/profile/password")

    response = run_async(rate_limit_module.rate_limit_middleware(request))

    assert response is None
    assert called["value"] is False


def test_rate_limit_middleware_user_endpoint_blocks_when_limit_exceeded(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(
        rate_limit_module,
        "_increment_and_check_limit",
        lambda *args, **kwargs: (False, 32),
    )
    request = make_request(
        method="PUT",
        path="/v1/user/profile/password",
        ctx=SimpleNamespace(user_id=12),
    )

    response = run_async(rate_limit_module.rate_limit_middleware(request))

    assert response.status == 429
    assert response_json(response)["error"] == "Rate limit exceeded. Try again later."
    assert response_json(response)["retry_after"] == 32
    assert response.headers["Retry-After"] == "32"


def test_rate_limit_middleware_fails_open_on_unexpected_error(
    monkeypatch, make_request, run_async
):
    def _raise_error(*args, **kwargs):
        raise ValueError("redis timeout")

    monkeypatch.setattr(
        rate_limit_module,
        "get_client_ip",
        lambda request: "203.0.113.10",
    )
    monkeypatch.setattr(rate_limit_module, "_increment_and_check_limit", _raise_error)
    request = make_request(method="POST", path="/v1/auth/register")

    response = run_async(rate_limit_module.rate_limit_middleware(request))

    assert response is None


def test_rate_limit_middleware_ignores_non_limited_path(
    monkeypatch, make_request, run_async
):
    called = {"value": False}

    def _track_call(*args, **kwargs):
        called["value"] = True
        return True, 0

    monkeypatch.setattr(rate_limit_module, "_increment_and_check_limit", _track_call)
    request = make_request(method="GET", path="/v1/game/info")

    response = run_async(rate_limit_module.rate_limit_middleware(request))

    assert response is None
    assert called["value"] is False
