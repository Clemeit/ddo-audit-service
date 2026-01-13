import json
import os
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from time import time
from typing import Optional, Generator

from constants.activity import CharacterActivityType
from models.character import (
    Character,
    CharacterActivitySummary,
    CharacterQuestActivity,
)
from models.game import PopulationDataPoint, PopulationPointInTime
from models.redis import ServerInfo, ServerInfoDict
from models.service import News, PageMessage, FeedbackRequest, LogRequest
from psycopg2 import pool, Error as PostgresError  # type: ignore
import psycopg2.extras  # type: ignore
import psycopg2.sql  # type: ignore
from constants.activity import (
    MAX_CHARACTER_ACTIVITY_READ_LENGTH,
    MAX_CHARACTER_AGG_ACTIVITY_READ_LENGTH,
    MAX_CHARACTER_ACTIVITY_READ_HISTORY,
)

from models.quest import Quest
from models.area import Area
from models.quest_session import QuestSession, QuestAnalytics

from utils.areas import get_valid_area_ids
from utils.time import datetime_to_datetime_string

# Setup logging
logger = logging.getLogger(__name__)

# PostgreSQL configuration with defaults
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "ddo_audit")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
POSTGRES_MIN_CONN = int(os.getenv("POSTGRES_MIN_CONN", "1"))
POSTGRES_MAX_CONN = int(os.getenv("POSTGRES_MAX_CONN", "20"))
POSTGRES_CONNECT_TIMEOUT = int(os.getenv("POSTGRES_CONNECT_TIMEOUT", "10"))
POSTGRES_COMMAND_TIMEOUT = int(os.getenv("POSTGRES_COMMAND_TIMEOUT", "30"))
POSTGRES_APPLICATION_NAME = os.getenv("POSTGRES_APPLICATION_NAME", "ddo-audit-service")

# Connection pool configuration
DB_CONFIG = {
    "dbname": POSTGRES_DB,
    "host": POSTGRES_HOST,
    "port": POSTGRES_PORT,
    "user": POSTGRES_USER,
    "password": POSTGRES_PASSWORD,
    "connect_timeout": POSTGRES_CONNECT_TIMEOUT,
    "application_name": POSTGRES_APPLICATION_NAME,
    # Removed cursor_factory to use default tuple cursors for compatibility
}


class PostgresConnectionManager:
    """Manages PostgreSQL connections using connection pooling for optimal performance."""

    def __init__(self):
        self._connection_pool: Optional[pool.SimpleConnectionPool] = None
        self._is_initialized = False
        # Track connection usage statistics
        self._connection_stats = {
            "total_connections_requested": 0,
            "total_connections_returned": 0,
            "current_connections_in_use": 0,
            "peak_connections_in_use": 0,
            "connection_errors": 0,
            "last_reset_time": datetime.now(),
        }

    def initialize(self):
        """Initialize PostgreSQL connection pool."""
        if self._is_initialized:
            logger.warning("PostgreSQL connection manager already initialized")
            return

        logger.info("Initializing PostgreSQL connection pool...")

        try:
            self._connection_pool = pool.SimpleConnectionPool(
                minconn=POSTGRES_MIN_CONN,
                maxconn=POSTGRES_MAX_CONN,
                **DB_CONFIG,
            )

            self._is_initialized = True
            logger.info(
                f"PostgreSQL connection pool initialized successfully "
                f"(min: {POSTGRES_MIN_CONN}, max: {POSTGRES_MAX_CONN})"
            )

            # Test the connection (but don't fail initialization if it fails)
            # This allows the app to start even if DB is temporarily unavailable
            if self.health_check():
                logger.info("PostgreSQL initial health check passed")
            else:
                logger.warning(
                    "PostgreSQL initial health check failed, but continuing startup"
                )

        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL connection pool: {e}")
            # Reset initialization state on failure
            self._is_initialized = False
            self._connection_pool = None
            raise

    @contextmanager
    def get_connection(self) -> Generator:
        """Get a database connection from the pool with proper error handling."""
        if not self._is_initialized or not self._connection_pool:
            raise RuntimeError("PostgreSQL connection manager not initialized")

        conn = None
        try:
            # Update connection statistics
            self._connection_stats["total_connections_requested"] += 1

            conn = self._connection_pool.getconn()

            if conn is None:
                self._connection_stats["connection_errors"] += 1
                raise ConnectionError("Failed to get connection from pool")

            # Track active connections
            self._connection_stats["current_connections_in_use"] += 1
            if (
                self._connection_stats["current_connections_in_use"]
                > self._connection_stats["peak_connections_in_use"]
            ):
                self._connection_stats["peak_connections_in_use"] = (
                    self._connection_stats["current_connections_in_use"]
                )

            # Set autocommit to False for transaction control
            conn.autocommit = False

            # Set a statement timeout for long-running queries
            with conn.cursor() as cursor:
                cursor.execute(f"SET statement_timeout = '{POSTGRES_COMMAND_TIMEOUT}s'")

            yield conn

        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except Exception as rollback_error:
                    logger.error(f"Error during rollback: {rollback_error}")
            self._connection_stats["connection_errors"] += 1
            raise e
        finally:
            if conn:
                try:
                    self._connection_pool.putconn(conn)
                    self._connection_stats["total_connections_returned"] += 1
                    self._connection_stats["current_connections_in_use"] -= 1
                except Exception as putconn_error:
                    logger.error(f"Error returning connection to pool: {putconn_error}")
                    self._connection_stats["connection_errors"] += 1

    @contextmanager
    def get_cursor(self, commit: bool = True) -> Generator:
        """Get a cursor with automatic transaction management."""
        with self.get_connection() as conn:
            with conn.cursor() as cursor:
                try:
                    yield cursor
                    if commit:
                        conn.commit()
                except Exception as e:
                    conn.rollback()
                    raise e

    def execute_query(
        self,
        query: str,
        params: tuple = None,
        fetch_one: bool = False,
        fetch_all: bool = False,
        commit: bool = True,
    ):
        """Execute a query with automatic transaction management."""
        with self.get_cursor(commit=commit) as cursor:
            cursor.execute(query, params)

            if fetch_one:
                return cursor.fetchone()
            elif fetch_all:
                return cursor.fetchall()
            return cursor.rowcount

    def execute_many(self, query: str, params_list: list, commit: bool = True):
        """Execute a query multiple times with different parameters."""
        with self.get_cursor(commit=commit) as cursor:
            cursor.executemany(query, params_list)
            return cursor.rowcount

    def bulk_insert(
        self,
        table: str,
        columns: list,
        data: list,
        on_conflict: str = None,
        commit: bool = True,
    ):
        """Perform bulk insert with optional conflict resolution."""
        if not data:
            return 0

        try:
            with self.get_cursor(commit=commit) as cursor:
                # Use SQL composition for safety
                table_name = psycopg2.sql.Identifier(table)
                column_names = psycopg2.sql.SQL(", ").join(
                    psycopg2.sql.Identifier(col) for col in columns
                )
                placeholders = psycopg2.sql.SQL(", ").join(
                    psycopg2.sql.Placeholder() for _ in columns
                )

                query = psycopg2.sql.SQL(
                    "INSERT INTO {table} ({columns}) VALUES ({placeholders})"
                ).format(
                    table=table_name, columns=column_names, placeholders=placeholders
                )

                if on_conflict:
                    query = psycopg2.sql.SQL("{query} {conflict}").format(
                        query=query, conflict=psycopg2.sql.SQL(on_conflict)
                    )

                cursor.executemany(query, data)
                return cursor.rowcount

        except Exception as e:
            logger.error(f"Bulk insert failed for table {table}: {e}")
            raise

    def execute_transaction(self, operations: list, commit: bool = True):
        """Execute multiple operations in a single transaction."""
        with self.get_cursor(commit=commit) as cursor:
            results = []
            for operation in operations:
                query = operation.get("query")
                params = operation.get("params", ())
                fetch = operation.get("fetch", None)  # 'one', 'all', or None

                cursor.execute(query, params)

                if fetch == "one":
                    results.append(cursor.fetchone())
                elif fetch == "all":
                    results.append(cursor.fetchall())
                else:
                    results.append(cursor.rowcount)

            return results

    def health_check(self) -> bool:
        """Perform a health check on the PostgreSQL connection."""
        try:
            with self.get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                    result = cursor.fetchone()
                    return result is not None and result[0] == 1
        except Exception as e:
            logger.error(f"PostgreSQL health check failed: {e}")
            return False

    def get_pool_stats(self) -> dict:
        """Get connection pool statistics for monitoring."""
        if not self._connection_pool:
            return {"error": "Pool not initialized"}

        try:
            # psycopg2 SimpleConnectionPool has limited introspection
            # but we can get some basic info
            pool = self._connection_pool

            # Try to access private attributes (may vary by psycopg2 version)
            try:
                # These are internal attributes and may not always be available
                total_connections = (
                    len(pool._pool) if hasattr(pool, "_pool") else "unknown"
                )
                available_connections = (
                    len(pool._pool) if hasattr(pool, "_pool") else "unknown"
                )
                used_connections = getattr(pool, "_used", {})
                used_count = (
                    len(used_connections)
                    if isinstance(used_connections, dict)
                    else "unknown"
                )
            except (AttributeError, TypeError):
                total_connections = "unknown"
                available_connections = "unknown"
                used_count = "unknown"

            # Include our custom tracking statistics
            uptime_seconds = (
                datetime.now() - self._connection_stats["last_reset_time"]
            ).total_seconds()

            return {
                "min_connections": POSTGRES_MIN_CONN,
                "max_connections": POSTGRES_MAX_CONN,
                "initialized": self._is_initialized,
                "pool_total_connections": total_connections,
                "pool_available_connections": available_connections,
                "pool_used_connections": used_count,
                # Custom tracking statistics
                "runtime_stats": {
                    "total_connections_requested": self._connection_stats[
                        "total_connections_requested"
                    ],
                    "total_connections_returned": self._connection_stats[
                        "total_connections_returned"
                    ],
                    "current_connections_in_use": self._connection_stats[
                        "current_connections_in_use"
                    ],
                    "peak_connections_in_use": self._connection_stats[
                        "peak_connections_in_use"
                    ],
                    "connection_errors": self._connection_stats["connection_errors"],
                    "uptime_seconds": round(uptime_seconds, 2),
                    "requests_per_second": round(
                        self._connection_stats["total_connections_requested"]
                        / max(uptime_seconds, 1),
                        2,
                    ),
                },
            }
        except Exception as e:
            logger.error(f"Error getting pool stats: {e}")
            return {"error": str(e)}

    def reset_connection_stats(self):
        """Reset connection statistics for monitoring."""
        self._connection_stats = {
            "total_connections_requested": 0,
            "total_connections_returned": 0,
            "current_connections_in_use": 0,
            "peak_connections_in_use": 0,
            "connection_errors": 0,
            "last_reset_time": datetime.now(),
        }

    def close(self):
        """Close all PostgreSQL connections."""
        if not self._is_initialized:
            return

        logger.info("Closing PostgreSQL connections...")

        try:
            if self._connection_pool:
                self._connection_pool.closeall()
        except Exception as e:
            logger.error(f"Error closing PostgreSQL connections: {e}")
        finally:
            self._is_initialized = False
            self._connection_pool = None
            logger.info("PostgreSQL connections closed")


# Global connection manager instance
_postgres_manager = PostgresConnectionManager()


def get_postgres_client() -> PostgresConnectionManager:
    """Get the PostgreSQL connection manager."""
    return _postgres_manager


def initialize_postgres():
    """Initialize PostgreSQL connection pool."""
    _postgres_manager.initialize()


def close_postgres_client():
    """Close all PostgreSQL connections."""
    _postgres_manager.close()


def postgres_health_check() -> bool:
    """Check if PostgreSQL is healthy and responsive."""
    return _postgres_manager.health_check()


def get_postgres_pool_stats() -> dict:
    """Get PostgreSQL connection pool statistics."""
    return _postgres_manager.get_pool_stats()


def reset_postgres_connection_stats():
    """Reset PostgreSQL connection tracking statistics."""
    return _postgres_manager.reset_connection_stats()


@contextmanager
def get_db_connection():
    """Get a database connection context manager for backward compatibility."""
    with _postgres_manager.get_connection() as conn:
        yield conn


@contextmanager
def get_db_cursor(commit: bool = True):
    """Get a database cursor with automatic transaction management."""
    with _postgres_manager.get_cursor(commit=commit) as cursor:
        yield cursor


