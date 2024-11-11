import os

from psycopg2 import pool # type: ignore
import json
from contextlib import contextmanager


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


def add_or_update_character(character: dict) -> None:
    character_fields = [f for f in character.keys()]

    # Construct the query dynamically
    columns = ", ".join(character_fields)
    placeholders = ", ".join(["%s"] * len(character_fields))
    updates = ", ".join([f"{field} = EXCLUDED.{field}" for field in character_fields])

    query = f"""
        INSERT INTO characters ({columns})
        VALUES ({placeholders})
        ON CONFLICT (id) DO UPDATE SET
        {updates}
    """

    # Get the values of the Character model
    values = [json.dumps(value) if isinstance(value, (dict, list)) else value for value in character.values()]

    with get_db_connection() as conn:
        try:
            with conn.cursor() as cursor:
                cursor.execute(query, values)
                conn.commit()
        except Exception as e:
            print(f"Failed to add or update character in the database: {e}")
            conn.rollback()


def get_nested_value(data: dict, field: str):
    fields = field.split(".")
    value = data
    for f in fields:
        value = value.get(f)
        if value is None:
            return None
    return value


def add_or_update_character_activity(
    character_id: str, activity: dict[str, dict]
) -> None:
    with get_db_connection() as conn: 
        try:
            timestamp = activity.get("timestamp")
            data = activity.get("data")

            with conn.cursor() as cursor:

                cursor.execute(
                    "SELECT 1 FROM public.character_activity WHERE id = %s", (character_id,)
                )
                exists = cursor.fetchone()

                relevant_fields = [
                    "total_level",
                    "location.id",
                    "guild_name",
                    "server_name",
                    "is_online",
                ]

                values = []
                for field in relevant_fields:
                    value = get_nested_value(data, field)
                    values.append(
                        json.dumps(
                            [{"timestamp": timestamp, "data": value}]
                            if value is not None
                            else []
                        )
                    )

                relevant_fields = [f.split(".")[0] for f in relevant_fields]

                if exists:
                    # Update the existing record
                    query_fields = ", ".join(
                        [f"{field} = {field} || %s::jsonb" for field in relevant_fields]
                    )
                    query = f"""
                        UPDATE public.character_activity
                        SET
                            {query_fields}
                        WHERE id = %s
                    """

                    # print("query", query)
                    # print("values", values)

                    cursor.execute(
                        query,
                        values + [character_id],
                    )
                else:
                    # Insert a new record
                    columns = ", ".join([*relevant_fields])
                    placeholders = ", ".join(["%s::jsonb"] * len(relevant_fields))

                    query = f"""
                        INSERT INTO public.character_activity (id, {columns})
                        VALUES (%s, {placeholders})"""

                    # print("query", query)
                    # print("values", values)

                    cursor.execute(
                        query,
                        [character_id] + values,
                    )

                conn.commit()
        except Exception as e:
            print(f"Failed to add or update character activity in the database: {e}")
            conn.rollback()


def get_character_by_id(character_id: str) -> dict:
    with get_db_connection() as conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM public.characters WHERE id = %s", (character_id,))
            character = cursor.fetchone()
            if character:
                return build_character_from_row(character)
        except Exception as e:
            print(f"Failed to get character by ID from the database: {e}")
    return {}


def get_character_by_name_and_server(character_name: str, server_name: str) -> dict:
    with get_db_connection() as conn:
        try:
            with conn.cursor() as cursor: 
                cursor.execute(
                    "SELECT * FROM public.characters WHERE LOWER(name) = %s AND LOWER(server_name) = %s",
                    (character_name.lower(), server_name.lower()),
                )
                character = cursor.fetchone()
                if character:
                    return build_character_from_row(character)
        except Exception as e:
            print(f"Failed to get character by name and server from the database: {e}")
    return {}


def build_character_from_row(row: tuple) -> dict:
    return {
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


def get_character_activity_summary_by_character_id(character_id: str) -> dict:
    with get_db_connection() as conn:
        try:
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
        try:
            with conn.cursor() as cursor:
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