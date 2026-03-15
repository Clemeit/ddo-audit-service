import middleware.jwt as jwt_middleware_module


def test_unauthorized_response_payload(response_json):
    response = jwt_middleware_module._unauthorized_response()

    assert response.status == 401
    assert response_json(response) == {"error": "Unauthorized"}


def test_jwt_middleware_skips_unprotected_route(monkeypatch, make_request, run_async):
    monkeypatch.setattr(
        jwt_middleware_module, "is_jwt_protected", lambda request: False
    )
    request = make_request(method="GET", path="/v1/game/info")

    response = run_async(jwt_middleware_module.jwt_middleware(request))

    assert response is None


def test_jwt_middleware_rejects_missing_authorization_header(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(jwt_middleware_module, "is_jwt_protected", lambda request: True)
    request = make_request(method="GET", path="/v1/user/profile")

    response = run_async(jwt_middleware_module.jwt_middleware(request))

    assert response.status == 401
    assert response_json(response)["error"] == "Unauthorized"


def test_jwt_middleware_rejects_malformed_authorization_header(
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


def test_jwt_middleware_rejects_invalid_token(
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
        headers={"Authorization": "Bearer malformed"},
    )

    response = run_async(jwt_middleware_module.jwt_middleware(request))

    assert response.status == 401
    assert response_json(response)["error"] == "Unauthorized"


def test_jwt_middleware_rejects_expired_token(
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
        headers={"Authorization": "Bearer expired.token"},
    )

    response = run_async(jwt_middleware_module.jwt_middleware(request))

    assert response.status == 401
    assert response_json(response)["error"] == "Unauthorized"


def test_jwt_middleware_uses_bearer_token_without_prefix(
    monkeypatch, make_request, run_async
):
    captured = {}

    async def _mock_async_validate(token):
        captured["token"] = token
        return {
            "user_id": 42,
            "username": "user42",
            "session_id": "session-42",
            "auth_version": 3,
        }

    monkeypatch.setattr(jwt_middleware_module, "is_jwt_protected", lambda request: True)
    monkeypatch.setattr(
        jwt_middleware_module.auth_service,
        "async_validate_access_token",
        _mock_async_validate,
    )
    request = make_request(
        method="GET",
        path="/v1/user/profile",
        headers={"Authorization": "Bearer abc.def.ghi"},
    )

    response = run_async(jwt_middleware_module.jwt_middleware(request))

    assert response is None
    assert captured["token"] == "abc.def.ghi"
    assert request.ctx.user_id == 42
    assert request.ctx.username == "user42"
    assert request.ctx.session_id == "session-42"
    assert request.ctx.auth_version == 3


def test_jwt_middleware_rejects_payload_missing_user_id(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(jwt_middleware_module, "is_jwt_protected", lambda request: True)

    async def _mock_async_validate(token):
        return {
            "user_id": None,
            "username": "user88",
            "session_id": "session-88",
            "auth_version": 1,
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


def test_jwt_middleware_rejects_payload_missing_session_id(
    monkeypatch, make_request, run_async, response_json
):
    monkeypatch.setattr(jwt_middleware_module, "is_jwt_protected", lambda request: True)

    async def _mock_async_validate(token):
        return {
            "user_id": 88,
            "username": "user88",
            "session_id": None,
            "auth_version": 1,
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
