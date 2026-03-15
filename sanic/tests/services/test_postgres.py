from contextlib import contextmanager, asynccontextmanager
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock

import pytest

import services.postgres as postgres_service
from services.postgres import OnConflict


def _mock_connection_and_cursor():
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value.__enter__.return_value = cursor
    conn.cursor.return_value.__exit__.return_value = None
    return conn, cursor


def test_initialize_creates_connection_pool_and_runs_health_check(monkeypatch):
    manager = postgres_service.PostgresConnectionManager()
    pool_instance = MagicMock()
    captured = {}

    def _fake_pool_factory(minconn, maxconn, **kwargs):
        captured["minconn"] = minconn
        captured["maxconn"] = maxconn
        captured["kwargs"] = kwargs
        return pool_instance

    monkeypatch.setattr(
        postgres_service.pool, "SimpleConnectionPool", _fake_pool_factory
    )
    monkeypatch.setattr(manager, "health_check", lambda: True)

    manager.initialize()

    assert manager._is_initialized is True
    assert manager._connection_pool is pool_instance
    assert captured["minconn"] == postgres_service.POSTGRES_MIN_CONN
    assert captured["maxconn"] == postgres_service.POSTGRES_MAX_CONN
    assert captured["kwargs"] == postgres_service.DB_CONFIG


def test_initialize_is_noop_when_already_initialized(monkeypatch):
    manager = postgres_service.PostgresConnectionManager()
    manager._is_initialized = True

    def _should_not_run(*args, **kwargs):
        raise AssertionError("Pool creation should not run when already initialized")

    monkeypatch.setattr(postgres_service.pool, "SimpleConnectionPool", _should_not_run)

    manager.initialize()


def test_initialize_resets_state_and_raises_on_pool_error(monkeypatch):
    manager = postgres_service.PostgresConnectionManager()

    def _raise_pool_error(*args, **kwargs):
        raise RuntimeError("pool unavailable")

    monkeypatch.setattr(
        postgres_service.pool, "SimpleConnectionPool", _raise_pool_error
    )

    with pytest.raises(RuntimeError, match="pool unavailable"):
        manager.initialize()

    assert manager._is_initialized is False
    assert manager._connection_pool is None


def test_get_connection_raises_when_not_initialized():
    manager = postgres_service.PostgresConnectionManager()

    with pytest.raises(RuntimeError, match="not initialized"):
        with manager.get_connection():
            pass


def test_get_connection_sets_timeout_and_tracks_stats():
    manager = postgres_service.PostgresConnectionManager()
    pool_instance = MagicMock()
    conn, cursor = _mock_connection_and_cursor()
    manager._is_initialized = True
    manager._connection_pool = pool_instance
    pool_instance.getconn.return_value = conn

    with manager.get_connection() as returned:
        assert returned is conn

    assert conn.autocommit is False
    cursor.execute.assert_not_called()
    pool_instance.putconn.assert_called_once_with(conn)
    assert manager._connection_stats["total_connections_requested"] == 1
    assert manager._connection_stats["total_connections_returned"] == 1
    assert manager._connection_stats["current_connections_in_use"] == 0
    assert manager._connection_stats["peak_connections_in_use"] == 1
    assert manager._connection_stats["connection_errors"] == 0


def test_get_connection_handles_none_connection_as_error():
    manager = postgres_service.PostgresConnectionManager()
    pool_instance = MagicMock()
    manager._is_initialized = True
    manager._connection_pool = pool_instance
    pool_instance.getconn.return_value = None

    with pytest.raises(ConnectionError, match="Failed to get connection"):
        with manager.get_connection():
            pass

    # One increment before raising, then another in the except block.
    assert manager._connection_stats["connection_errors"] == 2
    assert manager._connection_stats["total_connections_returned"] == 0


