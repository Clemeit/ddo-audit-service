"""Refresh-token rotation robustness: grace window, reuse detection, absolute lifetime,
database-error handling, plus the other session-lifecycle helpers in services.auth."""

from datetime import datetime, timedelta, timezone

import pytest

from conftest import _amock

import services.auth as auth_service


NOW = datetime.now(timezone.utc)


def _session(**overrides):
    session = {
        "session_id": "sess-1",
        "user_id": 7,
        "auth_version": 2,
        "created_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=29),
        "revoked_at": None,
        "refresh_token_hash": auth_service.hash_refresh_token("current-token"),
        "previous_refresh_token_hash": None,
        "refresh_rotated_at": None,
        "matched": "current",
    }
    session.update(overrides)
    return session


@pytest.fixture
def backend(monkeypatch):
    """Stub postgres/redis and record side effects."""
    state = {
        "session": _session(),
        "reread": None,
        "rotate_result": "rotated",
        "rotate_calls": [],
        "revoked": [],
        "cache_cleared": [],
        "user": {"id": 7, "username": "alice", "auth_version": 2},
        "auth_version": 2,
    }

    def _lookup(refresh_token_hash):
        return state["session"]

    def _rotate(**kwargs):
        state["rotate_calls"].append(kwargs)
        if state["rotate_result"] == "rotated":
            return {**state["session"], "expires_at": kwargs["expires_at"]}
        return None

    pg = auth_service.postgres_client
    monkeypatch.setattr(pg, "async_get_auth_session_by_refresh_token_hash", _amock(_lookup))
    monkeypatch.setattr(pg, "async_rotate_auth_session_refresh_token", _amock(_rotate))
    monkeypatch.setattr(
        pg,
        "async_get_auth_session",
        _amock(lambda session_id, raise_on_error=False: state["reread"]),
    )
    monkeypatch.setattr(pg, "async_get_user_by_id", _amock(lambda user_id: state["user"]))
    monkeypatch.setattr(
        pg,
        "async_revoke_auth_session",
        _amock(lambda session_id, reason: state["revoked"].append((session_id, reason)) or True),
    )
    monkeypatch.setattr(
        auth_service,
        "_async_get_user_auth_version",
        _amock(lambda user_id: state["auth_version"]),
    )
    rc = auth_service.redis_client
    monkeypatch.setattr(
        rc,
        "async_clear_cached_auth_session",
        _amock(lambda session_id: state["cache_cleared"].append(session_id)),
    )
    monkeypatch.setattr(rc, "async_cache_auth_session", _amock(lambda *a: None))
    monkeypatch.setattr(rc, "async_cache_user_auth_version", _amock(lambda *a: None))
    monkeypatch.setattr(auth_service, "generate_refresh_token", lambda: "next-token")
    return state


def test_current_token_rotates_and_returns_cookie_lifetime(backend, run_async):
    success, data, error = run_async(auth_service.async_refresh_session("current-token"))

    assert success is True and error == ""
    assert data["refresh_token"] == "next-token"
    # Sliding 30-day window, session is 1 day old: well under the 90-day cap.
    assert abs(data["refresh_expires_in"] - auth_service.REFRESH_TOKEN_EXPIRATION_SECONDS) < 5


def test_previous_token_inside_grace_gets_access_token_without_rotation(
    backend, run_async
):
    backend["session"] = _session(
        matched="previous",
        previous_refresh_token_hash=auth_service.hash_refresh_token("old-token"),
        refresh_rotated_at=NOW - timedelta(seconds=5),
    )

    success, data, error = run_async(auth_service.async_refresh_session("old-token"))

    assert success is True
    assert data["access_token"]
    assert "refresh_token" not in data  # endpoint must leave the cookie alone
    assert backend["rotate_calls"] == []
    assert backend["revoked"] == []


