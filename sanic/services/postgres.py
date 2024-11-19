import json
import os
from contextlib import contextmanager
from typing import Optional
from time import time

from models.character import Character, CharacterActivity
from models.redis import GameInfo
from models.game import PopulationPointInTime, PopulationDataPoint
from models.service import News, PageMessage

from psycopg2 import pool  # type: ignore

# Load environment variables
DB_CONFIG = {
    "dbname": os.getenv("POSTGRES_DB"),
    "host": os.getenv("POSTGRES_HOST"),
    "port": int(os.getenv("POSTGRES_PORT")),
    "user": os.getenv("POSTGRES_USER"),
    "password": os.getenv("POSTGRES_PASSWORD"),
}
DB_MIN_CONN = int(os.getenv("POSTGRES_MIN_CONN"))
DB_MAX_CONN = int(os.getenv("POSTGRES_MAX_CONN"))


class PostgresSingleton:
    _instance = None
    client: pool.SimpleConnectionPool

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            print("Creating PostgreSQL connection pool...")
            cls._instance = super(PostgresSingleton, cls).__new__(cls, *args, **kwargs)
            cls._instance.client = pool.SimpleConnectionPool(
                minconn=DB_MIN_CONN,
                maxconn=DB_MAX_CONN,
                **DB_CONFIG,
            )
        return cls._instance

    def close(self):
        self.client.closeall()

    def get_client(self):
        return self.client


postgres_singleton = PostgresSingleton()


def get_postgres_client() -> pool.SimpleConnectionPool:
    return postgres_singleton.get_client()


def close_postgres_client() -> None:
    postgres_singleton.close()


@contextmanager
def get_db_connection():
    conn = postgres_singleton.get_client().getconn()
    try:
        yield conn
    finally:
        postgres_singleton.get_client().putconn(conn)


def add_or_update_characters(characters: list[Character]) -> None:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                for character in characters:
                    character_dump = character.model_dump()
                    exclude_fields = ["public_comment", "is_online"]
                    character_fields = [
                        field
                        for field in character_dump.keys()
                        if field not in exclude_fields
                    ]

                    # Construct the query dynamically
                    columns = ", ".join(character_fields)
                    placeholders = ", ".join(["%s"] * len(character_fields))
                    updates = ", ".join(
                        [f"{field} = EXCLUDED.{field}" for field in character_fields]
                    )

                    query = f"""
                        INSERT INTO characters ({columns})
                        VALUES ({placeholders})
                        ON CONFLICT (id) DO UPDATE SET
                        {updates}
                    """

                    # Get the values of the Character model
                    values = [
                        json.dumps(value) if isinstance(value, (dict, list)) else value
                        for key, value in character_dump.items()
                        if key in character_fields
                    ]

                    cursor.execute(query, values)
                conn.commit()
            except Exception as e:
                print(f"Failed to commit changes to the database: {e}")
                conn.rollback()


def add_or_update_character_activities(activity_entries: list[dict]) -> None:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                for activity_entry in activity_entries:
                    # timestamp = activity_entry.get("timestamp")
                    character_id = activity_entry.get("id")
                    activity_type = activity_entry.get("type")
                    data = activity_entry.get("data")

                    cursor.execute(
                        """
                        INSERT INTO public.character_activity (timestamp, id, activity_type, data)
                        VALUES (NOW(), %s, %s, %s)
                        """,
                        (character_id, activity_type, json.dumps(data)),
                    )
                conn.commit()
            except Exception as e:
                print(
                    f"Failed to add or update character activity in the database: {e}"
                )
                conn.rollback()


def get_character_by_id(character_id: str) -> Character:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute(
                    "SELECT * FROM public.characters WHERE id = %s", (character_id,)
                )
                character = cursor.fetchone()
                if character:
                    return build_character_from_row(character)
            except Exception as e:
                print(f"Failed to get character by ID from the database: {e}")
    return {}


def get_character_by_name_and_server(
    character_name: str, server_name: str
) -> Character:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute(
                    "SELECT * FROM public.characters WHERE LOWER(name) = %s AND LOWER(server_name) = %s",
                    (character_name.lower(), server_name.lower()),
                )
                character = cursor.fetchone()
                if character:
                    return build_character_from_row(character)
            except Exception as e:
                print(
                    f"Failed to get character by name and server from the database: {e}"
                )
    return {}


def get_character_activity_summary_by_character_id(character_id: str) -> dict:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
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
                if activity:
                    return build_character_activity_summary_from_row(activity)
            except Exception as e:
                print(
                    f"Failed to get character activity by character ID from the database: {e}"
                )
    return {}