@contextmanager
def get_dict_cursor(commit: bool = True):
    """Get a database cursor that returns dict-like results."""
    with _postgres_manager.get_connection() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            try:
                yield cursor
                if commit:
                    conn.commit()
            except Exception as e:
                conn.rollback()
                raise e


def execute_bulk_operation(
    table: str, columns: list, data: list, on_conflict: str = None
):
    """Execute bulk insert operation with optimized performance."""
    return _postgres_manager.bulk_insert(table, columns, data, on_conflict)


def execute_transaction(operations: list):
    """Execute multiple database operations in a single transaction."""
    return _postgres_manager.execute_transaction(operations)


def get_detailed_connection_info():
    """Get detailed connection information for debugging."""
    try:
        with get_db_cursor(commit=False) as cursor:
            # Get overall connection stats
            cursor.execute(
                """
                SELECT 
                    count(*) as total_connections,
                    count(*) FILTER (WHERE state = 'active') as active,
                    count(*) FILTER (WHERE state = 'idle') as idle,
                    count(*) FILTER (WHERE state = 'idle in transaction') as idle_in_transaction,
                    count(*) FILTER (WHERE application_name = %s) as our_app_connections,
                    count(*) FILTER (WHERE application_name = %s AND state = 'active') as our_active_connections,
                    count(*) FILTER (WHERE application_name = %s AND state = 'idle') as our_idle_connections
                FROM pg_stat_activity
                WHERE pid != pg_backend_pid()  -- Exclude current connection
            """,
                (
                    POSTGRES_APPLICATION_NAME,
                    POSTGRES_APPLICATION_NAME,
                    POSTGRES_APPLICATION_NAME,
                ),
            )

            result = cursor.fetchone()

            # Get additional database stats
            cursor.execute(
                """
                SELECT 
                    numbackends as backends,
                    xact_commit as transactions_committed,
                    xact_rollback as transactions_rolled_back,
                    blks_read as blocks_read,
                    blks_hit as blocks_hit,
                    tup_returned as tuples_returned,
                    tup_fetched as tuples_fetched,
                    tup_inserted as tuples_inserted,
                    tup_updated as tuples_updated,
                    tup_deleted as tuples_deleted
                FROM pg_stat_database 
                WHERE datname = current_database()
                """
            )

            db_stats = cursor.fetchone()

            # Calculate cache hit ratio
            blocks_hit = db_stats[4] if db_stats and db_stats[4] else 0
            blocks_read = db_stats[3] if db_stats and db_stats[3] else 0
            total_blocks = blocks_hit + blocks_read
            cache_hit_ratio = (
                (blocks_hit / total_blocks * 100) if total_blocks > 0 else 0
            )

            return {
                "total_connections": result[0],
                "active_connections": result[1],
                "idle_connections": result[2],
                "idle_in_transaction_connections": result[3],
                "our_app_connections": result[4],
                "our_active_connections": result[5],
                "our_idle_connections": result[6],
                "database_backends": db_stats[0] if db_stats else 0,
                "transactions_committed": db_stats[1] if db_stats else 0,
                "transactions_rolled_back": db_stats[2] if db_stats else 0,
                "cache_hit_ratio_percent": round(cache_hit_ratio, 2),
                "blocks_read": blocks_read,
                "blocks_hit": blocks_hit,
                "tuples_returned": db_stats[5] if db_stats else 0,
                "tuples_fetched": db_stats[6] if db_stats else 0,
                "tuples_inserted": db_stats[7] if db_stats else 0,
                "tuples_updated": db_stats[8] if db_stats else 0,
                "tuples_deleted": db_stats[9] if db_stats else 0,
            }
    except Exception as e:
        logger.error(f"Error getting connection info: {e}")
        return {"error": str(e)}


def get_active_queries_info():
    """Get information about currently running queries."""
    try:
        with get_db_cursor(commit=False) as cursor:
            cursor.execute(
                """
                SELECT 
                    pid,
                    application_name,
                    client_addr,
                    state,
                    query_start,
                    state_change,
                    wait_event_type,
                    wait_event,
                    EXTRACT(EPOCH FROM (now() - query_start)) as query_duration_seconds,
                    LEFT(query, 100) as query_preview
                FROM pg_stat_activity 
                WHERE state = 'active' 
                    AND pid != pg_backend_pid()  -- Exclude current connection
                    AND application_name = %s
                ORDER BY query_start
                LIMIT 10
                """,
                (POSTGRES_APPLICATION_NAME,),
            )

            active_queries = cursor.fetchall()

            queries_info = []
            for query in active_queries:
                queries_info.append(
                    {
                        "pid": query[0],
                        "application_name": query[1],
                        "client_addr": str(query[2]) if query[2] else None,
                        "state": query[3],
                        "query_start": query[4].isoformat() if query[4] else None,
                        "state_change": query[5].isoformat() if query[5] else None,
                        "wait_event_type": query[6],
                        "wait_event": query[7],
                        "query_duration_seconds": round(query[8], 2) if query[8] else 0,
                        "query_preview": query[9],
                    }
                )

            return queries_info

    except Exception as e:
        logger.error(f"Error getting active queries info: {e}")
        return {"error": str(e)}


def health_check():
    """Add database performance monitoring information."""
    try:
        stats = get_postgres_pool_stats()
        health_status = postgres_health_check()
        connection_info = get_detailed_connection_info()
        active_queries = get_active_queries_info()

        # Add basic performance metrics
        with get_db_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE state = 'active'"
            )
            active_connections = cursor.fetchone()[0]

            cursor.execute("SELECT pg_database_size(current_database())")
            db_size = cursor.fetchone()[0]

            cursor.execute("SHOW max_connections")
            pg_max_connections = cursor.fetchone()[0]

            # Get current connection info for this specific connection
            cursor.execute(
                """
                SELECT 
                    application_name,
                    client_addr,
                    state,
                    query_start,
                    state_change,
                    backend_start
                FROM pg_stat_activity 
                WHERE pid = pg_backend_pid()
                """
            )
            current_conn_info = cursor.fetchone()

        # Format current connection info
        current_connection = (
            {
                "application_name": current_conn_info[0] if current_conn_info else None,
                "client_addr": (
                    str(current_conn_info[1])
                    if current_conn_info and current_conn_info[1]
                    else None
                ),
                "state": current_conn_info[2] if current_conn_info else None,
                "query_start": (
                    current_conn_info[3].isoformat()
                    if current_conn_info and current_conn_info[3]
                    else None
                ),
                "state_change": (
                    current_conn_info[4].isoformat()
                    if current_conn_info and current_conn_info[4]
                    else None
                ),
                "backend_start": (
                    current_conn_info[5].isoformat()
                    if current_conn_info and current_conn_info[5]
                    else None
                ),
            }
            if current_conn_info
            else {}
        )

        return {
            "database": {
                "healthy": health_status,
                "active_connections": active_connections,
                "database_size_bytes": db_size,
                "pool_stats": stats,
                "postgresql_max_connections": int(pg_max_connections),
                "connection_info": connection_info,
                "current_connection": current_connection,
                "active_queries": active_queries,
                "application_name": POSTGRES_APPLICATION_NAME,
            }
        }
    except Exception as e:
        logger.error(f"Error getting database performance metrics: {e}")
        return {"database": {"healthy": False, "error": str(e)}}


def add_or_update_characters(characters: list[dict]):
    """Add or update characters with optimized bulk operations and error handling."""
    if not characters:
        return

    (valid_area_ids, _, _) = get_valid_area_ids()

    try:
        with get_db_cursor() as cursor:
            # Prepare data and validate location_ids
            processed_characters = []
            for character in characters:
                # Check if the character's location_id is valid
                if character.get("location_id") not in valid_area_ids:
                    character["location_id"] = 0  # Set to 0 if invalid
                processed_characters.append(character)

            exclude_fields = [
                "public_comment",
                "group_id",
                "is_in_party",
                "is_recruiting",
                "is_online",
                "last_save",
            ]

            # Process characters in batches for better performance
            batch_size = 1000  # Configurable batch size
            for i in range(0, len(processed_characters), batch_size):
                batch = processed_characters[i : i + batch_size]

                for character in batch:
                    character_fields = [
                        field
                        for field in character.keys()
                        if field not in exclude_fields
                    ]

                    update_list: list[str] = [
                        f"{field} = EXCLUDED.{field}"
                        for field in character_fields
                        if field not in ["name", "gender"]
                    ]

                    # Note: name and gender are different because anonymous characters will
                    # have no name or gender. So these are only updated if the character
                    # is not anonymous.
                    update_list.extend(
                        [
                            f"{field} = CASE WHEN EXCLUDED.is_anonymous = 'true' THEN characters.{field} ELSE EXCLUDED.{field} END"
                            for field in ["name", "gender"]
                        ]
                    )

                    # Construct the query dynamically using SQL composition for safety
                    columns = psycopg2.sql.SQL(", ").join(
                        psycopg2.sql.Identifier(field) for field in character_fields
                    )
                    placeholders = psycopg2.sql.SQL(", ").join(
                        psycopg2.sql.Placeholder() for _ in character_fields
                    )
                    updates = psycopg2.sql.SQL(", ").join(
                        psycopg2.sql.SQL(update) for update in update_list
                    )

                    query = psycopg2.sql.SQL(
                        """
                        INSERT INTO characters ({columns})
                        VALUES ({placeholders})
                        ON CONFLICT (id) DO UPDATE SET
                        {updates}, last_save = NOW()
                    """
                    ).format(
                        columns=columns, placeholders=placeholders, updates=updates
                    )

                    # Get the values of the Character model
                    values = [
                        json.dumps(value) if isinstance(value, (dict, list)) else value
                        for key, value in character.items()
                        if key in character_fields
                    ]

                    cursor.execute(query, values)

                logger.debug(
                    f"Processed batch {i//batch_size + 1} of characters "
                    f"({len(batch)} characters)"
                )

        logger.info(f"Successfully added/updated {len(characters)} characters")

    except Exception as e:
        logger.error(f"Failed to add/update characters: {e}")
        raise


def get_character_by_id(character_id: int) -> Character | None:
    """Get a character by ID with optimized query."""
    try:
        with get_db_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT * FROM public.characters WHERE id = %s", (character_id,)
            )
            character = cursor.fetchone()
            if not character:
                return None

            return build_character_from_row(character)
    except Exception as e:
        logger.error(f"Error getting character by ID {character_id}: {e}")
        return None


def get_characters_by_ids(character_ids: list[int]) -> list[Character]:
    """Get multiple characters by IDs with optimized bulk query."""
    if not character_ids:
        return []

    try:
        with get_db_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT * FROM public.characters WHERE id = ANY(%s)",
                (character_ids,),
            )
            characters = cursor.fetchall()
            if not characters:
                return []

            return [build_character_from_row(character) for character in characters]
    except Exception as e:
        logger.error(f"Error getting characters by IDs: {e}")
        return []


def get_character_ids_by_server_and_guild(
    server_name: str,
    guild_name: str,
    page: int = 1,
    page_size: int = 20,
    sort_by: str = "last_save",
) -> list[int]:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            offset = (page - 1) * page_size

            # Whitelist allowed columns
            allowed_sort_cols = {"last_save", "id", "name", "total_level"}
            if sort_by not in allowed_sort_cols:
                sort_by = "last_save"

            query = psycopg2.sql.SQL(
                """
                SELECT id FROM public.characters
                WHERE LOWER(server_name) = %s AND LOWER(guild_name) = %s
                ORDER BY {col} DESC
                LIMIT %s OFFSET %s
                """
            ).format(col=psycopg2.sql.Identifier(sort_by))

            cursor.execute(
                query,
                (server_name.lower(), guild_name.lower(), page_size, offset),
            )
            character_ids = cursor.fetchall()
            if not character_ids:
                return []
            return [char_id[0] for char_id in character_ids]


def get_character_by_name_and_server(
    character_name: str, server_name: str
) -> Character | None:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM public.characters WHERE LOWER(name) = %s AND LOWER(server_name) = %s",
                (character_name.lower(), server_name.lower()),
            )
            character = cursor.fetchone()
            if not character:
                return None

            return build_character_from_row(character)


