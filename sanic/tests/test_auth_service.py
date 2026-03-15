import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt

from conftest import _amock

import services.auth as auth_service
import services.postgres as postgres_client


def _future_datetime(seconds=3600):
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


def _active_session(session_id="session-1", user_id=1, auth_version=1):
    return {
        "session_id": session_id,
        "user_id": user_id,
        "auth_version": auth_version,
        "expires_at": _future_datetime(),
        "revoked_at": None,
    }


def test_hash_password_and_verify_password_round_trip():
    password = "P@ssw0rd!"

    password_hash = auth_service.hash_password(password)

    assert password_hash != password
    assert auth_service.verify_password(password, password_hash) is True
    assert auth_service.verify_password("not-the-password", password_hash) is False


def test_hash_refresh_token_matches_sha256():
    token = "refresh-token"

    assert (
        auth_service.hash_refresh_token(token)
        == hashlib.sha256(token.encode("utf-8")).hexdigest()
    )


def test_generate_and_verify_jwt_token_round_trip():
    token = auth_service.generate_jwt_token(
        user_id=7,
        username="user7",
        session_id="session-7",
        auth_version=3,
    )

    payload = auth_service.verify_jwt_token(token)

    assert payload is not None
    assert payload["type"] == "access"
    assert payload["user_id"] == 7
    assert payload["username"] == "user7"
    assert payload["session_id"] == "session-7"
    assert payload["auth_version"] == 3


def test_verify_jwt_token_rejects_non_access_token_type():
    now = int(datetime.now(timezone.utc).timestamp())
    refresh_like_token = jwt.encode(
        {
            "type": "refresh",
            "exp": now + 60,
        },
        auth_service.JWT_SECRET_KEY,
        algorithm=auth_service.JWT_ALGORITHM,
    )

    assert auth_service.verify_jwt_token(refresh_like_token) is None


def test_normalize_datetime_parses_iso_string_and_rejects_invalid():
    iso_value = "2026-03-14T12:30:45Z"

    parsed = auth_service._normalize_datetime(iso_value)

    assert parsed is not None
    assert parsed.tzinfo is not None
    assert auth_service._normalize_datetime("not-a-datetime") is None


def test_is_auth_session_active_enforces_revocation_and_expiry():
    active_session = _active_session()
    revoked_session = {**_active_session(), "revoked_at": _future_datetime()}
    expired_session = {**_active_session(), "expires_at": _future_datetime(-5)}

    assert auth_service.is_auth_session_active(active_session) is True
    assert auth_service.is_auth_session_active(revoked_session) is False
    assert auth_service.is_auth_session_active(expired_session) is False
    assert auth_service.is_auth_session_active(None) is False


def test_validate_access_token_success(monkeypatch, run_async):
    payload = {
        "user_id": 8,
        "username": "user8",
        "session_id": "session-8",
        "auth_version": 5,
    }

    monkeypatch.setattr(auth_service, "verify_jwt_token", lambda token: payload)
    monkeypatch.setattr(
        auth_service, "_async_get_user_auth_version", _amock(lambda user_id: 5)
    )
    monkeypatch.setattr(
        auth_service,
        "_async_get_auth_session",
        _amock(
            lambda session_id: _active_session(
                session_id="session-8", user_id=8, auth_version=5
            )
        ),
    )

    assert (
        run_async(auth_service.async_validate_access_token("access-token")) == payload
    )


def test_validate_access_token_rejects_auth_version_mismatch(monkeypatch, run_async):
    payload = {
        "user_id": 8,
        "username": "user8",
        "session_id": "session-8",
        "auth_version": 2,
    }

    monkeypatch.setattr(auth_service, "verify_jwt_token", lambda token: payload)
    monkeypatch.setattr(
        auth_service, "_async_get_user_auth_version", _amock(lambda user_id: 3)
    )

    assert run_async(auth_service.async_validate_access_token("access-token")) is None


def test_validate_access_token_clears_cache_for_inactive_session(
    monkeypatch, run_async
):
    payload = {
        "user_id": 8,
        "username": "user8",
        "session_id": "session-8",
        "auth_version": 5,
    }
    cleared_session_ids = []

    monkeypatch.setattr(auth_service, "verify_jwt_token", lambda token: payload)
    monkeypatch.setattr(
        auth_service, "_async_get_user_auth_version", _amock(lambda user_id: 5)
    )
    monkeypatch.setattr(
        auth_service,
        "_async_get_auth_session",
        _amock(
            lambda session_id: {
                **_active_session(session_id="session-8", user_id=8, auth_version=5),
                "revoked_at": _future_datetime(),
            }
        ),
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "async_clear_cached_auth_session",
        _amock(lambda session_id: cleared_session_ids.append(session_id)),
    )

    assert run_async(auth_service.async_validate_access_token("access-token")) is None
    assert cleared_session_ids == ["session-8"]


