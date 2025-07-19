import json
import os
import logging
from contextlib import contextmanager, asynccontextmanager
from datetime import datetime, timedelta
from time import time
from typing import Optional, Generator, AsyncGenerator

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
    MAX_CHARACTER_ACTIVITY_READ_HISTORY,
)

from models.quest import Quest
from models.area import Area

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
            conn = self._connection_pool.getconn()

            if conn is None:
                raise ConnectionError("Failed to get connection from pool")

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
            raise e
        finally:
            if conn:
                try:
                    self._connection_pool.putconn(conn)
                except Exception as putconn_error:
                    logger.error(f"Error returning connection to pool: {putconn_error}")

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
            # Note: psycopg2 SimpleConnectionPool doesn't expose detailed stats
            # This is a basic implementation
            return {
                "min_connections": POSTGRES_MIN_CONN,
                "max_connections": POSTGRES_MAX_CONN,
                "initialized": self._is_initialized,
            }
        except Exception as e:
            logger.error(f"Error getting pool stats: {e}")
            return {"error": str(e)}

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


def health_check():
    """Add database performance monitoring information."""
    try:
        stats = get_postgres_pool_stats()
        health_status = postgres_health_check()

        # Add basic performance metrics
        with get_db_cursor(commit=False) as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE state = 'active'"
            )
            active_connections = cursor.fetchone()[0]

            cursor.execute("SELECT pg_database_size(current_database())")
            db_size = cursor.fetchone()[0]

        return {
            "database": {
                "healthy": health_status,
                "active_connections": active_connections,
                "database_size_bytes": db_size,
                "pool_stats": stats,
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

    THIS IS EXPENSIVE! Don't use this unless there's a good reason to.
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
        end_date = start_date + timedelta(days=MAX_CHARACTER_ACTIVITY_READ_HISTORY)

    limit = max(
        1,
        min(
            limit if limit is not None else MAX_CHARACTER_ACTIVITY_READ_LENGTH,
            MAX_CHARACTER_ACTIVITY_READ_LENGTH,
        ),
    )

    with get_db_connection() as conn:
        # TODO: use datetime_to_datetime_string ?
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT timestamp, character_id, data
                FROM public.character_activity
                WHERE id = %s AND activity_type = %s AND timestamp BETWEEN %s AND %s
                ORDER BY timestamp DESC
                LIMIT %s
                """,
                (
                    character_id,
                    activity_Type.name,
                    start_date.isoformat(),
                    end_date.isoformat(),
                    limit,
                ),
            )
            activity = cursor.fetchall()
            if not activity:
                return []

            return build_character_activity_from_rows(activity)


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

            return build_character_activity_from_rows(activities)


def get_recent_raid_activity_by_character_ids(
    character_ids: list[int],
) -> list[dict]:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT timestamp, public.character_activity.character_id, quests.id as quest_id FROM public.character_activity
                LEFT JOIN public.quests ON public.quests.area_id = CAST(public.character_activity.data ->> 'value' as INTEGER)
                WHERE quests.group_size = 'Raid' AND character_activity.character_id = ANY(%s) AND character_activity.activity_type = 'location' AND timestamp >= NOW() - INTERVAL '5 days'
                ORDER BY timestamp DESC
                LIMIT 100
                """,
                (character_ids,),
            )
            activities = cursor.fetchall()
            if not activities:
                return []

            return build_character_activity_from_rows(activities)


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
        start_date=(
            datetime_to_datetime_string(row[3]) if isinstance(row[3], datetime) else ""
        ),
        end_date=(
            datetime_to_datetime_string(row[4]) if isinstance(row[4], datetime) else ""
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


def build_character_activity_from_rows(rows: list[tuple]) -> list[dict]:
    return [
        {
            "timestamp": (
                datetime_to_datetime_string(row[0])
                if isinstance(row[0], datetime)
                else ""
            ),
            "character_id": int(row[1]),
            "data": row[2],
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

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute(
                    """
                    INSERT INTO public.feedback (message, contact, ticket)
                    VALUES (%s, %s, %s)
                    """,
                    (
                        feedbackMessage,
                        feedbackContact,
                        ticket,
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
                    INSERT INTO public.logs (message, level, timestamp, component, action, metadata, session_id, user_id, user_agent, browser, browser_version, os, screen_resolution, viewport_size, url, page_title, referrer, route, ip_address, country)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
    """Get population data for the last week (full days)."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today - timedelta(days=7)
    yesterday_end = today
    return get_game_population(start_date=yesterday_start, end_date=yesterday_end)


def get_game_population_last_month() -> list[PopulationPointInTime]:
    """Get population data for the last week (full days)."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_start = today - timedelta(days=28)
    yesterday_end = today
    return get_game_population(start_date=yesterday_start, end_date=yesterday_end)


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