def get_characters_by_name(character_name: str) -> list[Character]:
    """
    Gets all characters (the most recent 10) from the database that match the given name.
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM public.characters WHERE LOWER(name) = %s ORDER BY last_save DESC LIMIT 10",
                (character_name.lower(),),
            )
            characters = cursor.fetchall()
            if not characters:
                return []

            return [build_character_from_row(character) for character in characters]


def get_character_activity_summary_by_character_id(
    character_id: str,
) -> CharacterActivitySummary:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    jsonb_array_length(total_level),
                    jsonb_array_length(location),
                    jsonb_array_length(guild_name),
                    jsonb_array_length(server_name),
                    jsonb_array_length(is_online)
                FROM public.character_activity
                WHERE id = %s
                """,
                (character_id,),
            )
            activity = cursor.fetchone()
            if not activity:
                return CharacterActivitySummary(
                    level_event_count=0,
                    location_event_count=0,
                    guild_name_event_count=0,
                    server_name_event_count=0,
                    status_event_count=0,
                )

            return build_character_activity_summary_from_row(activity)


def get_all_character_activity_by_character_id(
    character_id: int,
    start_date: datetime = None,
    end_date: datetime = None,
    limit: int = MAX_CHARACTER_AGG_ACTIVITY_READ_LENGTH,
) -> list[dict]:
    """
    Get mixed activity entries (all activity types) for a character.

    Returns a list of dicts:
      { activity_type: "<type>", timestamp: "...", character_id: <int>, data: {...} }

    Applies the same default window and maximum history as
    get_character_activity_by_type_and_character_id.
    """
    if not start_date:
        start_date = datetime.now() - timedelta(days=90)
    if not end_date:
        end_date = datetime.now()

    # Clamp history window
    if (end_date - start_date).days > MAX_CHARACTER_ACTIVITY_READ_HISTORY:
        start_date = datetime.now() - timedelta(
            days=MAX_CHARACTER_ACTIVITY_READ_HISTORY
        )
        end_date = datetime.now()

    # Clamp limit
    limit = max(
        1,
        min(
            limit if limit is not None else MAX_CHARACTER_AGG_ACTIVITY_READ_LENGTH,
            MAX_CHARACTER_AGG_ACTIVITY_READ_LENGTH,
        ),
    )

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT timestamp, character_id, activity_type, data
                FROM public.character_activity
                WHERE character_id = %s
                  AND timestamp BETWEEN %s AND %s
                ORDER BY timestamp DESC
                LIMIT %s
                """,
                (
                    character_id,
                    start_date.isoformat(),
                    end_date.isoformat(),
                    limit,
                ),
            )
            rows = cursor.fetchall()
            if not rows:
                return []

            results: list[dict] = []
            for row in rows:
                ts, cid, activity_type_str, data = row
                try:
                    atype = CharacterActivityType(activity_type_str)
                except Exception:
                    atype = None

                # Reuse existing builders by adapting the tuple shape
                built: dict
                if atype == CharacterActivityType.LOCATION:
                    built = build_character_location_activity_from_row((ts, cid, data))
                elif atype == CharacterActivityType.STATUS:
                    built = build_character_status_activity_from_row((ts, cid, data))
                elif atype == CharacterActivityType.TOTAL_LEVEL:
                    built = build_character_total_level_activity_from_row(
                        (ts, cid, data)
                    )
                elif atype == CharacterActivityType.GUILD_NAME:
                    built = build_character_guild_name_activity_from_row(
                        (ts, cid, data)
                    )
                else:
                    # Fallback for unknown/unhandled types
                    built = {
                        "timestamp": (
                            datetime_to_datetime_string(ts)
                            if isinstance(ts, datetime)
                            else ""
                        ),
                        "character_id": int(cid),
                        "data": data if isinstance(data, dict) else {},
                    }

                # Attach the activity type for mixed results
                built["activity_type"] = activity_type_str
                results.append(built)

            return results


def get_character_activity_by_type_and_character_id(
    character_id: str,
    activity_Type: CharacterActivityType,
    start_date: datetime = None,
    end_date: datetime = None,
    limit: int = MAX_CHARACTER_ACTIVITY_READ_LENGTH,
) -> list[dict]:
    if not start_date:
        start_date = datetime.now() - timedelta(days=90)
    if not end_date:
        end_date = datetime.now()

    # if the total number of days is greater than the maximum allowed, set
    # the end date to the maximum allowed
    if (end_date - start_date).days > MAX_CHARACTER_ACTIVITY_READ_HISTORY:
        start_date = datetime.now() - timedelta(
            days=MAX_CHARACTER_ACTIVITY_READ_HISTORY
        )
        end_date = datetime.now()

    limit = max(
        1,
        min(
            limit if limit is not None else MAX_CHARACTER_ACTIVITY_READ_LENGTH,
            MAX_CHARACTER_ACTIVITY_READ_LENGTH,
        ),
    )

    if activity_Type not in CharacterActivityType:
        raise ValueError(f"Invalid activity type: {activity_Type}")

    with get_db_connection() as conn:
        # TODO: use datetime_to_datetime_string ?
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT timestamp, character_id, data
                FROM public.character_activity
                WHERE character_id = %s AND activity_type = %s AND timestamp BETWEEN %s AND %s
                ORDER BY timestamp DESC
                LIMIT %s
                """,
                (
                    character_id,
                    activity_Type.value,
                    start_date.isoformat(),
                    end_date.isoformat(),
                    limit,
                ),
            )
            activity = cursor.fetchall()
            if not activity:
                return []

            if activity_Type == CharacterActivityType.LOCATION:
                return [
                    build_character_location_activity_from_row(row) for row in activity
                ]
            elif activity_Type == CharacterActivityType.STATUS:
                return [
                    build_character_status_activity_from_row(row) for row in activity
                ]
            elif activity_Type == CharacterActivityType.TOTAL_LEVEL:
                return [
                    build_character_total_level_activity_from_row(row)
                    for row in activity
                ]
            elif activity_Type == CharacterActivityType.GUILD_NAME:
                return [
                    build_character_guild_name_activity_from_row(row)
                    for row in activity
                ]
            else:
                return []  # not implemented


def build_character_location_activity_from_row(row: tuple) -> dict:
    return {
        "timestamp": (
            datetime_to_datetime_string(row[0]) if isinstance(row[0], datetime) else ""
        ),
        "character_id": int(row[1]),
        "data": {
            "location_id": int(row[2]["value"]) if row[2] is not None else None,
        },
    }


def build_character_status_activity_from_row(row: tuple) -> dict:
    return {
        "timestamp": (
            datetime_to_datetime_string(row[0]) if isinstance(row[0], datetime) else ""
        ),
        "character_id": int(row[1]),
        "data": {
            "status": bool(row[2]["value"]) if row[2] is not None else None,
        },
    }


def build_character_guild_name_activity_from_row(row: tuple) -> dict:
    return {
        "timestamp": (
            datetime_to_datetime_string(row[0]) if isinstance(row[0], datetime) else ""
        ),
        "character_id": int(row[1]),
        "data": {
            "guild_name": str(row[2]["value"]) if row[2] is not None else None,
        },
    }


def build_character_total_level_activity_from_row(row: tuple) -> dict:
    return {
        "timestamp": (
            datetime_to_datetime_string(row[0]) if isinstance(row[0], datetime) else ""
        ),
        "character_id": int(row[1]),
        "data": {
            "total_level": row[2]["total_level"] if row[2] is not None else None,
            "classes": row[2]["classes"] if row[2] is not None else None,
        },
    }


def get_recent_quest_activity_by_character_id(
    character_id: int,
) -> list[dict[str, Quest]]:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT timestamp, public.character_activity.character_id, public.quests.name FROM public.character_activity
                LEFT JOIN public.quests ON public.quests.area_id = CAST(public.character_activity.data ->> 'value' as INTEGER)
                WHERE public.character_activity.character_id = %s AND activity_type = 'location' AND timestamp >= NOW() - INTERVAL '7 days'
                ORDER BY timestamp DESC
                LIMIT 500
                """,
                (character_id,),
            )
            activity = cursor.fetchall()
            if not activity:
                return []

            return activity


def get_recent_raid_activity_by_character_id(
    character_id: int,
) -> list[dict[str, Quest]]:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT timestamp, public.character_activity.character_id, public.quests.id FROM public.character_activity
                LEFT JOIN public.quests ON public.quests.area_id = CAST(public.character_activity.data ->> 'value' as INTEGER)
                WHERE quests.group_size = 'Raid' AND character_activity.character_id = %s AND character_activity.activity_type = 'location' AND timestamp >= NOW() - INTERVAL '5 days'
                ORDER BY timestamp DESC
                LIMIT 100
                """,
                (character_id,),
            )
            activities = cursor.fetchall()
            if not activities:
                return []

            return build_character_raid_activity_from_rows(activities)