def test_register_user_rejects_existing_username(monkeypatch, run_async):
    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_user_by_username",
        _amock(lambda username: {"id": 1}),
    )

    success, data, error = run_async(
        auth_service.async_register_user("existing", "Password123!")
    )

    assert success is False
    assert data is None
    assert error == auth_service.AUTH_ERROR_USERNAME_EXISTS


def test_register_user_handles_username_race_condition(monkeypatch, run_async):
    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_user_by_username",
        _amock(lambda username: None),
    )

    def _raise_username_exists(**kwargs):
        raise postgres_client.UsernameAlreadyExistsError("exists")

    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_create_user_with_settings_and_auth_session",
        _amock(_raise_username_exists),
    )

    success, data, error = run_async(
        auth_service.async_register_user("newuser", "Password123!")
    )

    assert success is False
    assert data is None
    assert error == auth_service.AUTH_ERROR_USERNAME_EXISTS


def test_register_user_success(monkeypatch, run_async):
    now = datetime(2026, 3, 14, 10, 0, tzinfo=timezone.utc)
    cached_versions = []
    cached_sessions = []

    user = {
        "id": 11,
        "username": "alice11",
        "auth_version": 1,
        "created_at": now,
    }
    session = {
        "session_id": "session-11",
        "user_id": 11,
        "auth_version": 1,
        "expires_at": now + timedelta(days=30),
        "revoked_at": None,
    }

    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_user_by_username",
        _amock(lambda username: None),
    )
    monkeypatch.setattr(auth_service, "hash_password", lambda password: "hashed-pass")
    monkeypatch.setattr(
        auth_service,
        "generate_refresh_token",
        lambda: "refresh-token-11",
    )
    monkeypatch.setattr(
        auth_service,
        "generate_jwt_token",
        lambda **kwargs: "access-token-11",
    )
    monkeypatch.setattr(
        auth_service.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="session-11"),
    )
    monkeypatch.setattr(
        auth_service,
        "get_refresh_token_expiry",
        lambda: now + timedelta(days=30),
    )
    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_create_user_with_settings_and_auth_session",
        _amock(lambda **kwargs: {"user": user, "session": session}),
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "async_cache_user_auth_version",
        _amock(
            lambda user_id, auth_version: cached_versions.append(
                (user_id, auth_version)
            )
        ),
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "async_cache_auth_session",
        _amock(
            lambda session_id, session_data: cached_sessions.append(
                (session_id, session_data)
            )
        ),
    )

    success, data, error = run_async(
        auth_service.async_register_user(
            "alice11",
            "Password123!",
            created_ip="10.0.0.1",
            created_user_agent="pytest",
        )
    )

    assert success is True
    assert error == ""
    assert data["access_token"] == "access-token-11"
    assert data["refresh_token"] == "refresh-token-11"
    assert data["user"]["username"] == "alice11"
    assert cached_versions == [(11, 1)]
    assert cached_sessions[0][0] == "session-11"


def test_login_user_rejects_unknown_user(monkeypatch, run_async):
    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_user_by_username",
        _amock(lambda username: None),
    )

    success, data, error = run_async(auth_service.async_login_user("ghost", "password"))

    assert success is False
    assert data is None
    assert error == auth_service.AUTH_ERROR_INVALID_CREDENTIALS


def test_login_user_rejects_invalid_password(monkeypatch, run_async):
    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_user_by_username",
        _amock(
            lambda username: {
                "id": 9,
                "username": "alice",
                "password_hash": "hashed",
                "auth_version": 1,
                "created_at": _future_datetime(),
            }
        ),
    )
    monkeypatch.setattr(
        auth_service, "verify_password", lambda password, password_hash: False
    )

    success, data, error = run_async(
        auth_service.async_login_user("alice", "bad-password")
    )

    assert success is False
    assert data is None
    assert error == auth_service.AUTH_ERROR_INVALID_CREDENTIALS


def test_login_user_success(monkeypatch, run_async):
    user = {
        "id": 9,
        "username": "alice",
        "password_hash": "hashed",
        "auth_version": 4,
        "created_at": _future_datetime(),
    }

    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_user_by_username",
        _amock(lambda username: user),
    )
    monkeypatch.setattr(
        auth_service, "verify_password", lambda password, password_hash: True
    )
    monkeypatch.setattr(
        auth_service,
        "_async_create_session",
        _amock(
            lambda **kwargs: (
                True,
                {
                    "session_id": "session-9",
                    "refresh_token": "refresh-9",
                    "auth_version": 4,
                },
                "",
            )
        ),
    )
    monkeypatch.setattr(
        auth_service,
        "generate_jwt_token",
        lambda **kwargs: "access-token-9",
    )

    success, data, error = run_async(
        auth_service.async_login_user("alice", "Password123!")
    )

    assert success is True
    assert error == ""
    assert data["access_token"] == "access-token-9"
    assert data["refresh_token"] == "refresh-9"
    assert data["user"]["username"] == "alice"


