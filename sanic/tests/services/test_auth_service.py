import hashlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import jwt

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


def test_validate_access_token_success(monkeypatch):
    payload = {
        "user_id": 8,
        "username": "user8",
        "session_id": "session-8",
        "auth_version": 5,
    }

    monkeypatch.setattr(auth_service, "verify_jwt_token", lambda token: payload)
    monkeypatch.setattr(auth_service, "_get_user_auth_version", lambda user_id: 5)
    monkeypatch.setattr(
        auth_service,
        "_get_auth_session",
        lambda session_id: _active_session(
            session_id="session-8", user_id=8, auth_version=5
        ),
    )

    assert auth_service.validate_access_token("access-token") == payload


def test_validate_access_token_rejects_auth_version_mismatch(monkeypatch):
    payload = {
        "user_id": 8,
        "username": "user8",
        "session_id": "session-8",
        "auth_version": 2,
    }

    monkeypatch.setattr(auth_service, "verify_jwt_token", lambda token: payload)
    monkeypatch.setattr(auth_service, "_get_user_auth_version", lambda user_id: 3)

    assert auth_service.validate_access_token("access-token") is None


def test_validate_access_token_clears_cache_for_inactive_session(monkeypatch):
    payload = {
        "user_id": 8,
        "username": "user8",
        "session_id": "session-8",
        "auth_version": 5,
    }
    cleared_session_ids = []

    monkeypatch.setattr(auth_service, "verify_jwt_token", lambda token: payload)
    monkeypatch.setattr(auth_service, "_get_user_auth_version", lambda user_id: 5)
    monkeypatch.setattr(
        auth_service,
        "_get_auth_session",
        lambda session_id: {
            **_active_session(session_id="session-8", user_id=8, auth_version=5),
            "revoked_at": _future_datetime(),
        },
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "clear_cached_auth_session",
        lambda session_id: cleared_session_ids.append(session_id),
    )

    assert auth_service.validate_access_token("access-token") is None
    assert cleared_session_ids == ["session-8"]


def test_register_user_rejects_existing_username(monkeypatch):
    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_user_by_username",
        lambda username: {"id": 1},
    )

    success, data, error = auth_service.register_user("existing", "Password123!")

    assert success is False
    assert data is None
    assert error == auth_service.AUTH_ERROR_USERNAME_EXISTS


def test_register_user_handles_username_race_condition(monkeypatch):
    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_user_by_username",
        lambda username: None,
    )

    def _raise_username_exists(**kwargs):
        raise postgres_client.UsernameAlreadyExistsError("exists")

    monkeypatch.setattr(
        auth_service.postgres_client,
        "create_user_with_settings_and_auth_session",
        _raise_username_exists,
    )

    success, data, error = auth_service.register_user("newuser", "Password123!")

    assert success is False
    assert data is None
    assert error == auth_service.AUTH_ERROR_USERNAME_EXISTS


def test_register_user_success(monkeypatch):
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
        "get_user_by_username",
        lambda username: None,
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
        "create_user_with_settings_and_auth_session",
        lambda **kwargs: {"user": user, "session": session},
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "cache_user_auth_version",
        lambda user_id, auth_version: cached_versions.append((user_id, auth_version)),
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "cache_auth_session",
        lambda session_id, session_data: cached_sessions.append(
            (session_id, session_data)
        ),
    )

    success, data, error = auth_service.register_user(
        "alice11",
        "Password123!",
        created_ip="10.0.0.1",
        created_user_agent="pytest",
    )

    assert success is True
    assert error == ""
    assert data["access_token"] == "access-token-11"
    assert data["refresh_token"] == "refresh-token-11"
    assert data["user"]["username"] == "alice11"
    assert cached_versions == [(11, 1)]
    assert cached_sessions[0][0] == "session-11"


def test_login_user_rejects_unknown_user(monkeypatch):
    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_user_by_username",
        lambda username: None,
    )

    success, data, error = auth_service.login_user("ghost", "password")

    assert success is False
    assert data is None
    assert error == auth_service.AUTH_ERROR_INVALID_CREDENTIALS