def get_recent_raid_activity_by_character_ids(
    character_ids: list[int],
) -> list[dict]:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT 
                    next_activity.timestamp as timestamp,
                    current_activity.character_id, 
                    ARRAY_AGG(DISTINCT quests.id ORDER BY quests.id) as quest_ids
                FROM public.character_activity current_activity
                LEFT JOIN public.quests ON public.quests.area_id = CAST(current_activity.data ->> 'value' as INTEGER)
                INNER JOIN LATERAL (
                    SELECT timestamp 
                    FROM public.character_activity next_activity
                    WHERE next_activity.character_id = current_activity.character_id 
                        AND next_activity.activity_type = 'location'
                        AND next_activity.timestamp > current_activity.timestamp
                    ORDER BY next_activity.timestamp ASC
                    LIMIT 1
                ) next_activity ON true
                WHERE quests.group_size = 'Raid' 
                    AND current_activity.character_id = ANY(%s) 
                    AND current_activity.activity_type = 'location' 
                    AND current_activity.timestamp >= NOW() - INTERVAL '66 hours'
                GROUP BY next_activity.timestamp, current_activity.character_id
                ORDER BY timestamp DESC
                LIMIT 100
                """,
                (character_ids,),
            )
            activities = cursor.fetchall()
            if not activities:
                return []

            return build_character_raid_activity_from_rows(activities)


def validate_lookback(lookback: int) -> None:
    """
    Validate the lookback period for queries.
    Raises ValueError if lookback is not within the allowed range.
    """
    if lookback <= 0:
        raise ValueError("lookback must be greater than 0")
    if lookback > 365:
        raise ValueError("lookback cannot exceed 365 days")


def get_gender_distribution(
    lookback_in_days: int = 90, activity_level: str = "all"
) -> dict[str, int]:
    """
    Get the gender distribution of characters in the database.
    Optionally filter by activity_level: one of "all" (default), "active", or "inactive".
    """
    validate_lookback(lookback_in_days)
    if activity_level == "active":
        query = """
            SELECT server_name, gender, COUNT(*) as count FROM public.characters
            LEFT JOIN public.character_report_status crs ON public.characters.id = crs.character_id
            WHERE last_save > NOW() - (make_interval(days => %s)) AND crs.active IS TRUE
            GROUP BY gender, server_name
            """
    elif activity_level == "inactive":
        query = """
            SELECT server_name, gender, COUNT(*) as count FROM public.characters
            LEFT JOIN public.character_report_status crs ON public.characters.id = crs.character_id
            WHERE last_save > NOW() - (make_interval(days => %s)) AND crs.active IS FALSE
            GROUP BY gender, server_name
            """
    else:
        query = """
            SELECT server_name, gender, COUNT(*) as count FROM public.characters
            WHERE last_save > NOW() - (make_interval(days => %s))
            GROUP BY gender, server_name
            """

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (lookback_in_days,))
            gender_distribution = cursor.fetchall()
            if not gender_distribution:
                return {}
            output = {}
            for server_name, gender, count in gender_distribution:
                server_name_lower = str(server_name).lower()
                if server_name_lower not in output:
                    output[server_name_lower] = {}
                output[server_name_lower][str(gender)] = count
            return output


def get_race_distribution(
    lookback_in_days: int = 90, activity_level: str = "all"
) -> dict[str, int]:
    """
    Get the race distribution of characters in the database.
    Optionally filter by activity_level: one of "all" (default), "active", or "inactive".
    """
    validate_lookback(lookback_in_days)

    if activity_level == "active":
        query = """
            SELECT server_name, race, COUNT(*) as count FROM public.characters
            LEFT JOIN public.character_report_status crs ON public.characters.id = crs.character_id
            WHERE last_save > NOW() - (make_interval(days => %s)) AND crs.active IS TRUE
            GROUP BY race, server_name
            """
    elif activity_level == "inactive":
        query = """
            SELECT server_name, race, COUNT(*) as count FROM public.characters
            LEFT JOIN public.character_report_status crs ON public.characters.id = crs.character_id
            WHERE last_save > NOW() - (make_interval(days => %s)) AND crs.active IS FALSE
            GROUP BY race, server_name
            """
    else:
        query = """
            SELECT server_name, race, COUNT(*) as count FROM public.characters
            WHERE last_save > NOW() - (make_interval(days => %s))
            GROUP BY race, server_name
            """

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (lookback_in_days,))
            race_distribution = cursor.fetchall()
            if not race_distribution:
                return {}
            output = {}
            for server_name, race, count in race_distribution:
                server_name_lower = str(server_name).lower()
                if server_name_lower not in output:
                    output[server_name_lower] = {}
                output[server_name_lower][str(race)] = count
            return output


def get_total_level_distribution(
    lookback_in_days: int = 90, activity_level: str = "all"
) -> dict[str, int]:
    """
    Get the total_level distribution of characters in the database.
    """
    validate_lookback(lookback_in_days)

    if activity_level == "active":
        query = """
                SELECT server_name, total_level, COUNT(*) as count FROM public.characters
                LEFT JOIN public.character_report_status crs ON public.characters.id = crs.character_id
                WHERE last_save > NOW() - (make_interval(days => %s)) AND crs.active IS TRUE
                GROUP BY total_level, server_name
                ORDER BY server_name, total_level
                """
    elif activity_level == "inactive":
        query = """
                SELECT server_name, total_level, COUNT(*) as count FROM public.characters
                LEFT JOIN public.character_report_status crs ON public.characters.id = crs.character_id
                WHERE last_save > NOW() - (make_interval(days => %s)) AND crs.active IS FALSE
                GROUP BY total_level, server_name
                ORDER BY server_name, total_level
                """
    else:
        query = """
                SELECT server_name, total_level, COUNT(*) as count FROM public.characters
                WHERE last_save > NOW() - (make_interval(days => %s))
                GROUP BY total_level, server_name
                ORDER BY server_name, total_level
                """

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query,
                (lookback_in_days,),
            )
            total_level_distribution = cursor.fetchall()
            if not total_level_distribution:
                return {}
            output = {}
            for server_name, total_level, count in total_level_distribution:
                server_name_lower = str(server_name).lower()
                if server_name_lower not in output:
                    output[server_name_lower] = {}
                output[server_name_lower][str(total_level)] = count
            return output


def get_class_count_distribution(
    lookback_in_days: int = 90, activity_level: str = "all"
) -> dict[str, int]:
    """
    Get the number of classes distribution of characters in the database.
    i.e. The number of classes each character has, excluding "Legendary" and "Epic" (which are not playable classes).

    'classes' is a jsonb field that contains an array of classes that looks like:
    [{"name": "Fighter", "level": 20}, {"name": "Wizard", "level": 10}]
    """
    validate_lookback(lookback_in_days)
    if activity_level == "active":
        query = """
            SELECT server_name, class_count, COUNT(*) as count
            FROM (
                SELECT server_name,
                    (
                        SELECT COUNT(*)
                        FROM jsonb_array_elements(classes) AS elem
                        WHERE elem->>'name' NOT IN ('Legendary', 'Epic')
                    ) AS class_count
                FROM public.characters
                LEFT JOIN public.character_report_status crs ON public.characters.id = crs.character_id
                WHERE last_save > NOW() - (make_interval(days => %s)) AND crs.active IS TRUE
            ) AS sub
            GROUP BY server_name, class_count
            ORDER BY server_name, class_count
            """
    elif activity_level == "inactive":
        query = """
            SELECT server_name, class_count, COUNT(*) as count
            FROM (
                SELECT server_name,
                    (
                        SELECT COUNT(*)
                        FROM jsonb_array_elements(classes) AS elem
                        WHERE elem->>'name' NOT IN ('Legendary', 'Epic')
                    ) AS class_count
                FROM public.characters
                LEFT JOIN public.character_report_status crs ON public.characters.id = crs.character_id
                WHERE last_save > NOW() - (make_interval(days => %s)) AND crs.active IS FALSE
            ) AS sub
            GROUP BY server_name, class_count
            ORDER BY server_name, class_count
            """
    else:
        query = """
            SELECT server_name, class_count, COUNT(*) as count
            FROM (
                SELECT server_name,
                    (
                        SELECT COUNT(*)
                        FROM jsonb_array_elements(classes) AS elem
                        WHERE elem->>'name' NOT IN ('Legendary', 'Epic')
                    ) AS class_count
                FROM public.characters
                WHERE last_save > NOW() - (make_interval(days => %s))
            ) AS sub
            GROUP BY server_name, class_count
            ORDER BY server_name, class_count
            """

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (lookback_in_days,))
            result = cursor.fetchall()
            if not result:
                return {}
            output = {}
            for server_name, class_count, count in result:
                server_name_lower = str(server_name).lower()
                if server_name_lower not in output:
                    output[server_name_lower] = {}
                output[server_name_lower][str(class_count)] = count
            return output


def get_primary_class_distribution(
    lookback_in_days: int = 90, activity_level: str = "all"
) -> dict[str, int]:
    """
    Get the primary class distribution of characters in the database.
    """
    validate_lookback(lookback_in_days)
    if activity_level == "active":
        query = """
            SELECT
                server_name,
                primary_class,
                COUNT(*) as count
            FROM (
                SELECT
                    server_name,
                    (
                        SELECT elem->>'name'
                        FROM jsonb_array_elements(classes) AS elem
                        WHERE elem->>'name' NOT IN ('Legendary', 'Epic')
                        ORDER BY (elem->>'level')::int DESC
                        LIMIT 1
                    ) AS primary_class
                FROM public.characters
                LEFT JOIN public.character_report_status crs ON public.characters.id = crs.character_id
                WHERE last_save > NOW() - (make_interval(days => %s)) AND crs.active IS TRUE
            ) AS sub
            GROUP BY server_name, primary_class
            ORDER BY server_name, count DESC
            """
    elif activity_level == "inactive":
        query = """
            SELECT
                server_name,
                primary_class,
                COUNT(*) as count
            FROM (
                SELECT
                    server_name,
                    (
                        SELECT elem->>'name'
                        FROM jsonb_array_elements(classes) AS elem
                        WHERE elem->>'name' NOT IN ('Legendary', 'Epic')
                        ORDER BY (elem->>'level')::int DESC
                        LIMIT 1
                    ) AS primary_class
                FROM public.characters
                LEFT JOIN public.character_report_status crs ON public.characters.id = crs.character_id
                WHERE last_save > NOW() - (make_interval(days => %s)) AND crs.active IS FALSE
            ) AS sub
            GROUP BY server_name, primary_class
            ORDER BY server_name, count DESC
            """
    else:
        query = """
            SELECT
                server_name,
                primary_class,
                COUNT(*) as count
            FROM (
                SELECT
                    server_name,
                    (
                        SELECT elem->>'name'
                        FROM jsonb_array_elements(classes) AS elem
                        WHERE elem->>'name' NOT IN ('Legendary', 'Epic')
                        ORDER BY (elem->>'level')::int DESC
                        LIMIT 1
                    ) AS primary_class
                FROM public.characters
                WHERE last_save > NOW() - (make_interval(days => %s))
            ) AS sub
            GROUP BY server_name, primary_class
            ORDER BY server_name, count DESC
            """

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (lookback_in_days,))
            result = cursor.fetchall()
            if not result:
                return {}
            output = {}
            for server_name, class_count, count in result:
                server_name_lower = str(server_name).lower()
                if server_name_lower not in output:
                    output[server_name_lower] = {}
                output[server_name_lower][str(class_count)] = count
            return output


def get_guild_affiliation_distribution(
    lookback_in_days: int = 90, activity_level: str = "all"
) -> dict[str, dict[str, int]]:
    """
    Gets the distribution of characters in a guild and not in a guild.
    """
    validate_lookback(lookback_in_days)
    if activity_level == "active":
        query = """
            SELECT server_name,
                SUM(CASE WHEN guild_name IS NOT NULL AND guild_name <> '' THEN 1 ELSE 0 END) AS in_guild,
                SUM(CASE WHEN guild_name IS NULL OR guild_name = '' THEN 1 ELSE 0 END) AS not_in_guild
            FROM public.characters
            LEFT JOIN public.character_report_status crs ON public.characters.id = crs.character_id
            WHERE last_save > NOW() - (make_interval(days => %s)) AND crs.active IS TRUE
            GROUP BY server_name
            """
    elif activity_level == "inactive":
        query = """
            SELECT server_name,
                SUM(CASE WHEN guild_name IS NOT NULL AND guild_name <> '' THEN 1 ELSE 0 END) AS in_guild,
                SUM(CASE WHEN guild_name IS NULL OR guild_name = '' THEN 1 ELSE 0 END) AS not_in_guild
            FROM public.characters
            LEFT JOIN public.character_report_status crs ON public.characters.id = crs.character_id
            WHERE last_save > NOW() - (make_interval(days => %s)) AND crs.active IS FALSE
            GROUP BY server_name
            """
    else:
        query = """
            SELECT server_name,
                SUM(CASE WHEN guild_name IS NOT NULL AND guild_name <> '' THEN 1 ELSE 0 END) AS in_guild,
                SUM(CASE WHEN guild_name IS NULL OR guild_name = '' THEN 1 ELSE 0 END) AS not_in_guild
            FROM public.characters
            WHERE last_save > NOW() - (make_interval(days => %s))
            GROUP BY server_name
            """

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (lookback_in_days,))
            result = cursor.fetchall()
            if not result:
                return {}
            output = {}
            for server_name, in_guild, not_in_guild in result:
                output[str(server_name).lower()] = {
                    "in_guild": in_guild,
                    "not_in_guild": not_in_guild,
                }
            return output


def get_average_population_by_server(
    lookback_in_days: int = 90,
) -> dict[str, Optional[float]]:
    """
    Get the average population per server for the provided lookback.
    """
    validate_lookback(lookback_in_days)
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    server_name,
                    AVG(character_count) AS avg_character_count,
                    AVG(lfm_count) AS avg_lfm_count
                FROM (
                    SELECT
                        jsonb_object_keys(data->'servers') AS server_name,
                        (data->'servers'->jsonb_object_keys(data->'servers')->>'character_count')::int AS character_count,
                        (data->'servers'->jsonb_object_keys(data->'servers')->>'lfm_count')::int AS lfm_count
                    FROM public.game_info
                    WHERE "timestamp" > NOW() - (make_interval(days => %s))
                ) AS sub
                GROUP BY server_name
                ORDER BY server_name
                """,
                (lookback_in_days,),
            )
            result = cursor.fetchall()
            if not result:
                return {}
            output = {}
            for server_name, avg_character_count, avg_lfm_count in result:
                output[server_name] = {
                    "avg_character_count": (
                        float(avg_character_count)
                        if avg_character_count is not None
                        else None
                    ),
                    "avg_lfm_count": (
                        float(avg_lfm_count) if avg_lfm_count is not None else None
                    ),
                }
            return output


def get_average_population_by_hour_per_server(
    lookback_in_days: int = 90,
) -> dict[str, Optional[float]]:
    """
    Gets the average population by hour of the day (0-23) per server for the provided lookback.
    """
    validate_lookback(lookback_in_days)
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    server_name,
                    EXTRACT(HOUR FROM "timestamp") AS hour,
                    AVG(character_count) AS avg_character_count,
                    AVG(lfm_count) AS avg_lfm_count
                FROM (
                    SELECT
                        jsonb_object_keys(data->'servers') AS server_name,
                        (data->'servers'->jsonb_object_keys(data->'servers')->>'character_count')::int AS character_count,
                        (data->'servers'->jsonb_object_keys(data->'servers')->>'lfm_count')::int AS lfm_count,
                        "timestamp"
                    FROM public.game_info
                    WHERE "timestamp" > NOW() - (make_interval(days => %s))
                ) AS sub
                GROUP BY server_name, hour
                ORDER BY server_name, hour
                """,
                (lookback_in_days,),
            )
            result = cursor.fetchall()
            if not result:
                return {}
            output = {}
            for server_name, hour, avg_character_count, avg_lfm_count in result:
                if server_name not in output:
                    output[server_name] = {}
                output[server_name][int(hour)] = {
                    "avg_character_count": (
                        float(avg_character_count)
                        if avg_character_count is not None
                        else None
                    ),
                    "avg_lfm_count": (
                        float(avg_lfm_count) if avg_lfm_count is not None else None
                    ),
                }
            return output


def get_average_population_by_day_of_week_per_server(
    lookback_in_days: int = 90,
) -> dict[str, Optional[float]]:
    """
    Gets the average population by day of the week (0-6, where 0 is Sunday) per server for the provided lookback.
    """
    validate_lookback(lookback_in_days)
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    server_name,
                    EXTRACT(DOW FROM "timestamp") AS day_of_week,
                    AVG(character_count) AS avg_character_count,
                    AVG(lfm_count) AS avg_lfm_count
                FROM (
                    SELECT
                        jsonb_object_keys(data->'servers') AS server_name,
                        (data->'servers'->jsonb_object_keys(data->'servers')->>'character_count')::int AS character_count,
                        (data->'servers'->jsonb_object_keys(data->'servers')->>'lfm_count')::int AS lfm_count,
                        "timestamp"
                    FROM public.game_info
                    WHERE "timestamp" > NOW() - (make_interval(days => %s))
                ) AS sub
                GROUP BY server_name, day_of_week
                ORDER BY server_name, day_of_week
                """,
                (lookback_in_days,),
            )
            result = cursor.fetchall()
            if not result:
                return {}
            output = {}
            for server_name, day_of_week, avg_character_count, avg_lfm_count in result:
                if server_name not in output:
                    output[server_name] = {}
                output[server_name][int(day_of_week)] = {
                    "avg_character_count": (
                        float(avg_character_count)
                        if avg_character_count is not None
                        else None
                    ),
                    "avg_lfm_count": (
                        float(avg_lfm_count) if avg_lfm_count is not None else None
                    ),
                }
            return output