def test_get_connection_rolls_back_when_exception_raised_inside_context():
    manager = postgres_service.PostgresConnectionManager()
    pool_instance = MagicMock()
    conn, _ = _mock_connection_and_cursor()
    manager._is_initialized = True
    manager._connection_pool = pool_instance
    pool_instance.getconn.return_value = conn

    with pytest.raises(ValueError, match="boom"):
        with manager.get_connection():
            raise ValueError("boom")

    conn.rollback.assert_called_once()
    pool_instance.putconn.assert_called_once_with(conn)
    assert manager._connection_stats["connection_errors"] == 1


def test_get_connection_counts_putconn_error_without_raising():
    manager = postgres_service.PostgresConnectionManager()
    pool_instance = MagicMock()
    conn, _ = _mock_connection_and_cursor()
    manager._is_initialized = True
    manager._connection_pool = pool_instance
    pool_instance.getconn.return_value = conn
    pool_instance.putconn.side_effect = RuntimeError("return failed")

    with manager.get_connection() as returned:
        assert returned is conn

    assert manager._connection_stats["connection_errors"] == 1
    assert manager._connection_stats["total_connections_returned"] == 0


def test_get_cursor_commits_when_commit_true(monkeypatch):
    manager = postgres_service.PostgresConnectionManager()
    conn, cursor = _mock_connection_and_cursor()

    @contextmanager
    def _connection_context():
        yield conn

    monkeypatch.setattr(manager, "get_connection", _connection_context)

    with manager.get_cursor(commit=True) as returned_cursor:
        assert returned_cursor is cursor

    conn.commit.assert_called_once()
    conn.rollback.assert_not_called()


def test_get_cursor_rolls_back_and_reraises_on_cursor_error(monkeypatch):
    manager = postgres_service.PostgresConnectionManager()
    conn, _ = _mock_connection_and_cursor()

    @contextmanager
    def _connection_context():
        yield conn

    monkeypatch.setattr(manager, "get_connection", _connection_context)

    with pytest.raises(RuntimeError, match="cursor failed"):
        with manager.get_cursor(commit=True):
            raise RuntimeError("cursor failed")

    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()


def test_execute_query_returns_fetch_one_result(monkeypatch):
    manager = postgres_service.PostgresConnectionManager()
    cursor = MagicMock()
    cursor.fetchone.return_value = ("row",)

    @contextmanager
    def _cursor_context(commit=True):
        assert commit is False
        yield cursor

    monkeypatch.setattr(manager, "get_cursor", _cursor_context)

    result = manager.execute_query(
        "SELECT * FROM table WHERE id = %s",
        params=(1,),
        fetch_one=True,
        commit=False,
    )

    cursor.execute.assert_called_once_with("SELECT * FROM table WHERE id = %s", (1,))
    assert result == ("row",)


def test_execute_query_returns_fetch_all_and_rowcount(monkeypatch):
    manager = postgres_service.PostgresConnectionManager()
    cursor = MagicMock()
    cursor.fetchall.return_value = [(1,), (2,)]
    cursor.rowcount = 3

    @contextmanager
    def _cursor_context(commit=True):
        yield cursor

    monkeypatch.setattr(manager, "get_cursor", _cursor_context)

    rows = manager.execute_query("SELECT * FROM table", fetch_all=True)
    count = manager.execute_query("DELETE FROM table")

    assert rows == [(1,), (2,)]
    assert count == 3


def test_bulk_insert_returns_zero_for_empty_data():
    manager = postgres_service.PostgresConnectionManager()

    assert manager.bulk_insert("characters", ["id"], []) == 0


def test_bulk_insert_executes_many_and_returns_rowcount(monkeypatch):
    manager = postgres_service.PostgresConnectionManager()
    cursor = MagicMock()
    cursor.rowcount = 2

    @contextmanager
    def _cursor_context(commit=True):
        yield cursor

    monkeypatch.setattr(manager, "get_cursor", _cursor_context)

    data = [(1, "Alice"), (2, "Bob")]
    result = manager.bulk_insert(
        table="characters",
        columns=["id", "name"],
        data=data,
        on_conflict=OnConflict(conflict_columns=["id"], action="nothing"),
    )

    assert result == 2
    assert cursor.executemany.call_count == 1
    assert cursor.executemany.call_args[0][1] == data