def get_character_activity_field_by_character_id(
    character_id: str, field: str
) -> list[dict]:

    # Validate the field
    VALID_FIELDS = [
        "total_level",
        "location",
        "guild_name",
        "server_name",
        "is_online",
    ]
    if field not in VALID_FIELDS:
        return []

    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute(
                    f"""
                    SELECT {field}
                    FROM public.character_activity
                    WHERE id = %s
                    """,
                    (character_id,),
                )
                activity = cursor.fetchone()
                if activity:
                    return activity[0]
            except Exception as e:
                print(
                    f"Failed to get character activity field by character ID from the database: {e}"
                )
    return []


def get_game_population(start_date: str = None, end_date: str = None) -> list[dict]:
    if not start_date:
        start_date = "NOW() - INTERVAL '1 day'"
    if not end_date:
        end_date = "NOW()"

    # get all entries from the game_info table
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute(
                    f"""
                    SELECT
                        EXTRACT(EPOCH FROM timestamp) as timestamp,
                        data
                    FROM public.game_info
                    WHERE timestamp BETWEEN {start_date} AND {end_date}
                    """
                )
                game_info_list = cursor.fetchall()
                population_points: list[dict] = []
                for game_info in game_info_list:
                    try:
                        timestamp = game_info[0]
                        data = GameInfo(**game_info[1])
                        population_data_points: list[dict[str, PopulationDataPoint]] = (
                            []
                        )
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
                            population_data_points.append(
                                {server_name: population_data_point}
                            )
                        population_point = PopulationPointInTime(
                            timestamp=timestamp, data=population_data_points
                        )
                        population_points.append(population_point.model_dump())
                    except Exception:
                        pass
                return population_points
            except Exception as e:
                print(f"Failed to get game population from the database: {e}")


def add_game_info(game_info: GameInfo):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                insert_query = """
                    INSERT INTO game_info (data)
                    VALUES (%s)
                    """
                cursor.execute(
                    insert_query,
                    (game_info.model_dump_json(),),
                )
                conn.commit()
            except Exception as e:
                print(f"Failed to add game info to the database: {e}")
                conn.rollback()


def add_character_activity(game_activity: dict[str, list[CharacterActivity]]):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                for server_activity in game_activity.values():
                    for activity in server_activity:
                        insert_query = """
                            INSERT INTO character_activity (timestamp, id, activity_type, data)
                            VALUES (NOW(), %s, %s, %s)
                        """
                        cursor.execute(
                            insert_query,
                            (
                                activity.id,
                                activity.activity_type.name,
                                json.dumps(activity.data),
                            ),
                        )
                conn.commit()
            except Exception as e:
                print(f"Failed to add character activity to the database: {e}")
                conn.rollback()


def get_news() -> list[News]:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                cursor.execute("SELECT * FROM public.news")
                news = cursor.fetchall()
                return [build_news_from_row(news_item) for news_item in news]
            except Exception as e:
                print(f"Failed to get news from the database: {e}")
    return []


def get_page_messages(page_name: Optional[str] = None) -> list[PageMessage]:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                if page_name:
                    cursor.execute(
                        f"""
                        SELECT * FROM public.page_messages
                        WHERE page_name = ANY(%s)
                        """,
                        page_name,
                    )
                else:
                    cursor.execute("SELECT * FROM public.page_messages")
                page_messages = cursor.fetchall()
                messages = [
                    build_page_message_from_row(message) for message in page_messages
                ]
                filtered_messages = [
                    message
                    for message in messages
                    if message.start_date.timestamp()
                    < time()
                    < message.end_date.timestamp()
                ]
                return filtered_messages
            except Exception as e:
                print(f"Failed to get page messages from the database: {e}")
    return []


def build_character_from_row(row: tuple) -> Character:
    character_data = {
        "id": str(row[0]),
        "name": row[1],
        "gender": row[2],
        "race": row[3],
        "total_level": row[4],
        "classes": row[5],
        "location": row[6],
        "guild_name": row[7],
        "server_name": row[8],
        "home_server_name": row[9],
        "group_id": str(row[10]),
        "is_in_party": row[11],
        "is_recruiting": row[12],
        "is_anonymous": row[13],
    }
    return Character(**character_data)


def build_news_from_row(row: tuple) -> News:
    return News(id=row[0], message=row[1])


def build_page_message_from_row(row: tuple) -> PageMessage:
    return PageMessage(
        id=row[0],
        message=row[1],
        affected_pages=row[2],
        start_date=row[3],
        end_date=row[4],
    )


def build_character_activity_summary_from_row(row: tuple) -> dict:
    return {
        "event_totals": {
            "total_level": row[0],
            "location": row[1],
            "guild_name": row[2],
            "server_name": row[3],
            "is_online": row[4],
        }
    }