def get_average_population_by_hour_and_day_of_week_per_server(
    lookback_in_days: int = 90,
) -> dict[str, dict[int, dict[int, dict[str, Optional[float]]]]]:
    """
    Gets the average population by hour of the day (0-23) and day of week (0-6, where 0 is Sunday) per server for the provided lookback.
    Returns a nested dict: {server_name: {day_of_week: {hour: {...}}}}
    """
    validate_lookback(lookback_in_days)
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    server_name,
                    EXTRACT(DOW FROM "timestamp") AS day_of_week,
                    EXTRACT(HOUR FROM "timestamp") AS hour,
                    AVG(character_count) AS avg_character_count,
                    AVG(lfm_count) AS avg_lfm_count
                FROM (
                    SELECT
                        jsonb_object_keys(data->'servers') AS server_name,
                        (data->'servers'->jsonb_object_keys(data->'servers')->>'character_count')::int AS character_count,
                        (data->'servers'->jsonb_object_keys(data->'servers')->>'lfm_count')::int AS lfm_count,
                        "timestamp"
                    FROM public.game_info
                    WHERE "timestamp" > NOW() - (make_interval(days => %s))
                ) AS sub
                GROUP BY server_name, day_of_week, hour
                ORDER BY server_name, day_of_week, hour
                """,
                (lookback_in_days,),
            )
            result = cursor.fetchall()
            if not result:
                return {}
            output = {}
            for (
                server_name,
                day_of_week,
                hour,
                avg_character_count,
                avg_lfm_count,
            ) in result:
                if server_name not in output:
                    output[server_name] = {}
                if int(day_of_week) not in output[server_name]:
                    output[server_name][int(day_of_week)] = {}
                output[server_name][int(day_of_week)][int(hour)] = {
                    "avg_character_count": (
                        float(avg_character_count)
                        if avg_character_count is not None
                        else None
                    ),
                    "avg_lfm_count": (
                        float(avg_lfm_count) if avg_lfm_count is not None else None
                    ),
                }
            return output


def get_game_population_relative(days: int = 1) -> list[PopulationPointInTime]:
    """
    Get population info for a relative date range starting at some
    offset number of days ago and ending now.
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    return get_game_population(start_date=start_date, end_date=end_date)


def get_game_population(
    start_date: datetime = None, end_date: datetime = None
) -> list[PopulationPointInTime]:
    """
    Get population info for a range of dates.

    Args:
        start_date: Start datetime (defaults to 1 day ago)
        end_date: End datetime (defaults to now)

    Returns:
        List of population data points within the date range
    """
    # Set defaults
    if not end_date:
        end_date = datetime.now()
    if not start_date:
        start_date = end_date - timedelta(days=1)

    # Validate date range
    if start_date >= end_date:
        raise ValueError("start_date must be before end_date")

    # Limit the range to prevent excessive data retrieval (adjust as needed)
    max_days = 30
    if (end_date - start_date).days > max_days:
        raise ValueError(f"Date range cannot exceed {max_days} days")

    # get all entries from the game_info table
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT timestamp, data
                FROM public.game_info
                WHERE timestamp BETWEEN %s AND %s
                ORDER BY timestamp ASC
                """,
                (
                    start_date.isoformat(),
                    end_date.isoformat(),
                ),
            )
            game_info_list = cursor.fetchall()
            if not game_info_list:
                return []

            population_points: list[PopulationPointInTime] = []
            for game_info in game_info_list:
                try:
                    timestamp = datetime_to_datetime_string(game_info[0])
                    data = ServerInfo(**game_info[1])
                    population_data_points: dict[str, PopulationDataPoint] = {}
                    for server_name, server_info in data.servers.items():
                        character_count = 0
                        lfm_count = 0
                        if server_info:
                            if server_info.character_count:
                                character_count = server_info.character_count
                            if server_info.lfm_count:
                                lfm_count = server_info.lfm_count
                        population_data_point = PopulationDataPoint(
                            character_count=character_count,
                            lfm_count=lfm_count,
                        )
                        population_data_points[server_name] = population_data_point
                    population_point = PopulationPointInTime(
                        timestamp=timestamp, data=population_data_points
                    )
                    population_points.append(population_point)
                except Exception:
                    pass
            return population_points


def add_game_info(game_info: dict):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                serialized_data = {
                    "servers": {
                        server_name: server_info
                        for server_name, server_info in game_info.items()
                    }
                }
                insert_query = """
                    INSERT INTO game_info (data)
                    VALUES (%s)
                    """
                cursor.execute(
                    insert_query,
                    (json.dumps(serialized_data),),
                )
                conn.commit()
            except Exception as e:
                print(f"Failed to add game info to the database: {e}")
                conn.rollback()
                raise e


def add_character_activity(activites: list[dict]):
    insert_query = """
        INSERT INTO character_activity (timestamp, character_id, activity_type, data)
        VALUES (NOW(), %s, %s, %s)
    """
    batch_size = 500
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                batch = []
                for activity in activites:
                    batch.append(
                        (
                            activity.get("character_id"),
                            activity.get("activity_type").value,
                            json.dumps(activity.get("data")),
                        )
                    )
                    if len(batch) >= batch_size:
                        cursor.executemany(insert_query, batch)
                        batch.clear()
                if batch:
                    cursor.executemany(insert_query, batch)
                conn.commit()
            except Exception as e:
                print(f"Failed to add character activity to the database: {e}")
                conn.rollback()
                raise e


def set_character_active_status(
    character_id: int, active: bool, checked_at: datetime | None = None
) -> None:
    """Insert or update a character's active status and last-checked timestamp.

    Uses an upsert so callers don't need to know if a row exists.
    If checked_at is None, the database will set it to NOW().
    """
    query = """
        INSERT INTO public.character_report_status (character_id, active, active_checked_at, updated_at)
        VALUES (%s, %s, COALESCE(%s, NOW()), NOW())
        ON CONFLICT (character_id) DO UPDATE
        SET active = EXCLUDED.active,
            active_checked_at = EXCLUDED.active_checked_at,
            updated_at = NOW()
    """

    with get_db_cursor(commit=True) as cursor:
        cursor.execute(query, (character_id, active, checked_at))


def set_characters_active_status_bulk(
    updates: list[tuple[int, bool, datetime | None]],
) -> int:
    """Bulk upsert active status for many characters.

    Each tuple in updates is (character_id, active, checked_at | None).
    Returns the number of rows processed where available.
    """
    if not updates:
        return 0

    query = """
        INSERT INTO public.character_report_status (character_id, active, active_checked_at, updated_at)
        VALUES (%s, %s, COALESCE(%s, NOW()), NOW())
        ON CONFLICT (character_id) DO UPDATE
        SET active = EXCLUDED.active,
            active_checked_at = EXCLUDED.active_checked_at,
            updated_at = NOW()
    """

    with get_db_cursor(commit=True) as cursor:
        cursor.executemany(query, updates)
        return cursor.rowcount


def get_character_active_status(character_id: int) -> dict | None:
    """Fetch the active status row for a character.

    Returns a dict: {"character_id": int, "active": bool, "active_checked_at": str | None, "updated_at": str | None}
    or None if not present.
    """
    query = """
        SELECT character_id, active, active_checked_at, updated_at
        FROM public.character_report_status
        WHERE character_id = %s
    """

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (character_id,))
            row = cursor.fetchone()
            if not row:
                return None

            return {
                "character_id": int(row[0]),
                "active": bool(row[1]),
                "active_checked_at": (
                    datetime_to_datetime_string(row[2])
                    if isinstance(row[2], datetime)
                    else None
                ),
                "updated_at": (
                    datetime_to_datetime_string(row[3])
                    if isinstance(row[3], datetime)
                    else None
                ),
            }


def get_news() -> list[News]:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM public.news")
            news = cursor.fetchall()
            if not news:
                return []

            return [build_news_from_row(news_item) for news_item in news]


def add_news(news: News) -> News:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                if not news.date or news.date.strip() == "":
                    cursor.execute(
                        """
                        INSERT INTO public.news (message)
                        VALUES (%s)
                        RETURNING id, date
                        """,
                        (news.message,),
                    )
                    result = cursor.fetchone()
                    news_id = result[0]
                    news_date = (
                        result[1].isoformat()
                        if isinstance(result[1], datetime)
                        else result[1]
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO public.news (date, message)
                        VALUES (%s, %s)
                        RETURNING id
                        """,
                        (news.date, news.message),
                    )
                    news_id = cursor.fetchone()[0]
                    news_date = news.date
                conn.commit()
                return News(id=news_id, date=news_date, message=news.message)
            except Exception as e:
                print(f"Failed to add news to the database: {e}")
                conn.rollback()
                raise e


def delete_news(news_id: int):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute("DELETE FROM public.news WHERE id = %s", (news_id,))
                conn.commit()
            except Exception as e:
                print(f"Failed to delete news from the database: {e}")
                conn.rollback()
                raise e


def get_page_messages(page_name: Optional[str] = None) -> list[PageMessage]:
    # TODO: Add support for page_name because right now it doesn't work
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            if page_name:
                cursor.execute(
                    """
                    SELECT * FROM public.page_messages
                    WHERE affected_pages = ANY(%s)
                    """,
                    page_name,
                )
            else:
                cursor.execute("SELECT * FROM public.page_messages")
            page_messages = cursor.fetchall()
            if not page_messages:
                return []

            messages = [
                build_page_message_from_row(message) for message in page_messages
            ]
            filtered_messages = [
                message
                for message in messages
                if datetime.fromisoformat(message.start_date).timestamp()
                < time()
                < datetime.fromisoformat(message.end_date).timestamp()
            ]
            return filtered_messages


def add_page_message(page_message: PageMessage) -> PageMessage:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                # Build dynamic query based on which fields are None
                fields = ["message", "affected_pages"]
                values = [page_message.message, json.dumps(page_message.affected_pages)]
                placeholders = ["%s", "%s"]

                if page_message.start_date is not None:
                    fields.append("start_date")
                    values.append(page_message.start_date)
                    placeholders.append("%s")

                if page_message.end_date is not None:
                    fields.append("end_date")
                    values.append(page_message.end_date)
                    placeholders.append("%s")

                fields_str = ", ".join(fields)
                placeholders_str = ", ".join(placeholders)

                cursor.execute(
                    f"""
                    INSERT INTO public.page_messages ({fields_str})
                    VALUES ({placeholders_str})
                    RETURNING id, start_date, end_date
                    """,
                    values,
                )
                result = cursor.fetchone()
                message_id = result[0]
                returned_start_date = (
                    result[1].isoformat()
                    if isinstance(result[1], datetime)
                    else result[1]
                )
                returned_end_date = (
                    result[2].isoformat()
                    if isinstance(result[2], datetime)
                    else result[2]
                )

                conn.commit()
                return PageMessage(
                    id=message_id,
                    message=page_message.message,
                    affected_pages=page_message.affected_pages,
                    start_date=returned_start_date,
                    end_date=returned_end_date,
                )
            except Exception as e:
                print(f"Failed to add page message to the database: {e}")
                conn.rollback()
                raise e


def build_character_from_row(row: tuple) -> Character:
    return Character(
        id=int(row[0]),
        name=row[1],
        gender=row[2],
        race=row[3],
        total_level=row[4],
        classes=row[5],
        location_id=row[6],
        guild_name=row[7],
        server_name=row[8],
        home_server_name=row[9],
        is_anonymous=row[10],
        last_update=(
            datetime_to_datetime_string(row[11])
            if isinstance(row[11], datetime)
            else ""
        ),
        last_save=(
            datetime_to_datetime_string(row[12])
            if isinstance(row[12], datetime)
            else ""
        ),
    )