def test_bulk_insert_reraises_on_executemany_error(monkeypatch):
    manager = postgres_service.PostgresConnectionManager()
    cursor = MagicMock()
    cursor.executemany.side_effect = RuntimeError("insert failed")

    @contextmanager
    def _cursor_context(commit=True):
        yield cursor

    monkeypatch.setattr(manager, "get_cursor", _cursor_context)

    with pytest.raises(RuntimeError, match="insert failed"):
        manager.bulk_insert(
            table="characters",
            columns=["id"],
            data=[(1,)],
        )


def test_execute_transaction_handles_fetch_modes_and_rowcount(monkeypatch):
    manager = postgres_service.PostgresConnectionManager()
    cursor = MagicMock()
    cursor.fetchone.return_value = ("one",)
    cursor.fetchall.return_value = [("a",), ("b",)]

    def _execute(query, params):
        if query.startswith("UPDATE"):
            cursor.rowcount = 3

    cursor.execute.side_effect = _execute

    @contextmanager
    def _cursor_context(commit=True):
        yield cursor

    monkeypatch.setattr(manager, "get_cursor", _cursor_context)

    results = manager.execute_transaction(
        [
            {"query": "SELECT 1", "params": (), "fetch": "one"},
            {"query": "SELECT 2", "params": (), "fetch": "all"},
            {"query": "UPDATE x SET y = %s", "params": (1,)},
        ]
    )

    assert results == [
        ("one",),
        [("a",), ("b",)],
        3,
    ]


def test_health_check_returns_true_on_select_one(monkeypatch):
    manager = postgres_service.PostgresConnectionManager()
    conn, cursor = _mock_connection_and_cursor()
    cursor.fetchone.return_value = (1,)

    @contextmanager
    def _connection_context():
        yield conn

    monkeypatch.setattr(manager, "get_connection", _connection_context)

    assert manager.health_check() is True
    cursor.execute.assert_called_once_with("SELECT 1")


def test_health_check_returns_false_on_exception(monkeypatch):
    manager = postgres_service.PostgresConnectionManager()

    @contextmanager
    def _connection_context():
        raise RuntimeError("db down")
        yield

    monkeypatch.setattr(manager, "get_connection", _connection_context)

    assert manager.health_check() is False


def test_get_pool_stats_returns_error_when_pool_not_initialized():
    manager = postgres_service.PostgresConnectionManager()

    assert manager.get_pool_stats() == {"error": "Pool not initialized"}


def test_get_pool_stats_returns_runtime_and_pool_metrics():
    manager = postgres_service.PostgresConnectionManager()
    manager._is_initialized = True
    manager._connection_pool = SimpleNamespace(
        _pool=[object(), object(), object()],
        _used={1: object()},
    )
    manager._connection_stats.update(
        {
            "total_connections_requested": 20,
            "total_connections_returned": 19,
            "current_connections_in_use": 1,
            "peak_connections_in_use": 4,
            "connection_errors": 2,
            "last_reset_time": datetime.now() - timedelta(seconds=10),
        }
    )

    stats = manager.get_pool_stats()

    assert stats["initialized"] is True
    assert stats["pool_total_connections"] == 3
    assert stats["pool_available_connections"] == 3
    assert stats["pool_used_connections"] == 1
    assert stats["runtime_stats"]["total_connections_requested"] == 20
    assert stats["runtime_stats"]["connection_errors"] == 2
    assert stats["runtime_stats"]["uptime_seconds"] >= 0
    assert stats["runtime_stats"]["requests_per_second"] >= 0


# ============================
# Async helper tests (Phase 1)
# ============================


def _mock_async_cursor():
    """Create a mock async cursor and context manager for get_async_dict_cursor."""
    cursor = AsyncMock()
    cursor.rowcount = 0

    @asynccontextmanager
    async def _fake_cursor(commit=True):
        yield cursor

    return cursor, _fake_cursor