def test_login_user_rejects_invalid_password(monkeypatch):
    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_user_by_username",
        lambda username: {
            "id": 9,
            "username": "alice",
            "password_hash": "hashed",
            "auth_version": 1,
            "created_at": _future_datetime(),
        },
    )
    monkeypatch.setattr(
        auth_service, "verify_password", lambda password, password_hash: False
    )

    success, data, error = auth_service.login_user("alice", "bad-password")

    assert success is False
    assert data is None
    assert error == auth_service.AUTH_ERROR_INVALID_CREDENTIALS


def test_login_user_success(monkeypatch):
    user = {
        "id": 9,
        "username": "alice",
        "password_hash": "hashed",
        "auth_version": 4,
        "created_at": _future_datetime(),
    }

    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_user_by_username",
        lambda username: user,
    )
    monkeypatch.setattr(
        auth_service, "verify_password", lambda password, password_hash: True
    )
    monkeypatch.setattr(
        auth_service,
        "_create_session",
        lambda **kwargs: (
            True,
            {
                "session_id": "session-9",
                "refresh_token": "refresh-9",
                "auth_version": 4,
            },
            "",
        ),
    )
    monkeypatch.setattr(
        auth_service,
        "generate_jwt_token",
        lambda **kwargs: "access-token-9",
    )

    success, data, error = auth_service.login_user("alice", "Password123!")

    assert success is True
    assert error == ""
    assert data["access_token"] == "access-token-9"
    assert data["refresh_token"] == "refresh-9"
    assert data["user"]["username"] == "alice"


def test_refresh_session_rejects_inactive_or_unknown_session(monkeypatch):
    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_auth_session_by_refresh_token_hash",
        lambda refresh_token_hash: None,
    )

    success, data, error = auth_service.refresh_session("refresh-token")

    assert success is False
    assert data is None
    assert error == auth_service.AUTH_ERROR_INVALID_REFRESH_TOKEN


def test_refresh_session_rejects_auth_version_mismatch(monkeypatch):
    cleared_session_ids = []
    session = _active_session(session_id="session-20", user_id=20, auth_version=1)

    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_auth_session_by_refresh_token_hash",
        lambda refresh_token_hash: session,
    )
    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_user_by_id",
        lambda user_id: {
            "id": 20,
            "username": "bob20",
            "auth_version": 2,
        },
    )
    monkeypatch.setattr(auth_service, "_get_user_auth_version", lambda user_id: 2)
    monkeypatch.setattr(
        auth_service.redis_client,
        "clear_cached_auth_session",
        lambda session_id: cleared_session_ids.append(session_id),
    )

    success, data, error = auth_service.refresh_session("refresh-token")

    assert success is False
    assert data is None
    assert error == auth_service.AUTH_ERROR_INVALID_REFRESH_TOKEN
    assert cleared_session_ids == ["session-20"]


def test_refresh_session_success(monkeypatch):
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
        "get_auth_session_by_refresh_token_hash",
        lambda refresh_token_hash: session,
    )
    monkeypatch.setattr(
        auth_service.postgres_client, "get_user_by_id", lambda user_id: user
    )
    monkeypatch.setattr(auth_service, "_get_user_auth_version", lambda user_id: 3)
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
        "rotate_auth_session_refresh_token",
        _rotate_auth_session_refresh_token,
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "cache_user_auth_version",
        lambda user_id, auth_version: cached_versions.append((user_id, auth_version)),
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "cache_auth_session",
        lambda session_id, session_data: cached_sessions.append(
            (session_id, session_data)
        ),
    )

    success, data, error = auth_service.refresh_session("old-refresh-token")

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