def build_news_from_row(row: tuple) -> News:
    return News(
        id=row[0],
        date=(
            datetime_to_datetime_string(row[1]) if isinstance(row[1], datetime) else ""
        ),
        message=row[2],
    )


def build_page_message_from_row(row: tuple) -> PageMessage:
    return PageMessage(
        id=row[0],
        message=row[1],
        affected_pages=row[2],
        dismissable=row[3],
        type=row[4],
        start_date=(
            datetime_to_datetime_string(row[5]) if isinstance(row[5], datetime) else ""
        ),
        end_date=(
            datetime_to_datetime_string(row[6]) if isinstance(row[6], datetime) else ""
        ),
    )


def build_character_activity_summary_from_row(row: tuple) -> CharacterActivitySummary:
    return CharacterActivitySummary(
        level_event_count=row[0],
        location_event_count=row[1],
        guild_name_event_count=row[2],
        server_name_event_count=row[3],
        status_event_count=row[4],
    )


def build_character_quest_activity_from_row(row: tuple) -> CharacterQuestActivity:
    return CharacterQuestActivity(
        timestamp=(
            datetime_to_datetime_string(row[0]) if isinstance(row[0], datetime) else ""
        ),
        quest_id=int(row[1]),
    )


def build_character_raid_activity_from_rows(rows: list[tuple]) -> list[dict]:
    return [
        {
            "timestamp": (
                datetime_to_datetime_string(row[0])
                if isinstance(row[0], datetime)
                else ""
            ),
            "character_id": int(row[1]),
            "data": {
                "quest_ids": (
                    [int(quest_id) for quest_id in row[2] if quest_id is not None]
                    if row[2]
                    else []
                ),
            },
        }
        for row in rows
    ]


def save_access_token(character_id: str, access_token: str):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute(
                    """
                    INSERT INTO public.access_tokens (character_id, access_token)
                    VALUES (%s, %s)
                    ON CONFLICT (character_id) DO UPDATE SET access_token = EXCLUDED.access_token
                    """,
                    (character_id, access_token),
                )
                conn.commit()
            except Exception as e:
                print(f"Failed to save access token to the database: {e}")
                conn.rollback()
                raise e


def get_access_token_by_character_id(character_id: str) -> str:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT access_token FROM public.access_tokens WHERE character_id = %s",
                (character_id,),
            )
            access_token = cursor.fetchone()
            if not access_token:
                return ""
            return access_token[0]


def get_character_id_by_access_token(access_token: str) -> int | None:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT character_id FROM public.access_tokens WHERE access_token = %s",
                (access_token,),
            )
            character_id = cursor.fetchone()
            if not character_id:
                return None
            return character_id[0]


def get_all_quest_names() -> list[str]:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT name FROM public.quests")
            quest_names = cursor.fetchall()
            if not quest_names:
                return []

            return [name for name, in quest_names]


def get_all_quests() -> list[Quest]:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM public.quests")
            quests = cursor.fetchall()
            if not quests:
                return []

            return [build_quest_from_row(quest) for quest in quests]


def get_all_areas() -> list[Area]:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM public.areas")
            areas = cursor.fetchall()
            if not areas:
                return []

            return [build_area_from_row(area) for area in areas]


def get_quest_by_name(name: str) -> Quest | None:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM public.quests WHERE name = %s",
                (name,),
            )
            quest = cursor.fetchone()
            if not quest:
                return None

            return build_quest_from_row(quest)


def get_quest_by_id(id: int) -> Quest | None:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM public.quests WHERE id = %s",
                (id,),
            )
            quest = cursor.fetchone()
            if not quest:
                return None

            return build_quest_from_row(quest)


def update_quest_by_id(id: int, quest: Quest) -> None:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute(
                    """
                    INSERT INTO public.quests (id, alt_id, area_id, name, heroic_normal_cr, epic_normal_cr, is_free_to_vip, required_adventure_pack, adventure_area, quest_journal_area, group_size, patron, xp, length, tip)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                    alt_id = EXCLUDED.alt_id,
                    area_id = EXCLUDED.area_id,
                    name = EXCLUDED.name,
                    heroic_normal_cr = EXCLUDED.heroic_normal_cr,
                    epic_normal_cr = EXCLUDED.epic_normal_cr,
                    is_free_to_vip = EXCLUDED.is_free_to_vip,
                    required_adventure_pack = EXCLUDED.required_adventure_pack,
                    adventure_area = EXCLUDED.adventure_area,
                    quest_journal_area = EXCLUDED.quest_journal_area,
                    group_size = EXCLUDED.group_size,
                    patron = EXCLUDED.patron,
                    xp = EXCLUDED.xp,
                    length = EXCLUDED.length,
                    tip = EXCLUDED.tip
                    """,
                    (
                        id,
                        quest.alt_id,
                        quest.area_id,
                        quest.name,
                        quest.heroic_normal_cr,
                        quest.epic_normal_cr,
                        quest.is_free_to_vip,
                        quest.required_adventure_pack,
                        quest.adventure_area,
                        quest.quest_journal_area,
                        quest.group_size,
                        quest.patron,
                        json.dumps(quest.xp),
                        quest.length,
                        quest.tip,
                    ),
                )
                conn.commit()
            except Exception as e:
                print(f"Failed to save quest to the database: {e}")
                conn.rollback()
                raise e


def update_quests(quests: list[Quest]) -> None:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                for quest in quests:
                    cursor.execute(
                        """
                        INSERT INTO public.quests (id, alt_id, area_id, name, heroic_normal_cr, epic_normal_cr, is_free_to_vip, required_adventure_pack, adventure_area, quest_journal_area, group_size, patron, xp, length, tip)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                        alt_id = EXCLUDED.alt_id,
                        area_id = EXCLUDED.area_id,
                        name = EXCLUDED.name,
                        heroic_normal_cr = EXCLUDED.heroic_normal_cr,
                        epic_normal_cr = EXCLUDED.epic_normal_cr,
                        is_free_to_vip = EXCLUDED.is_free_to_vip,
                        required_adventure_pack = EXCLUDED.required_adventure_pack,
                        adventure_area = EXCLUDED.adventure_area,
                        quest_journal_area = EXCLUDED.quest_journal_area,
                        group_size = EXCLUDED.group_size,
                        patron = EXCLUDED.patron,
                        xp = EXCLUDED.xp,
                        length = EXCLUDED.length,
                        tip = EXCLUDED.tip
                        """,
                        (
                            quest.id,
                            quest.alt_id,
                            quest.area_id,
                            quest.name,
                            quest.heroic_normal_cr,
                            quest.epic_normal_cr,
                            quest.is_free_to_vip,
                            quest.required_adventure_pack,
                            quest.adventure_area,
                            quest.quest_journal_area,
                            quest.group_size,
                            quest.patron,
                            json.dumps(quest.xp),
                            quest.length,
                            quest.tip,
                        ),
                    )
                conn.commit()
            except Exception as e:
                print(f"Failed to save quests to the database: {e}")
                # conn.rollback()
                # raise e


def get_all_area_ids() -> list[int]:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT id FROM public.areas")
            area_ids = cursor.fetchall()
            if not area_ids:
                return []

            return [int(area_id) for (area_id,) in area_ids]


def get_area_by_name(name: str) -> Area | None:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM public.areas WHERE name = %s",
                (name,),
            )
            area = cursor.fetchone()
            if not area:
                return None

            return build_area_from_row(area)


def get_area_by_id(id: int) -> Area | None:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM public.areas WHERE id = %s",
                (id,),
            )
            area = cursor.fetchone()
            if not area:
                return None

            return build_area_from_row(area)


def update_areas(areas_list: list[Area]) -> None:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                for area in areas_list:
                    cursor.execute(
                        """
                        INSERT INTO public.areas (id, name, is_public, is_wilderness, region)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        is_public = EXCLUDED.is_public,
                        is_wilderness = EXCLUDED.is_wilderness,
                        region = EXCLUDED.region
                        """,
                        (
                            area.id,
                            area.name,
                            area.is_public,
                            area.is_wilderness,
                            area.region,
                        ),
                    )
                conn.commit()
            except Exception as e:
                print(f"Failed to save area to the database: {e}")
                conn.rollback()
                raise e


def post_feedback(feedback: FeedbackRequest, ticket: str):
    """Save feedback to the database."""
    feedbackMessage = feedback.message
    feedbackContact = feedback.contact
    feedbackUserId = feedback.user_id if feedback.user_id else None
    feedbackSessionId = feedback.session_id if feedback.session_id else None
    commitHash = feedback.commit_hash if feedback.commit_hash else None

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute(
                    """
                    INSERT INTO public.feedback (message, contact, ticket, user_id, session_id, commit_hash)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        feedbackMessage,
                        feedbackContact,
                        ticket,
                        feedbackUserId,
                        feedbackSessionId,
                        commitHash,
                    ),
                )
                conn.commit()
            except Exception as e:
                print(f"Failed to save feedback to the database: {e}")
                conn.rollback()
                raise e


def persist_log(log: LogRequest):
    """Save log to the database."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute(
                    """
                    INSERT INTO public.logs (message, level, timestamp, component, action, metadata, session_id, user_id, user_agent, browser, browser_version, os, screen_resolution, viewport_size, url, page_title, referrer, route, ip_address, country, is_internal, commit_hash, originating_user_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        log.message,
                        log.level,
                        log.timestamp,
                        log.component,
                        log.action,
                        json.dumps(log.metadata) if log.metadata else None,
                        log.session_id,
                        log.user_id,
                        log.user_agent,
                        log.browser,
                        log.browser_version,
                        log.os,
                        log.screen_resolution,
                        log.viewport_size,
                        log.url,
                        log.page_title,
                        log.referrer,
                        log.route,
                        log.ip_address,
                        log.country,
                        log.is_internal,
                        log.commit_hash,
                        log.originating_user_id,
                    ),
                )
                conn.commit()
            except Exception as e:
                print(f"Failed to save log to the database: {e}")
                conn.rollback()
                raise e


def build_quest_from_row(row: tuple) -> Quest:
    return Quest(
        id=row[0],
        alt_id=row[1],
        area_id=row[2],
        name=row[3],
        heroic_normal_cr=row[4],
        epic_normal_cr=row[5],
        is_free_to_vip=row[6],
        required_adventure_pack=row[7],
        adventure_area=row[8],
        quest_journal_area=row[9],
        group_size=row[10],
        patron=row[11],
        xp=row[12],
        length=row[13],
        tip=row[14],
    )


def build_area_from_row(row: tuple) -> Area:
    return Area(
        id=row[0],
        name=row[1],
        is_public=row[2],
        is_wilderness=row[3],
        region=row[4],
    )


def build_quest_session_from_row(row: tuple) -> QuestSession:
    return QuestSession(
        id=row[0],
        character_id=row[1],
        quest_id=row[2],
        entry_timestamp=row[3],
        exit_timestamp=row[4],
        duration_seconds=float(row[5]) if row[5] is not None else None,
        created_at=row[6],
    )


def get_quest_id_for_area(area_id: int) -> Optional[int]:
    """Get quest_id for a given area_id, or None if area is not associated with a quest."""
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT id FROM public.quests WHERE area_id = %s LIMIT 1
                """,
                (area_id,),
            )
            row = cursor.fetchone()
            return int(row[0]) if row else None


# Add some helper functions for common time ranges
def get_game_population_last_hours(hours: int = 24) -> list[PopulationPointInTime]:
    """Get population data for the last N hours."""
    end_date = datetime.now()
    start_date = end_date - timedelta(hours=hours)
    return get_game_population(start_date=start_date, end_date=end_date)


def get_game_population_today() -> list[PopulationPointInTime]:
    """Get population data for today (from midnight to now)."""
    now = datetime.now()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return get_game_population(start_date=start_of_day, end_date=now)