def test_async_execute_many_returns_rowcount(monkeypatch, run_async):
    cursor, fake_ctx = _mock_async_cursor()
    cursor.rowcount = 5

    monkeypatch.setattr(postgres_service, "get_async_dict_cursor", fake_ctx)

    result = run_async(
        postgres_service.async_execute_many(
            "INSERT INTO t (a) VALUES (%s)", [(1,), (2,), (3,), (4,), (5,)]
        )
    )

    assert result == 5
    cursor.executemany.assert_awaited_once()
    call_args = cursor.executemany.call_args
    assert call_args[0][1] == [(1,), (2,), (3,), (4,), (5,)]


def test_async_bulk_insert_returns_zero_for_empty_data(run_async):
    result = run_async(postgres_service.async_bulk_insert("characters", ["id"], []))
    assert result == 0


def test_async_bulk_insert_executes_with_conflict(monkeypatch, run_async):
    cursor, fake_ctx = _mock_async_cursor()
    cursor.rowcount = 2

    monkeypatch.setattr(postgres_service, "get_async_dict_cursor", fake_ctx)

    data = [(1, "Alice"), (2, "Bob")]
    result = run_async(
        postgres_service.async_bulk_insert(
            table="characters",
            columns=["id", "name"],
            data=data,
            on_conflict=OnConflict(conflict_columns=["id"], action="nothing"),
        )
    )

    assert result == 2
    cursor.executemany.assert_awaited_once()
    call_args = cursor.executemany.call_args
    # Verify the data was passed correctly
    assert call_args[0][1] == data


def test_async_bulk_insert_without_conflict(monkeypatch, run_async):
    cursor, fake_ctx = _mock_async_cursor()
    cursor.rowcount = 3

    monkeypatch.setattr(postgres_service, "get_async_dict_cursor", fake_ctx)

    data = [(1,), (2,), (3,)]
    result = run_async(
        postgres_service.async_bulk_insert(
            table="items",
            columns=["id"],
            data=data,
        )
    )

    assert result == 3
    cursor.executemany.assert_awaited_once()


def test_async_pool_default_max_size_is_twenty():
    """Verify the async pool max size default was bumped to 20."""
    assert postgres_service.POSTGRES_ASYNC_MAX_CONN == 20


def test_get_async_dict_cursor_raises_when_pool_not_initialized(monkeypatch, run_async):
    monkeypatch.setattr(postgres_service, "_async_pool", None)

    with pytest.raises(RuntimeError, match="Async Postgres pool not initialized"):
        run_async(postgres_service.async_execute_many("SELECT 1", []))


def test_on_conflict_rejects_invalid_action():
    with pytest.raises(ValueError, match="must be 'nothing' or 'update'"):
        OnConflict(conflict_columns=["id"], action="drop")


def test_on_conflict_update_requires_columns_or_expressions():
    with pytest.raises(ValueError, match="requires at least one"):
        OnConflict(conflict_columns=["id"], action="update")


# ============================
# Async character query tests (Phase 2a)
# ============================


def _character_row(
    id=1,
    name="TestChar",
    gender="Male",
    race="Human",
    total_level=20,
    classes=None,
    location_id=100,
    guild_name="TestGuild",
    server_name="Argonnessen",
    home_server_name="Argonnessen",
    is_anonymous=False,
    last_update=datetime(2026, 3, 15, 12, 0, 0),
    last_save=datetime(2026, 3, 15, 12, 0, 0),
):
    """Create a dict row simulating a psycopg3 dict_row result."""
    return {
        "id": id,
        "name": name,
        "gender": gender,
        "race": race,
        "total_level": total_level,
        "classes": classes,
        "location_id": location_id,
        "guild_name": guild_name,
        "server_name": server_name,
        "home_server_name": home_server_name,
        "is_anonymous": is_anonymous,
        "last_update": last_update,
        "last_save": last_save,
    }


def test_build_character_from_dict_row_maps_all_fields():
    row = _character_row(id=42, name="Hero", server_name="Thelanis")
    char = postgres_service._build_character_from_dict_row(row)

    assert char.id == 42
    assert char.name == "Hero"
    assert char.server_name == "Thelanis"
    assert char.last_update == "2026-03-15T12:00:00Z"
    assert char.last_save == "2026-03-15T12:00:00Z"