def test_logout_session_revokes_session_and_clears_cache(monkeypatch):
    cleared_session_ids = []

    monkeypatch.setattr(
        auth_service.postgres_client,
        "revoke_auth_session",
        lambda session_id, reason: True,
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "clear_cached_auth_session",
        lambda session_id: cleared_session_ids.append(session_id),
    )

    assert auth_service.logout_session("session-logout") is True
    assert cleared_session_ids == ["session-logout"]


def test_change_password_rejects_when_old_password_is_invalid(monkeypatch):
    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_user_by_id",
        lambda user_id: {
            "id": user_id,
            "username": "user33",
            "password_hash": "hash",
        },
    )
    monkeypatch.setattr(
        auth_service, "verify_password", lambda password, password_hash: False
    )

    success, data, error = auth_service.change_password(
        33,
        "wrong-old",
        "NewPassword1!",
        "user33",
    )

    assert success is False
    assert data is None
    assert error == "Current password is incorrect"


def test_change_password_rejects_when_new_password_matches_username(monkeypatch):
    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_user_by_id",
        lambda user_id: {
            "id": user_id,
            "username": "user44",
            "password_hash": "hash",
        },
    )
    monkeypatch.setattr(
        auth_service, "verify_password", lambda password, password_hash: True
    )

    success, data, error = auth_service.change_password(44, "old", "user44", "user44")

    assert success is False
    assert data is None
    assert error == "Password cannot be the same as username"


def test_change_password_rejects_when_new_password_matches_current(monkeypatch):
    calls = iter([True, True])

    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_user_by_id",
        lambda user_id: {
            "id": user_id,
            "username": "user55",
            "password_hash": "hash",
        },
    )
    monkeypatch.setattr(
        auth_service,
        "verify_password",
        lambda password, password_hash: next(calls),
    )

    success, data, error = auth_service.change_password(
        55,
        "old-password",
        "old-password",
        "user55",
    )

    assert success is False
    assert data is None
    assert error == "New password must be different from current password"


def test_change_password_success(monkeypatch):
    cached_versions = []
    cached_sessions = []
    cleared_session_groups = []

    calls = iter([True, False])
    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_user_by_id",
        lambda user_id: {
            "id": user_id,
            "username": "user66",
            "password_hash": "current-hash",
        },
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
        "change_password_and_create_session",
        lambda **kwargs: {
            "session_id": "session-66",
            "auth_version": 6,
            "user_id": 66,
            "expires_at": _future_datetime(7200),
            "revoked_at": None,
            "revoked_session_ids": ["old-session-1", "old-session-2"],
        },
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "clear_cached_auth_sessions",
        lambda session_ids: cleared_session_groups.append(session_ids),
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "cache_user_auth_version",
        lambda user_id, auth_version: cached_versions.append((user_id, auth_version)),
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "cache_auth_session",
        lambda session_id, session_data: cached_sessions.append(
            (session_id, session_data)
        ),
    )

    success, data, error = auth_service.change_password(
        66,
        "current-password",
        "NewPassword1!",
        "user66",
        created_ip="192.168.0.66",
        created_user_agent="pytest-agent",
    )

    assert success is True
    assert error == ""
    assert data["message"] == "Password changed successfully"
    assert data["access_token"] == "access-66"
    assert data["refresh_token"] == "refresh-66"
    assert cleared_session_groups == [["old-session-1", "old-session-2"]]
    assert cached_versions == [(66, 6)]
    assert cached_sessions[0][0] == "session-66"


def test_get_user_by_id_returns_serialized_profile(monkeypatch):
    created_at = datetime(2026, 3, 14, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_user_by_id",
        lambda user_id: {
            "id": user_id,
            "username": "user77",
            "created_at": created_at,
        },
    )

    profile = auth_service.get_user_by_id(77)

    assert profile["id"] == 77
    assert profile["username"] == "user77"
    assert profile["created_at"].startswith("2026-03-14T12:00:00")