def get_game_population_yesterday() -> list[PopulationPointInTime]:
    """Get population data for yesterday (full day)."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today - timedelta(days=1)
    yesterday_end = today
    return get_game_population(start_date=yesterday_start, end_date=yesterday_end)


def get_game_population_last_week() -> list[PopulationPointInTime]:
    """Get population data for the last week (full hours)."""
    # End at the start of the current hour
    now = datetime.now()
    end_of_range = now.replace(minute=0, second=0, microsecond=0)
    start_of_range = end_of_range - timedelta(days=7)
    return get_game_population(start_date=start_of_range, end_date=end_of_range)


def get_game_population_last_month() -> list[PopulationPointInTime]:
    """Get population data for the last 28 days (full days)."""
    # End at the start of the current day
    now = datetime.now()
    end_of_range = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_range = end_of_range - timedelta(days=28)
    return get_game_population(start_date=start_of_range, end_date=end_of_range)


def get_game_population_last_quarter() -> list[PopulationPointInTime]:
    """Get population data for the last 90 days (full days)."""
    # End at the start of the current day
    now = datetime.now()
    end_of_range = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_range = end_of_range - timedelta(days=90)
    return get_game_population(start_date=start_of_range, end_date=end_of_range)


def get_game_population_last_year() -> list[PopulationPointInTime]:
    """Get population data for the last year (full days)."""
    # End at the start of the current day
    now = datetime.now()
    end_of_range = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_range = end_of_range - timedelta(days=365)
    return get_game_population(start_date=start_of_range, end_date=end_of_range)


def get_unique_character_and_guild_count(
    days: int = 90, server_name: str = None
) -> dict:
    """
    Get the count of unique characters and guilds within a specified time range and optionally filtered by server.

    Args:
        days: Number of days to look back from now (defaults to 90)
        server_name: Optional server name to filter by. If None, includes all servers.

    Returns:
        Dictionary containing character counts, guild counts, and query parameters used
    """
    try:
        # Calculate the start date (end date is always now)
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        with get_db_cursor(commit=False) as cursor:
            if server_name:
                # Query for specific server including active counts via join
                cursor.execute(
                    """
                    SELECT 
                        COUNT(*) AS unique_character_count,
                        COUNT(DISTINCT c.guild_name) AS unique_guild_count,
                        COUNT(*) FILTER (WHERE crs.active IS TRUE) AS active_unique_character_count,
                        COUNT(DISTINCT c.guild_name) FILTER (WHERE crs.active IS TRUE) AS active_unique_guild_count
                    FROM public.characters c
                    LEFT JOIN public.character_report_status crs
                        ON crs.character_id = c.id
                    WHERE c.last_save >= %s 
                        AND LOWER(c.server_name) = LOWER(%s)
                        AND c.guild_name IS NOT NULL
                        AND c.guild_name != ''
                    """,
                    (start_date, server_name),
                )
                result = cursor.fetchone()
                unique_character_count = result[0] if result else 0
                unique_guild_count = result[1] if result else 0
                active_unique_character_count = result[2] if result else 0
                active_unique_guild_count = result[3] if result else 0
                server_breakdown = {}
            else:
                # Query for all servers - get breakdown (including active counts) and sum for totals
                cursor.execute(
                    """
                    SELECT 
                        c.server_name, 
                        COUNT(*) AS unique_character_count,
                        COUNT(DISTINCT c.guild_name) AS unique_guild_count,
                        COUNT(*) FILTER (WHERE crs.active IS TRUE) AS active_unique_character_count,
                        COUNT(DISTINCT c.guild_name) FILTER (WHERE crs.active IS TRUE) AS active_unique_guild_count
                    FROM public.characters c
                    LEFT JOIN public.character_report_status crs
                        ON crs.character_id = c.id
                    WHERE c.last_save >= %s
                        AND c.server_name IS NOT NULL
                    GROUP BY c.server_name
                    ORDER BY unique_character_count DESC
                    """,
                    (start_date,),
                )

                server_results = cursor.fetchall()
                server_breakdown = (
                    {
                        str(server).lower(): {
                            "unique_character_count": char_count,
                            "unique_guild_count": guild_count,
                            "active_unique_character_count": active_char_count,
                            "active_unique_guild_count": active_guild_count,
                        }
                        for (
                            server,
                            char_count,
                            guild_count,
                            active_char_count,
                            active_guild_count,
                        ) in server_results
                    }
                    if server_results
                    else {}
                )

                # Calculate totals by summing the breakdown
                unique_character_count = (
                    sum(
                        data["unique_character_count"]
                        for data in server_breakdown.values()
                    )
                    if server_breakdown
                    else 0
                )
                unique_guild_count = (
                    sum(
                        data["unique_guild_count"] for data in server_breakdown.values()
                    )
                    if server_breakdown
                    else 0
                )
                active_unique_character_count = (
                    sum(
                        data["active_unique_character_count"]
                        for data in server_breakdown.values()
                    )
                    if server_breakdown
                    else 0
                )
                active_unique_guild_count = (
                    sum(
                        data["active_unique_guild_count"]
                        for data in server_breakdown.values()
                    )
                    if server_breakdown
                    else 0
                )

            return {
                "unique_character_count": unique_character_count,
                "unique_guild_count": unique_guild_count,
                "active_unique_character_count": active_unique_character_count,
                "active_unique_guild_count": active_unique_guild_count,
                "days_analyzed": days,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "server_name": server_name,
                "server_breakdown": server_breakdown if not server_name else None,
            }

    except Exception as e:
        logger.error(f"Error getting unique character count: {e}")
        return {
            "unique_character_count": 0,
            "unique_guild_count": 0,
            "active_unique_character_count": 0,
            "active_unique_guild_count": 0,
            "error": str(e),
            "days_analyzed": days,
            "server_name": server_name,
        }


def get_character_activity_stats(days: int = 90, server_name: str = None) -> dict:
    """
    Get comprehensive character activity statistics within a time range.

    Args:
        days: Number of days to look back from now (defaults to 90)
        server_name: Optional server name to filter by. If None, includes all servers.

    Returns:
        Dictionary containing various character activity metrics
    """
    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)

        with get_db_cursor(commit=False) as cursor:
            # Base WHERE clause
            base_where = "WHERE last_save >= %s"
            params = [start_date]

            if server_name:
                base_where += " AND LOWER(server_name) = LOWER(%s)"
                params.append(server_name)

            # Get unique character count and various metrics
            cursor.execute(
                f"""
                SELECT 
                    COUNT(*) as unique_characters,
                    COUNT(*) as total_character_records,
                    COUNT(DISTINCT server_name) as servers_with_activity,
                    MIN(last_save) as earliest_activity,
                    MAX(last_save) as latest_activity,
                    AVG(total_level) as avg_character_level
                FROM public.characters 
                {base_where}
                """,
                params,
            )

            stats = cursor.fetchone()

            # Get level distribution
            cursor.execute(
                f"""
                SELECT 
                    CASE 
                        WHEN total_level >= 30 THEN '30+'
                        WHEN total_level >= 20 THEN '20-29'
                        WHEN total_level >= 10 THEN '10-19'
                        WHEN total_level >= 1 THEN '1-9'
                        ELSE 'Unknown'
                    END as level_range,
                    COUNT(*) as character_count
                FROM public.characters 
                {base_where}
                GROUP BY level_range
                ORDER BY level_range
                """,
                params,
            )

            level_results = cursor.fetchall()
            level_distribution = {
                level_range: count for level_range, count in level_results
            }

            # Get server breakdown if no specific server requested
            server_breakdown = {}
            if not server_name:
                cursor.execute(
                    f"""
                    SELECT 
                        server_name,
                        COUNT(*) as unique_characters,
                        AVG(total_level) as avg_level
                    FROM public.characters 
                    {base_where}
                        AND server_name IS NOT NULL
                    GROUP BY server_name
                    ORDER BY unique_characters DESC
                    """,
                    params,
                )

                server_results = cursor.fetchall()
                server_breakdown = {
                    str(server).lower(): {
                        "unique_characters": count,
                        "avg_level": round(float(avg_level), 1) if avg_level else 0,
                    }
                    for server, count, avg_level in server_results
                }

            return {
                "unique_characters": stats[0] if stats else 0,
                "total_character_records": stats[1] if stats else 0,
                "servers_with_activity": stats[2] if stats else 0,
                "earliest_activity": (
                    stats[3].isoformat() if stats and stats[3] else None
                ),
                "latest_activity": stats[4].isoformat() if stats and stats[4] else None,
                "avg_character_level": (
                    round(float(stats[5]), 1) if stats and stats[5] else 0
                ),
                "level_distribution": level_distribution,
                "server_breakdown": server_breakdown if not server_name else None,
                "query_parameters": {
                    "days_analyzed": days,
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                    "server_name": server_name,
                },
            }

    except Exception as e:
        logger.error(f"Error getting character activity stats: {e}")
        return {
            "unique_characters": 0,
            "error": str(e),
            "query_parameters": {"days_analyzed": days, "server_name": server_name},
        }


def get_guilds_by_name(guild_name: str) -> list[dict]:
    """
    Gets the guild name, server name, character count, and average last update time for the top 10% of characters in a guild.
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # Use exact match for short guild names, partial match otherwise
            if len(guild_name) <= 3:
                match_value = guild_name
            else:
                match_value = f"%{guild_name}%"

            cursor.execute(
                """
                WITH ranked_characters AS (
                    SELECT 
                        guild_name, 
                        server_name, 
                        last_update,
                        EXTRACT(EPOCH FROM last_update) AS last_update_epoch,
                        ROW_NUMBER() OVER (PARTITION BY guild_name, server_name ORDER BY last_update DESC) AS rn,
                        COUNT(*) OVER (PARTITION BY guild_name, server_name) AS total_count
                    FROM public.characters
                    WHERE guild_name ILIKE %s
                ), top_10_percent AS (
                    SELECT 
                        guild_name, 
                        server_name,
                        last_update_epoch,
                        total_count
                    FROM ranked_characters
                    WHERE rn <= GREATEST(1, CEIL(total_count * 0.1))
                )
                SELECT 
                    guild_name, 
                    server_name, 
                    MAX(total_count) as character_count,
                    AVG(last_update_epoch) as avg_top_10_percent_last_update_epoch
                FROM top_10_percent
                GROUP BY guild_name, server_name
                ORDER BY character_count DESC
                LIMIT 20
            """,
                (match_value,),
            )
            guilds = cursor.fetchall()
            if not guilds:
                return []
            return [
                {
                    "guild_name": row[0],
                    "server_name": row[1],
                    "character_count": row[2],
                    "avg_top_10_percent_last_update_epoch": row[3],
                }
                for row in guilds
            ]


def get_guild_by_server_name_and_guild_name(
    server_name: str, guild_name: str
) -> dict | None:
    """
    Get a specific guild by server name and guild name with its character count.

    Args:
        server_name: The server name to filter by
        guild_name: The exact guild name to filter by
    Returns:
        Dictionary containing guild details or None if not found
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT 
                    guild_name, 
                    server_name, 
                    COUNT(*) as character_count
                FROM public.characters
                WHERE LOWER(server_name) = LOWER(%s)
                    AND LOWER(guild_name) = LOWER(%s)
                    AND guild_name IS NOT NULL AND guild_name != ''
                GROUP BY guild_name, server_name
                LIMIT 1
            """,
                (server_name, guild_name),
            )
            guild = cursor.fetchone()
            if not guild:
                return None
            return {
                "guild_name": guild[0],
                "server_name": guild[1],
                "character_count": guild[2],
            }


def get_all_guilds() -> list[dict]:
    """
    Get all unique guilds, including server name and the total number of characters in each guild.
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT 
                    guild_name, 
                    server_name, 
                    COUNT(*) as character_count
                FROM public.characters
                WHERE guild_name IS NOT NULL AND guild_name != ''
                GROUP BY guild_name, server_name
                ORDER BY character_count DESC
            """
            )
            guilds = cursor.fetchall()
            if not guilds:
                return []
            return [
                {
                    "guild_name": row[0],
                    "server_name": row[1],
                    "character_count": row[2],
                }
                for row in guilds
            ]


def get_config() -> dict:
    """
    Get all configuration settings from the database.

    Returns:
        Dictionary containing all configuration settings
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT * FROM public.config")
            config_rows = cursor.fetchall()
            if not config_rows:
                return {}

            return {
                row[0]: {
                    "key": row[0],
                    "value": row[1],
                    "description": row[2],
                    "is_enabled": row[3],
                    "created_date": (
                        datetime_to_datetime_string(row[4])
                        if isinstance(row[4], datetime)
                        else row[4]
                    ),
                    "modified_date": (
                        datetime_to_datetime_string(row[5])
                        if isinstance(row[5], datetime)
                        else row[5]
                    ),
                }
                for row in config_rows
            }


def get_config_by_key(key: str) -> dict | None:
    """
    Get a specific configuration setting by key.

    Args:
        key: The configuration key to retrieve

    Returns:
        Dictionary containing the configuration setting or None if not found
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM public.config WHERE key = %s",
                (key,),
            )
            config_row = cursor.fetchone()
            if not config_row:
                return None

            return {
                "key": config_row[0],
                "value": config_row[1],
                "description": config_row[2],
                "is_enabled": config_row[3],
                "created_date": (
                    datetime_to_datetime_string(config_row[4])
                    if isinstance(config_row[4], datetime)
                    else config_row[4]
                ),
                "modified_date": (
                    datetime_to_datetime_string(config_row[5])
                    if isinstance(config_row[5], datetime)
                    else config_row[5]
                ),
            }