def test_build_character_from_dict_row_handles_none_datetimes():
    row = _character_row(last_update=None, last_save=None)
    char = postgres_service._build_character_from_dict_row(row)

    assert char.last_update == ""
    assert char.last_save == ""


def test_async_get_character_by_id_returns_character(monkeypatch, run_async):
    cursor, fake_ctx = _mock_async_cursor()
    cursor.fetchone.return_value = _character_row(id=7, name="Finder")

    monkeypatch.setattr(postgres_service, "get_async_dict_cursor", fake_ctx)

    result = run_async(postgres_service.async_get_character_by_id(7))

    assert result is not None
    assert result.id == 7
    assert result.name == "Finder"
    cursor.execute.assert_awaited_once()


def test_async_get_character_by_id_returns_none_when_missing(monkeypatch, run_async):
    cursor, fake_ctx = _mock_async_cursor()
    cursor.fetchone.return_value = None

    monkeypatch.setattr(postgres_service, "get_async_dict_cursor", fake_ctx)

    result = run_async(postgres_service.async_get_character_by_id(999))

    assert result is None


def test_async_get_characters_by_ids_returns_list(monkeypatch, run_async):
    cursor, fake_ctx = _mock_async_cursor()
    cursor.fetchall.return_value = [
        _character_row(id=1, name="One"),
        _character_row(id=2, name="Two"),
    ]

    monkeypatch.setattr(postgres_service, "get_async_dict_cursor", fake_ctx)

    result = run_async(postgres_service.async_get_characters_by_ids([1, 2]))

    assert len(result) == 2
    assert result[0].id == 1
    assert result[1].name == "Two"


def test_async_get_characters_by_ids_returns_empty_for_empty_input(run_async):
    result = run_async(postgres_service.async_get_characters_by_ids([]))
    assert result == []


def test_async_get_character_by_name_and_server_returns_character(
    monkeypatch, run_async
):
    cursor, fake_ctx = _mock_async_cursor()
    cursor.fetchone.return_value = _character_row(
        id=5, name="Namefind", server_name="Ghallanda"
    )

    monkeypatch.setattr(postgres_service, "get_async_dict_cursor", fake_ctx)

    result = run_async(
        postgres_service.async_get_character_by_name_and_server("Namefind", "Ghallanda")
    )

    assert result is not None
    assert result.id == 5
    assert result.server_name == "Ghallanda"


def test_async_get_characters_by_name_returns_list(monkeypatch, run_async):
    cursor, fake_ctx = _mock_async_cursor()
    cursor.fetchall.return_value = [
        _character_row(id=10, name="Common", server_name="Argonnessen"),
        _character_row(id=11, name="Common", server_name="Thelanis"),
    ]

    monkeypatch.setattr(postgres_service, "get_async_dict_cursor", fake_ctx)

    result = run_async(postgres_service.async_get_characters_by_name("Common"))

    assert len(result) == 2
    assert all(c.name == "Common" for c in result)


def test_async_get_character_ids_by_server_and_guild_returns_ids(
    monkeypatch, run_async
):
    cursor, fake_ctx = _mock_async_cursor()
    cursor.fetchall.return_value = [{"id": 1}, {"id": 2}, {"id": 3}]

    monkeypatch.setattr(postgres_service, "get_async_dict_cursor", fake_ctx)

    result = run_async(
        postgres_service.async_get_character_ids_by_server_and_guild(
            "Argonnessen", "TestGuild"
        )
    )

    assert result == [1, 2, 3]


def test_async_add_character_activity_skips_empty_list(monkeypatch, run_async):
    cursor, fake_ctx = _mock_async_cursor()
    monkeypatch.setattr(postgres_service, "get_async_dict_cursor", fake_ctx)

    run_async(postgres_service.async_add_character_activity([]))

    cursor.executemany.assert_not_awaited()