def test_get_user_by_id_returns_none_on_failure(monkeypatch):
    def _raise_error(user_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(auth_service.postgres_client, "get_user_by_id", _raise_error)

    assert auth_service.get_user_by_id(1) is None


def test_serialize_datetime_handles_naive_and_none_values():
    naive = datetime(2026, 3, 15, 8, 30, 0)

    assert auth_service.serialize_datetime(naive) == "2026-03-15T08:30:00+00:00"
    assert auth_service.serialize_datetime(None) is None


def test_serialize_user_returns_public_fields_with_serialized_datetime():
    created_at = datetime(2026, 3, 15, 8, 45, tzinfo=timezone.utc)

    result = auth_service.serialize_user(
        {
            "id": 900,
            "username": "user900",
            "created_at": created_at,
            "password_hash": "internal-only",
        }
    )

    assert result == {
        "id": 900,
        "username": "user900",
        "created_at": "2026-03-15T08:45:00+00:00",
    }


def test_verify_password_returns_false_for_invalid_hash_payload():
    assert (
        auth_service.verify_password("Password123!", "not-a-valid-bcrypt-hash") is False
    )


def test_verify_jwt_token_rejects_exactly_expired_token():
    now = int(datetime.now(timezone.utc).timestamp())
    expired_token = jwt.encode(
        {
            "type": "access",
            "user_id": 1,
            "username": "edge",
            "session_id": "session-edge-expired",
            "auth_version": 1,
            "exp": now,
        },
        auth_service.JWT_SECRET_KEY,
        algorithm=auth_service.JWT_ALGORITHM,
    )

    assert auth_service.verify_jwt_token(expired_token) is None


def test_verify_jwt_token_accepts_token_with_one_second_remaining():
    now = int(datetime.now(timezone.utc).timestamp())
    nearly_expired_token = jwt.encode(
        {
            "type": "access",
            "user_id": 2,
            "username": "edge2",
            "session_id": "session-edge-remaining",
            "auth_version": 4,
            "exp": now + 1,
        },
        auth_service.JWT_SECRET_KEY,
        algorithm=auth_service.JWT_ALGORITHM,
    )

    payload = auth_service.verify_jwt_token(nearly_expired_token)

    assert payload is not None
    assert payload["user_id"] == 2
    assert payload["auth_version"] == 4


def test_get_refresh_token_expiry_uses_configured_ttl(monkeypatch):
    monkeypatch.setattr(auth_service, "REFRESH_TOKEN_EXPIRATION_SECONDS", 120)
    before = datetime.now(timezone.utc)

    expiry = auth_service.get_refresh_token_expiry()

    after = datetime.now(timezone.utc)
    assert before + timedelta(seconds=119) <= expiry <= after + timedelta(seconds=121)


def test_get_user_auth_version_returns_cached_value_without_db_lookup(monkeypatch):
    monkeypatch.setattr(
        auth_service.redis_client, "get_cached_user_auth_version", lambda user_id: 11
    )

    def _raise_if_called(user_id):
        raise AssertionError("DB lookup should not run when cache has value")

    monkeypatch.setattr(
        auth_service.postgres_client, "get_user_auth_version", _raise_if_called
    )

    assert auth_service._get_user_auth_version(5) == 11


def test_get_user_auth_version_falls_back_to_db_and_caches(monkeypatch):
    cached_versions = []

    monkeypatch.setattr(
        auth_service.redis_client, "get_cached_user_auth_version", lambda user_id: None
    )
    monkeypatch.setattr(
        auth_service.postgres_client, "get_user_auth_version", lambda user_id: 7
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "cache_user_auth_version",
        lambda user_id, auth_version: cached_versions.append((user_id, auth_version)),
    )

    assert auth_service._get_user_auth_version(15) == 7
    assert cached_versions == [(15, 7)]


def test_get_user_auth_version_returns_none_when_cache_and_db_are_empty(monkeypatch):
    cached_versions = []

    monkeypatch.setattr(
        auth_service.redis_client, "get_cached_user_auth_version", lambda user_id: None
    )
    monkeypatch.setattr(
        auth_service.postgres_client, "get_user_auth_version", lambda user_id: None
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "cache_user_auth_version",
        lambda user_id, auth_version: cached_versions.append((user_id, auth_version)),
    )

    assert auth_service._get_user_auth_version(16) is None
    assert cached_versions == []


def test_get_auth_session_returns_cached_session_without_db_lookup(monkeypatch):
    cached_session = _active_session(
        session_id="cached-session", user_id=77, auth_version=2
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "get_cached_auth_session",
        lambda session_id: cached_session,
    )

    def _raise_if_called(session_id):
        raise AssertionError("DB lookup should not run when session cache has value")

    monkeypatch.setattr(
        auth_service.postgres_client, "get_auth_session", _raise_if_called
    )

    assert auth_service._get_auth_session("cached-session") == cached_session


def test_get_auth_session_caches_active_db_session(monkeypatch):
    cached_sessions = []
    db_session = _active_session(session_id="db-session", user_id=88, auth_version=3)

    monkeypatch.setattr(
        auth_service.redis_client, "get_cached_auth_session", lambda session_id: None
    )
    monkeypatch.setattr(
        auth_service.postgres_client, "get_auth_session", lambda session_id: db_session
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "cache_auth_session",
        lambda session_id, session_data: cached_sessions.append(
            (session_id, session_data)
        ),
    )

    assert auth_service._get_auth_session("db-session") == db_session
    assert cached_sessions == [("db-session", db_session)]


def test_get_auth_session_clears_inactive_db_session(monkeypatch):
    cleared_sessions = []
    inactive_session = {
        **_active_session(session_id="expired-session", user_id=89, auth_version=3),
        "expires_at": _future_datetime(-60),
    }

    monkeypatch.setattr(
        auth_service.redis_client, "get_cached_auth_session", lambda session_id: None
    )
    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_auth_session",
        lambda session_id: inactive_session,
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "clear_cached_auth_session",
        lambda session_id: cleared_sessions.append(session_id),
    )

    assert auth_service._get_auth_session("expired-session") == inactive_session
    assert cleared_sessions == ["expired-session"]