# def get_game_population_by_date_strings(
#     start_date_str: str = None, end_date_str: str = None, date_format: str = "%Y-%m-%d"
# ) -> list[PopulationPointInTime]:
#     """
#     Get population info using date strings.

#     Args:
#         start_date_str: Start date as string (e.g., "2025-07-01")
#         end_date_str: End date as string (e.g., "2025-07-10")
#         date_format: Format of the date strings (defaults to "%Y-%m-%d")

#     Returns:
#         List of population data points within the date range
#     """
#     start_date = None
#     end_date = None

#     try:
#         if start_date_str:
#             start_date = datetime.strptime(start_date_str, date_format)
#         if end_date_str:
#             end_date = datetime.strptime(end_date_str, date_format)
#     except ValueError as e:
#         raise ValueError(f"Invalid date format. Expected format: {date_format}. Error: {e}")

#     return get_game_population(start_date=start_date, end_date=end_date)


# ========================================
# Quest Session Functions
# ========================================


def insert_quest_session(
    character_id: int,
    quest_id: int,
    entry_timestamp: datetime,
    exit_timestamp: Optional[datetime] = None,
) -> int:
    """Insert a new quest session and return its ID.

    Args:
        character_id: ID of the character
        quest_id: ID of the quest
        entry_timestamp: When the character entered the quest
        exit_timestamp: When the character exited (None for active sessions)

    Returns:
        The ID of the newly created quest session
    """
    query = """
        INSERT INTO public.quest_sessions 
        (character_id, quest_id, entry_timestamp, exit_timestamp)
        VALUES (%s, %s, %s, %s)
        RETURNING id
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query, (character_id, quest_id, entry_timestamp, exit_timestamp)
            )
            result = cursor.fetchone()
            conn.commit()
            return result[0] if result else None


def update_quest_session_exit(session_id: int, exit_timestamp: datetime) -> None:
    """Update a quest session with an exit timestamp (closing the session).

    Args:
        session_id: ID of the quest session to update
        exit_timestamp: When the character exited the quest
    """
    query = """
        UPDATE public.quest_sessions 
        SET exit_timestamp = %s
        WHERE id = %s
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (exit_timestamp, session_id))
            conn.commit()


def get_active_quest_session(character_id: int) -> Optional[QuestSession]:
    """Get the currently active quest session for a character.

    Args:
        character_id: ID of the character

    Returns:
        QuestSession if one is active, None otherwise
    """
    query = """
        SELECT id, character_id, quest_id, entry_timestamp, exit_timestamp, 
               duration_seconds, created_at
        FROM public.quest_sessions
        WHERE character_id = %s AND exit_timestamp IS NULL
        ORDER BY entry_timestamp DESC
        LIMIT 1
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, (character_id,))
            row = cursor.fetchone()
            if not row:
                return None

            return QuestSession(
                id=row[0],
                character_id=row[1],
                quest_id=row[2],
                entry_timestamp=row[3],
                exit_timestamp=row[4],
                duration_seconds=float(row[5]) if row[5] is not None else None,
                created_at=row[6],
            )


def get_quest_sessions_by_quest(
    quest_id: int,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> list[QuestSession]:
    """Get all quest sessions for a specific quest within a date range.

    Args:
        quest_id: ID of the quest
        start_date: Optional start date filter
        end_date: Optional end date filter

    Returns:
        List of QuestSession objects
    """
    query = """
        SELECT id, character_id, quest_id, entry_timestamp, exit_timestamp, 
               duration_seconds, created_at
        FROM public.quest_sessions
        WHERE quest_id = %s
    """
    params = [quest_id]

    if start_date:
        query += " AND entry_timestamp >= %s"
        params.append(start_date)
    if end_date:
        query += " AND entry_timestamp <= %s"
        params.append(end_date)

    query += " ORDER BY entry_timestamp DESC"

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

            return [
                QuestSession(
                    id=row[0],
                    character_id=row[1],
                    quest_id=row[2],
                    entry_timestamp=row[3],
                    exit_timestamp=row[4],
                    duration_seconds=float(row[5]) if row[5] is not None else None,
                    created_at=row[6],
                )
                for row in rows
            ]


def get_quest_sessions_by_character(
    character_id: int,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
) -> list[QuestSession]:
    """Get all quest sessions for a specific character within a date range.

    Args:
        character_id: ID of the character
        start_date: Optional start date filter
        end_date: Optional end date filter

    Returns:
        List of QuestSession objects
    """
    query = """
        SELECT id, character_id, quest_id, entry_timestamp, exit_timestamp, 
               duration_seconds, created_at
        FROM public.quest_sessions
        WHERE character_id = %s
    """
    params = [character_id]

    if start_date:
        query += " AND entry_timestamp >= %s"
        params.append(start_date)
    if end_date:
        query += " AND entry_timestamp <= %s"
        params.append(end_date)

    query += " ORDER BY entry_timestamp DESC"

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

            return [
                QuestSession(
                    id=row[0],
                    character_id=row[1],
                    quest_id=row[2],
                    entry_timestamp=row[3],
                    exit_timestamp=row[4],
                    duration_seconds=float(row[5]) if row[5] is not None else None,
                    created_at=row[6],
                )
                for row in rows
            ]


def get_quest_analytics(quest_id: int, lookback_days: int = 90) -> QuestAnalytics:
    """Get comprehensive analytics for a quest.

    Args:
        quest_id: ID of the quest
        lookback_days: Number of days to look back (default 90)

    Returns:
        QuestAnalytics object with duration stats and activity patterns
    """
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            # Get basic statistics
            cursor.execute(
                """
                SELECT 
                    AVG(duration_seconds) as avg_duration,
                    STDDEV(duration_seconds) as stddev_duration,
                    COUNT(*) as total_sessions,
                    COUNT(exit_timestamp) as completed_sessions,
                    COUNT(*) - COUNT(exit_timestamp) as active_sessions
                FROM public.quest_sessions
                WHERE quest_id = %s AND entry_timestamp >= %s
                """,
                (quest_id, cutoff_date),
            )
            stats = cursor.fetchone()

            avg_duration = float(stats[0]) if stats[0] is not None else None
            stddev_duration = float(stats[1]) if stats[1] is not None else None
            total_sessions = int(stats[2]) if stats[2] else 0
            completed_sessions = int(stats[3]) if stats[3] else 0
            active_sessions = int(stats[4]) if stats[4] else 0

            # Get histogram data (only completed sessions)
            cursor.execute(
                """
                WITH bins AS (
                    SELECT 
                        CASE 
                            WHEN duration_seconds < 300 THEN 1
                            WHEN duration_seconds < 600 THEN 2
                            WHEN duration_seconds < 900 THEN 3
                            WHEN duration_seconds < 1800 THEN 4
                            WHEN duration_seconds < 3600 THEN 5
                            WHEN duration_seconds < 7200 THEN 6
                            ELSE 7
                        END as bin,
                        COUNT(*) as count
                    FROM public.quest_sessions
                    WHERE quest_id = %s 
                      AND entry_timestamp >= %s
                      AND exit_timestamp IS NOT NULL
                    GROUP BY bin
                )
                SELECT bin, count FROM bins ORDER BY bin
                """,
                (quest_id, cutoff_date),
            )
            histogram_rows = cursor.fetchall()

            # Define bin ranges
            bin_ranges = [
                (0, 300, "0-5 min"),
                (300, 600, "5-10 min"),
                (600, 900, "10-15 min"),
                (900, 1800, "15-30 min"),
                (1800, 3600, "30-60 min"),
                (3600, 7200, "1-2 hr"),
                (7200, float("inf"), "2+ hr"),
            ]

            histogram = []
            for row in histogram_rows:
                bin_num = row[0]
                count = row[1]
                if 1 <= bin_num <= len(bin_ranges):
                    bin_start, bin_end, _ = bin_ranges[bin_num - 1]
                    histogram.append(
                        {
                            "bin_start": bin_start,
                            "bin_end": bin_end if bin_end != float("inf") else None,
                            "count": count,
                        }
                    )

            # Get activity by hour
            cursor.execute(
                """
                SELECT EXTRACT(HOUR FROM entry_timestamp)::int as hour, COUNT(*) as count
                FROM public.quest_sessions
                WHERE quest_id = %s AND entry_timestamp >= %s
                GROUP BY hour
                ORDER BY hour
                """,
                (quest_id, cutoff_date),
            )
            activity_by_hour = [
                {"hour": int(row[0]), "count": int(row[1])} for row in cursor.fetchall()
            ]

            # Get activity by day of week (convert PostgreSQL's 0=Sunday to 0=Monday)
            cursor.execute(
                """
                SELECT 
                    (EXTRACT(DOW FROM entry_timestamp)::int + 6) % 7 as day,
                    COUNT(*) as count
                FROM public.quest_sessions
                WHERE quest_id = %s AND entry_timestamp >= %s
                GROUP BY day
                ORDER BY day
                """,
                (quest_id, cutoff_date),
            )
            day_names = [
                "Monday",
                "Tuesday",
                "Wednesday",
                "Thursday",
                "Friday",
                "Saturday",
                "Sunday",
            ]
            dow_rows = cursor.fetchall()
            activity_by_day_of_week = [
                {
                    "day": int(row[0]),
                    "day_name": day_names[int(row[0])],
                    "count": int(row[1]),
                }
                for row in dow_rows
            ]

            # Get activity over time (daily)
            cursor.execute(
                """
                SELECT DATE(entry_timestamp) as date, COUNT(*) as count
                FROM public.quest_sessions
                WHERE quest_id = %s AND entry_timestamp >= %s
                GROUP BY date
                ORDER BY date
                """,
                (quest_id, cutoff_date),
            )
            activity_over_time = [
                {"date": row[0].strftime("%Y-%m-%d"), "count": int(row[1])}
                for row in cursor.fetchall()
            ]

            return QuestAnalytics(
                average_duration_seconds=avg_duration,
                standard_deviation_seconds=stddev_duration,
                histogram=histogram,
                activity_by_hour=activity_by_hour,
                activity_by_day_of_week=activity_by_day_of_week,
                activity_over_time=activity_over_time,
                total_sessions=total_sessions,
                completed_sessions=completed_sessions,
                active_sessions=active_sessions,
            )


def bulk_insert_quest_sessions(sessions: list[tuple]) -> None:
    """Bulk insert quest sessions for efficient batch processing.

    Args:
        sessions: List of tuples (character_id, quest_id, entry_timestamp, exit_timestamp)
    """
    if not sessions:
        return

    query = """
        INSERT INTO public.quest_sessions 
        (character_id, quest_id, entry_timestamp, exit_timestamp)
        VALUES (%s, %s, %s, %s)
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(query, sessions)
            conn.commit()


def mark_activities_as_processed(activity_ids: list[tuple]) -> None:
    """Mark character activities as processed for quest session tracking.

    Args:
        activity_ids: List of tuples (character_id, timestamp) identifying activities
    """
    if not activity_ids:
        return

    query = """
        UPDATE public.character_activity 
        SET quest_session_processed = true
        WHERE character_id = %s AND timestamp = %s
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.executemany(query, activity_ids)
            conn.commit()


def get_unprocessed_location_activities(
    last_timestamp: datetime, shard_count: int, shard_index: int, batch_size: int
) -> list[tuple]:
    """Fetch unprocessed location activities for quest session processing.

    Args:
        last_timestamp: Start processing from this timestamp
        shard_count: Total number of shards
        shard_index: Current shard index (0-based)
        batch_size: Maximum number of activities to return

    Returns:
        List of tuples (character_id, timestamp, area_id)
    """
    query = """
        SELECT character_id, timestamp, (data->>'value')::int as area_id
        FROM public.character_activity
        WHERE timestamp > %s
          AND activity_type = 'location'
          AND quest_session_processed = false
          AND (character_id %% %s) = %s
        ORDER BY character_id, timestamp ASC
        LIMIT %s
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                query, (last_timestamp, shard_count, shard_index, batch_size)
            )
            return cursor.fetchall()