def test_refresh_session_rejects_inactive_or_unknown_session(monkeypatch, run_async):
    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_auth_session_by_refresh_token_hash",
        _amock(lambda refresh_token_hash: None),
    )

    success, data, error = run_async(
        auth_service.async_refresh_session("refresh-token")
    )

    assert success is False
    assert data is None
    assert error == auth_service.AUTH_ERROR_INVALID_REFRESH_TOKEN


def test_refresh_session_rejects_auth_version_mismatch(monkeypatch, run_async):
    cleared_session_ids = []
    session = _active_session(session_id="session-20", user_id=20, auth_version=1)

    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_auth_session_by_refresh_token_hash",
        _amock(lambda refresh_token_hash: session),
    )
    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_user_by_id",
        _amock(
            lambda user_id: {
                "id": 20,
                "username": "bob20",
                "auth_version": 2,
            }
        ),
    )
    monkeypatch.setattr(
        auth_service, "_async_get_user_auth_version", _amock(lambda user_id: 2)
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "async_clear_cached_auth_session",
        _amock(lambda session_id: cleared_session_ids.append(session_id)),
    )

    success, data, error = run_async(
        auth_service.async_refresh_session("refresh-token")
    )

    assert success is False
    assert data is None
    assert error == auth_service.AUTH_ERROR_INVALID_REFRESH_TOKEN
    assert cleared_session_ids == ["session-20"]


def test_refresh_session_success(monkeypatch, run_async):
    now = datetime(2026, 3, 14, 11, 0, tzinfo=timezone.utc)
    cached_versions = []
    cached_sessions = []
    captured_rotate_args = {}

    session = _active_session(session_id="session-21", user_id=21, auth_version=3)
    user = {
        "id": 21,
        "username": "user21",
        "auth_version": 3,
    }
    rotated_session = {
        **session,
        "expires_at": now + timedelta(days=30),
    }

    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_auth_session_by_refresh_token_hash",
        _amock(lambda refresh_token_hash: session),
    )
    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_user_by_id",
        _amock(lambda user_id: user),
    )
    monkeypatch.setattr(
        auth_service, "_async_get_user_auth_version", _amock(lambda user_id: 3)
    )
    monkeypatch.setattr(
        auth_service, "generate_refresh_token", lambda: "new-refresh-21"
    )
    monkeypatch.setattr(
        auth_service, "get_refresh_token_expiry", lambda: now + timedelta(days=30)
    )
    monkeypatch.setattr(
        auth_service, "generate_jwt_token", lambda **kwargs: "access-token-21"
    )

    def _rotate_auth_session_refresh_token(**kwargs):
        captured_rotate_args.update(kwargs)
        return rotated_session

    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_rotate_auth_session_refresh_token",
        _amock(_rotate_auth_session_refresh_token),
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "async_cache_user_auth_version",
        _amock(
            lambda user_id, auth_version: cached_versions.append(
                (user_id, auth_version)
            )
        ),
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "async_cache_auth_session",
        _amock(
            lambda session_id, session_data: cached_sessions.append(
                (session_id, session_data)
            )
        ),
    )

    success, data, error = run_async(
        auth_service.async_refresh_session("old-refresh-token")
    )

    assert success is True
    assert error == ""
    assert data["access_token"] == "access-token-21"
    assert data["refresh_token"] == "new-refresh-21"
    assert captured_rotate_args["session_id"] == "session-21"
    assert captured_rotate_args[
        "current_refresh_token_hash"
    ] == auth_service.hash_refresh_token("old-refresh-token")
    assert cached_versions == [(21, 3)]
    assert cached_sessions[0][0] == "session-21"


def test_logout_session_revokes_session_and_clears_cache(monkeypatch, run_async):
    cleared_session_ids = []

    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_revoke_auth_session",
        _amock(lambda session_id, reason: True),
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "async_clear_cached_auth_session",
        _amock(lambda session_id: cleared_session_ids.append(session_id)),
    )

    assert run_async(auth_service.async_logout_session("session-logout")) is True
    assert cleared_session_ids == ["session-logout"]