def test_validate_access_token_rejects_invalid_user_or_auth_version(monkeypatch):
    monkeypatch.setattr(
        auth_service,
        "verify_jwt_token",
        lambda token: {
            "user_id": "not-an-int",
            "auth_version": "not-an-int",
            "session_id": "session-x",
        },
    )

    assert auth_service.validate_access_token("access-token") is None


def test_validate_access_token_rejects_missing_session_id(monkeypatch):
    monkeypatch.setattr(
        auth_service,
        "verify_jwt_token",
        lambda token: {
            "user_id": 10,
            "auth_version": 2,
        },
    )

    def _raise_if_called(user_id):
        raise AssertionError("Should return before auth-version lookup")

    monkeypatch.setattr(auth_service, "_get_user_auth_version", _raise_if_called)

    assert auth_service.validate_access_token("access-token") is None


def test_validate_access_token_rejects_session_identity_mismatch(monkeypatch):
    payload = {
        "user_id": 10,
        "username": "user10",
        "session_id": "session-10",
        "auth_version": 3,
    }

    monkeypatch.setattr(auth_service, "verify_jwt_token", lambda token: payload)
    monkeypatch.setattr(auth_service, "_get_user_auth_version", lambda user_id: 3)
    monkeypatch.setattr(
        auth_service,
        "_get_auth_session",
        lambda session_id: _active_session(
            session_id="session-10", user_id=999, auth_version=3
        ),
    )

    assert auth_service.validate_access_token("access-token") is None


def test_create_session_returns_internal_error_when_create_fails(monkeypatch):
    monkeypatch.setattr(
        auth_service.uuid, "uuid4", lambda: SimpleNamespace(hex="session-create-fail")
    )
    monkeypatch.setattr(
        auth_service, "generate_refresh_token", lambda: "refresh-create-fail"
    )
    monkeypatch.setattr(
        auth_service, "get_refresh_token_expiry", lambda: _future_datetime(3600)
    )
    monkeypatch.setattr(
        auth_service.postgres_client, "create_auth_session", lambda **kwargs: None
    )

    success, data, error = auth_service._create_session(
        user_id=101,
        auth_version=4,
        created_ip="127.0.0.1",
        created_user_agent="pytest",
    )

    assert success is False
    assert data is None
    assert error == auth_service.AUTH_ERROR_INTERNAL