def test_previous_token_after_grace_revokes_session(backend, run_async):
    backend["session"] = _session(
        matched="previous",
        refresh_rotated_at=NOW
        - timedelta(seconds=auth_service.REFRESH_REUSE_GRACE_SECONDS + 30),
    )

    success, data, error = run_async(auth_service.async_refresh_session("old-token"))

    assert success is False
    assert error == auth_service.AUTH_ERROR_INVALID_REFRESH_TOKEN
    assert data == {"reason": auth_service.REFRESH_FAILURE_REVOKED}
    assert backend["revoked"] == [("sess-1", auth_service.REVOKE_REASON_REFRESH_REUSE)]
    assert backend["cache_cleared"] == ["sess-1"]


def test_previous_token_on_revoked_session_is_revoked_not_reuse(backend, run_async):
    backend["session"] = _session(
        matched="previous",
        revoked_at=NOW - timedelta(minutes=1),
        refresh_rotated_at=NOW - timedelta(seconds=5),
    )

    success, data, _ = run_async(auth_service.async_refresh_session("old-token"))

    assert success is False
    assert data == {"reason": auth_service.REFRESH_FAILURE_REVOKED}
    assert backend["revoked"] == []


def test_lost_compare_and_set_is_treated_as_concurrent_refresh(backend, run_async):
    """Two tabs send the same cookie; this request finds the session first but the other
    one rotates it before our UPDATE lands. We must answer 200, not log the user out."""
    backend["rotate_result"] = None
    backend["reread"] = _session(
        refresh_token_hash=auth_service.hash_refresh_token("winner-token"),
        previous_refresh_token_hash=auth_service.hash_refresh_token("current-token"),
        refresh_rotated_at=NOW,
    )

    success, data, error = run_async(auth_service.async_refresh_session("current-token"))

    assert success is True and error == ""
    assert "refresh_token" not in data
    assert backend["revoked"] == []


def test_lost_compare_and_set_against_revocation_reports_revoked(backend, run_async):
    backend["rotate_result"] = None
    backend["reread"] = _session(revoked_at=NOW)

    success, data, _ = run_async(auth_service.async_refresh_session("current-token"))

    assert success is False
    assert data == {"reason": auth_service.REFRESH_FAILURE_REVOKED}


def test_expired_session_reports_expired(backend, run_async):
    backend["session"] = _session(expires_at=NOW - timedelta(seconds=1))

    success, data, _ = run_async(auth_service.async_refresh_session("current-token"))

    assert success is False
    assert data == {"reason": auth_service.REFRESH_FAILURE_EXPIRED}


def test_absolute_lifetime_caps_the_sliding_expiry(backend, run_async):
    lifetime = timedelta(seconds=auth_service.SESSION_ABSOLUTE_LIFETIME_SECONDS)
    backend["session"] = _session(created_at=NOW - lifetime + timedelta(days=2))

    success, data, _ = run_async(auth_service.async_refresh_session("current-token"))

    assert success is True
    expires_at = backend["rotate_calls"][0]["expires_at"]
    assert expires_at <= NOW + timedelta(days=2, seconds=5)
    assert data["refresh_expires_in"] <= 2 * 24 * 3600 + 5


def test_session_past_absolute_lifetime_is_expired_without_rotating(backend, run_async):
    lifetime = timedelta(seconds=auth_service.SESSION_ABSOLUTE_LIFETIME_SECONDS)
    backend["session"] = _session(created_at=NOW - lifetime - timedelta(minutes=1))

    success, data, _ = run_async(auth_service.async_refresh_session("current-token"))

    assert success is False
    assert data == {"reason": auth_service.REFRESH_FAILURE_EXPIRED}
    assert backend["rotate_calls"] == []


def test_database_error_is_internal_not_invalid(monkeypatch, run_async):
    """An outage must never look like a bad token (that would log users out)."""

    async def _boom(refresh_token_hash):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_auth_session_by_refresh_token_hash",
        _boom,
    )

    success, data, error = run_async(auth_service.async_refresh_session("any"))

    assert success is False
    assert error == auth_service.AUTH_ERROR_INTERNAL