def test_change_password_rejects_when_old_password_is_invalid(monkeypatch, run_async):
    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_user_by_id",
        _amock(
            lambda user_id: {
                "id": user_id,
                "username": "user33",
                "password_hash": "hash",
            }
        ),
    )
    monkeypatch.setattr(
        auth_service, "verify_password", lambda password, password_hash: False
    )

    success, data, error = run_async(
        auth_service.async_change_password(
            33,
            "wrong-old",
            "NewPassword1!",
            "user33",
        )
    )

    assert success is False
    assert data is None
    assert error == "Current password is incorrect"


def test_change_password_rejects_when_new_password_matches_username(
    monkeypatch, run_async
):
    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_user_by_id",
        _amock(
            lambda user_id: {
                "id": user_id,
                "username": "user44",
                "password_hash": "hash",
            }
        ),
    )
    monkeypatch.setattr(
        auth_service, "verify_password", lambda password, password_hash: True
    )

    success, data, error = run_async(
        auth_service.async_change_password(44, "old", "user44", "user44")
    )

    assert success is False
    assert data is None
    assert error == "Password cannot be the same as username"


def test_change_password_rejects_when_new_password_matches_current(
    monkeypatch, run_async
):
    calls = iter([True, True])

    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_user_by_id",
        _amock(
            lambda user_id: {
                "id": user_id,
                "username": "user55",
                "password_hash": "hash",
            }
        ),
    )
    monkeypatch.setattr(
        auth_service,
        "verify_password",
        lambda password, password_hash: next(calls),
    )

    success, data, error = run_async(
        auth_service.async_change_password(
            55,
            "old-password",
            "old-password",
            "user55",
        )
    )

    assert success is False
    assert data is None
    assert error == "New password must be different from current password"


def test_change_password_success(monkeypatch, run_async):
    cached_versions = []
    cached_sessions = []
    cleared_session_groups = []

    calls = iter([True, False])
    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_user_by_id",
        _amock(
            lambda user_id: {
                "id": user_id,
                "username": "user66",
                "password_hash": "current-hash",
            }
        ),
    )
    monkeypatch.setattr(
        auth_service,
        "verify_password",
        lambda password, password_hash: next(calls),
    )
    monkeypatch.setattr(auth_service, "hash_password", lambda password: "new-hash")
    monkeypatch.setattr(auth_service, "generate_refresh_token", lambda: "refresh-66")
    monkeypatch.setattr(
        auth_service, "generate_jwt_token", lambda **kwargs: "access-66"
    )
    monkeypatch.setattr(
        auth_service.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="session-66"),
    )
    monkeypatch.setattr(
        auth_service,
        "get_refresh_token_expiry",
        lambda: _future_datetime(7200),
    )
    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_change_password_and_create_session",
        _amock(
            lambda **kwargs: {
                "session_id": "session-66",
                "auth_version": 6,
                "user_id": 66,
                "expires_at": _future_datetime(7200),
                "revoked_at": None,
                "revoked_session_ids": ["old-session-1", "old-session-2"],
            }
        ),
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "async_clear_cached_auth_sessions",
        _amock(lambda session_ids: cleared_session_groups.append(session_ids)),
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "async_cache_user_auth_version",
        _amock(
            lambda user_id, auth_version: cached_versions.append(
                (user_id, auth_version)
            )
        ),
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "async_cache_auth_session",
        _amock(
            lambda session_id, session_data: cached_sessions.append(
                (session_id, session_data)
            )
        ),
    )

    success, data, error = run_async(
        auth_service.async_change_password(
            66,
            "current-password",
            "NewPassword1!",
            "user66",
            created_ip="192.168.0.66",
            created_user_agent="pytest-agent",
        )
    )

    assert success is True
    assert error == ""
    assert data["message"] == "Password changed successfully"
    assert data["access_token"] == "access-66"
    assert data["refresh_token"] == "refresh-66"
    assert cleared_session_groups == [["old-session-1", "old-session-2"]]
    assert cached_versions == [(66, 6)]
    assert cached_sessions[0][0] == "session-66"


def test_get_user_by_id_returns_serialized_profile(monkeypatch, run_async):
    created_at = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_user_by_id",
        _amock(
            lambda user_id: {
                "id": user_id,
                "username": "user77",
                "created_at": created_at,
            }
        ),
    )

    profile = run_async(auth_service.async_get_user_by_id(77))

    assert profile["id"] == 77
    assert profile["username"] == "user77"
    assert profile["created_at"].startswith("2026-03-14T12:00:00")


def test_get_user_by_id_returns_none_on_failure(monkeypatch, run_async):
    def _raise_error(user_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        auth_service.postgres_client, "async_get_user_by_id", _amock(_raise_error)
    )

    assert run_async(auth_service.async_get_user_by_id(1)) is None