def test_create_session_success_caches_session_and_auth_version(monkeypatch):
    cached_versions = []
    cached_sessions = []
    session_payload = _active_session(
        session_id="session-create-ok",
        user_id=102,
        auth_version=5,
    )

    monkeypatch.setattr(
        auth_service.uuid, "uuid4", lambda: SimpleNamespace(hex="session-create-ok")
    )
    monkeypatch.setattr(
        auth_service, "generate_refresh_token", lambda: "refresh-create-ok"
    )
    monkeypatch.setattr(
        auth_service, "get_refresh_token_expiry", lambda: _future_datetime(3600)
    )
    monkeypatch.setattr(
        auth_service.postgres_client,
        "create_auth_session",
        lambda **kwargs: session_payload,
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "cache_user_auth_version",
        lambda user_id, auth_version: cached_versions.append((user_id, auth_version)),
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "cache_auth_session",
        lambda session_id, session_data: cached_sessions.append(
            (session_id, session_data)
        ),
    )

    success, data, error = auth_service._create_session(
        user_id=102,
        auth_version=5,
        created_ip="127.0.0.1",
        created_user_agent="pytest",
    )

    assert success is True
    assert error == ""
    assert data == {
        "session_id": "session-create-ok",
        "refresh_token": "refresh-create-ok",
        "auth_version": 5,
    }
    assert cached_versions == [(102, 5)]
    assert cached_sessions == [("session-create-ok", session_payload)]


def test_register_user_returns_internal_error_when_transaction_returns_no_data(
    monkeypatch,
):
    monkeypatch.setattr(
        auth_service.postgres_client, "get_user_by_username", lambda username: None
    )
    monkeypatch.setattr(auth_service, "hash_password", lambda password: "hashed")
    monkeypatch.setattr(
        auth_service.uuid, "uuid4", lambda: SimpleNamespace(hex="register-fail")
    )
    monkeypatch.setattr(
        auth_service, "generate_refresh_token", lambda: "refresh-register"
    )
    monkeypatch.setattr(
        auth_service, "get_refresh_token_expiry", lambda: _future_datetime(3600)
    )
    monkeypatch.setattr(
        auth_service.postgres_client,
        "create_user_with_settings_and_auth_session",
        lambda **kwargs: None,
    )

    success, data, error = auth_service.register_user("new-user", "Password123!")

    assert success is False
    assert data is None
    assert error == auth_service.AUTH_ERROR_INTERNAL


def test_login_user_returns_internal_error_when_session_creation_fails(monkeypatch):
    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_user_by_username",
        lambda username: {
            "id": 404,
            "username": username,
            "password_hash": "hashed",
            "auth_version": 3,
            "created_at": _future_datetime(),
        },
    )
    monkeypatch.setattr(
        auth_service, "verify_password", lambda password, password_hash: True
    )
    monkeypatch.setattr(
        auth_service, "_create_session", lambda **kwargs: (False, None, "session_error")
    )

    success, data, error = auth_service.login_user("edge-user", "Password123!")

    assert success is False
    assert data is None
    assert error == auth_service.AUTH_ERROR_INTERNAL


def test_refresh_session_returns_internal_error_when_session_user_is_missing(
    monkeypatch,
):
    session = _active_session(
        session_id="missing-user-session", user_id=303, auth_version=2
    )

    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_auth_session_by_refresh_token_hash",
        lambda refresh_token_hash: session,
    )
    monkeypatch.setattr(
        auth_service.postgres_client, "get_user_by_id", lambda user_id: None
    )

    success, data, error = auth_service.refresh_session("refresh-token")

    assert success is False
    assert data is None
    assert error == auth_service.AUTH_ERROR_INTERNAL