def test_login_unknown_user_still_spends_a_bcrypt_check(monkeypatch, run_async):
    checks = []
    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_user_by_username",
        _amock(lambda username: None),
    )
    monkeypatch.setattr(auth_service, "_dummy_password_hash", "dummy-hash")
    monkeypatch.setattr(
        auth_service,
        "verify_password",
        lambda password, password_hash: checks.append(password_hash) or False,
    )

    success, _, error = run_async(auth_service.async_login_user("ghost", "pw"))

    assert success is False
    assert error == auth_service.AUTH_ERROR_INVALID_CREDENTIALS
    assert checks == ["dummy-hash"]


def test_bcrypt_runs_off_the_event_loop(monkeypatch, run_async):
    import threading

    loop_thread = threading.get_ident()
    seen = []
    monkeypatch.setattr(
        auth_service,
        "hash_password",
        lambda password: seen.append(threading.get_ident()) or "h",
    )

    run_async(auth_service.async_hash_password("pw"))

    assert seen and seen[0] != loop_thread


def test_delete_account_checks_password(monkeypatch, run_async):
    deleted = []
    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_user_by_id",
        _amock(lambda user_id: {"id": user_id, "password_hash": "hash"}),
    )
    monkeypatch.setattr(
        auth_service, "verify_password", lambda password, h: password == "right"
    )
    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_delete_user_account",
        _amock(lambda user_id: deleted.append(user_id) or {"deleted": True, "session_ids": []}),
    )
    monkeypatch.setattr(
        auth_service.redis_client, "async_clear_cached_auth_sessions", _amock(lambda ids: None)
    )
    monkeypatch.setattr(
        auth_service.redis_client,
        "async_clear_cached_user_auth_version",
        _amock(lambda user_id: None),
    )

    wrong = run_async(auth_service.async_delete_user_account(3, password="wrong"))
    missing = run_async(
        auth_service.async_delete_user_account(3, require_password=True)
    )
    right = run_async(auth_service.async_delete_user_account(3, password="right"))

    assert wrong == (False, auth_service.AUTH_ERROR_INVALID_CREDENTIALS)
    assert missing == (False, auth_service.AUTH_ERROR_PASSWORD_REQUIRED)
    assert right == (True, "")
    assert deleted == [3]


def test_logout_by_refresh_token(monkeypatch, run_async):
    revoked = []
    sessions = {auth_service.hash_refresh_token("tok"): {"session_id": "s-5"}}
    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_auth_session_by_refresh_token_hash",
        _amock(lambda h: sessions.get(h)),
    )
    monkeypatch.setattr(
        auth_service, "async_logout_session", _amock(lambda sid: revoked.append(sid) or True)
    )

    assert run_async(auth_service.async_logout_by_refresh_token("tok")) is True
    assert run_async(auth_service.async_logout_by_refresh_token("other")) is False
    assert revoked == ["s-5"]


def test_logout_by_refresh_token_reports_db_error(monkeypatch, run_async):
    async def _boom(h):
        raise RuntimeError("db down")

    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_get_auth_session_by_refresh_token_hash",
        _boom,
    )

    assert run_async(auth_service.async_logout_by_refresh_token("tok")) is None


class _FakeRedis:
    def __init__(self):
        self.keys = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.keys:
            return None
        self.keys[key] = value
        return True


def test_purge_runs_once_per_interval_across_workers(monkeypatch, run_async):
    fake_redis = _FakeRedis()
    purges = []
    monkeypatch.setattr(
        auth_service.redis_client, "get_async_redis_client", _amock(lambda: fake_redis)
    )
    monkeypatch.setattr(
        auth_service.postgres_client,
        "async_purge_dead_auth_sessions",
        _amock(lambda retention: purges.append(retention) or 3),
    )

    first = run_async(auth_service.async_purge_dead_auth_sessions_once())
    second = run_async(auth_service.async_purge_dead_auth_sessions_once())

    assert first == 3
    assert second is None
    assert purges == [auth_service.AUTH_SESSION_PURGE_RETENTION_SECONDS]