def test_refresh_session_returns_internal_error_when_auth_version_unavailable(
    monkeypatch,
):
    session = _active_session(
        session_id="missing-auth-version", user_id=304, auth_version=2
    )

    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_auth_session_by_refresh_token_hash",
        lambda refresh_token_hash: session,
    )
    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_user_by_id",
        lambda user_id: {
            "id": 304,
            "username": "user304",
            "auth_version": 2,
        },
    )
    monkeypatch.setattr(auth_service, "_get_user_auth_version", lambda user_id: None)

    success, data, error = auth_service.refresh_session("refresh-token")

    assert success is False
    assert data is None
    assert error == auth_service.AUTH_ERROR_INTERNAL


def test_refresh_session_rejects_when_refresh_rotation_fails(monkeypatch):
    cleared_sessions = []
    session = _active_session(
        session_id="rotate-fail-session", user_id=305, auth_version=4
    )

    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_auth_session_by_refresh_token_hash",
        lambda refresh_token_hash: session,
    )
    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_user_by_id",
        lambda user_id: {
            "id": 305,
            "username": "user305",
            "auth_version": 4,
        },
    )
    monkeypatch.setattr(auth_service, "_get_user_auth_version", lambda user_id: 4)
    monkeypatch.setattr(
        auth_service, "generate_refresh_token", lambda: "new-refresh-token"
    )
    monkeypatch.setattr(
        auth_service, "get_refresh_token_expiry", lambda: _future_datetime(3600)
    )
    monkeypatch.setattr(
        auth_service.postgres_client,
        "rotate_auth_session_refresh_token",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "clear_cached_auth_session",
        lambda session_id: cleared_sessions.append(session_id),
    )

    success, data, error = auth_service.refresh_session("refresh-token")

    assert success is False
    assert data is None
    assert error == auth_service.AUTH_ERROR_INVALID_REFRESH_TOKEN
    assert cleared_sessions == ["rotate-fail-session"]


def test_change_password_returns_user_not_found_when_user_missing(monkeypatch):
    monkeypatch.setattr(
        auth_service.postgres_client, "get_user_by_id", lambda user_id: None
    )

    success, data, error = auth_service.change_password(
        999,
        "old-password",
        "NewPassword1!",
        "user999",
    )

    assert success is False
    assert data is None
    assert error == "User not found"


def test_change_password_returns_error_when_password_update_transaction_fails(
    monkeypatch,
):
    calls = iter([True, False])

    monkeypatch.setattr(
        auth_service.postgres_client,
        "get_user_by_id",
        lambda user_id: {
            "id": user_id,
            "username": "user330",
            "password_hash": "hash330",
        },
    )
    monkeypatch.setattr(
        auth_service,
        "verify_password",
        lambda password, password_hash: next(calls),
    )
    monkeypatch.setattr(auth_service, "hash_password", lambda password: "new-hash-330")
    monkeypatch.setattr(
        auth_service.uuid, "uuid4", lambda: SimpleNamespace(hex="session-330")
    )
    monkeypatch.setattr(auth_service, "generate_refresh_token", lambda: "refresh-330")
    monkeypatch.setattr(
        auth_service, "get_refresh_token_expiry", lambda: _future_datetime(3600)
    )
    monkeypatch.setattr(
        auth_service.postgres_client,
        "change_password_and_create_session",
        lambda **kwargs: None,
    )

    success, data, error = auth_service.change_password(
        330,
        "old-password",
        "NewPassword1!",
        "user330",
    )

    assert success is False
    assert data is None
    assert error == "Failed to update password"


def test_get_user_by_id_returns_none_when_user_not_found(monkeypatch):
    monkeypatch.setattr(
        auth_service.postgres_client, "get_user_by_id", lambda user_id: None
    )

    assert auth_service.get_user_by_id(404) is None
