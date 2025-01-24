import json
import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from time import time
from typing import Optional

from constants.activity import CharacterActivityType
from models.character import (
    Character,
    CharacterActivity,
    CharacterActivitySummary,
    QuestTimer,
)
from models.game import PopulationDataPoint, PopulationPointInTime
from models.redis import GameInfo
from models.service import News, PageMessage
from psycopg2 import pool  # type: ignore
from constants.activity import (
    MAX_CHARACTER_ACTIVITY_READ_LENGTH,
    MAX_CHARACTER_ACTIVITY_READ_HISTORY,
)

from models.quest import Quest
from models.area import Area

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


def add_or_update_characters(characters: list[Character]):
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                for character in characters:
                    character_dump = character.model_dump()
                    exclude_fields = [
                        "public_comment",
                        "group_id",
                        "is_in_party",
                        "is_recruiting",
                        "is_online",
                        "last_saved",
                    ]
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
                        {updates}, last_saved = NOW()
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
                raise e


def get_character_by_id(character_id: str) -> Character | None:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM public.characters WHERE id = %s", (character_id,)
            )
            character = cursor.fetchone()
            if not character:
                return None

            return build_character_from_row(character)


def get_characters_by_ids(character_ids: list[str]) -> list[Character]:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT * FROM public.characters WHERE id = ANY(%s::bigint[])",
                (character_ids,),
            )
            characters = cursor.fetchall()
            if not characters:
                return []

            return [build_character_from_row(character) for character in characters]


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
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT timestamp, data
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
    character_id: str,
) -> list[dict[str, Quest]]:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT timestamp, public.quests.name FROM public.character_activity
                LEFT JOIN public.quests ON public.quests.area_id = CAST(public.character_activity.data ->> 'id' as INTEGER)
                WHERE public.character_activity.id = %s AND activity_type = 'location' AND timestamp >= NOW() - INTERVAL '7 days'
                ORDER BY timestamp DESC
                LIMIT 500
                """,
                (character_id,),
            )
            activity = cursor.fetchall()
            if not activity:
                return []

            return activity


def get_game_population(
    start_date: str = None, end_date: str = None
) -> list[dict]:  # TODO: add type
    if not start_date:
        start_date = "NOW() - INTERVAL 1 day"
    if not end_date:
        end_date = "NOW()"

    # get all entries from the game_info table
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    EXTRACT(EPOCH FROM timestamp) as timestamp,
                    data
                FROM public.game_info
                WHERE timestamp BETWEEN %s AND %s
                """,
                (
                    start_date,
                    end_date,
                ),
            )
            game_info_list = cursor.fetchall()
            if not game_info_list:
                return []

            population_points: list[dict] = []
            for game_info in game_info_list:
                try:
                    timestamp = game_info[0]
                    data = GameInfo(**game_info[1])
                    population_data_points: list[dict[str, PopulationDataPoint]] = []
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
                raise e


def add_character_activity(game_activity: dict[str, list[CharacterActivity]]):
    insert_query = """
        INSERT INTO character_activity (timestamp, id, activity_type, data)
        VALUES (NOW(), %s, %s, %s)
    """
    batch_size = 500
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            try:
                batch = []
                for server_activity in game_activity.values():
                    for activity in server_activity:
                        batch.append(
                            (
                                activity.id,
                                activity.activity_type.name,
                                json.dumps(activity.data),
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


def get_page_messages(page_name: Optional[str] = None) -> list[PageMessage]:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            if page_name:
                cursor.execute(
                    """
                    SELECT * FROM public.page_messages
                    WHERE page_name = ANY(%s)
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
                if message.start_date.timestamp()
                < time()
                < message.end_date.timestamp()
            ]
            return filtered_messages


def build_character_from_row(row: tuple) -> Character:
    return Character(
        id=str(row[0]),
        name=row[1],
        gender=row[2],
        race=row[3],
        total_level=row[4],
        classes=row[5],
        location=row[6],
        guild_name=row[7],
        server_name=row[8],
        home_server_name=row[9],
        is_anonymous=row[10],
        last_updated=row[11].isoformat() if isinstance(row[11], datetime) else "",
        last_saved=row[12].isoformat() if isinstance(row[12], datetime) else "",
    )


def build_news_from_row(row: tuple) -> News:
    return News(id=row[0], message=row[1])


def build_page_message_from_row(row: tuple) -> PageMessage:
    return PageMessage(
        id=row[0],
        message=row[1],
        affected_pages=row[2],
        start_date=row[3].isoformat() if isinstance(row[3], datetime) else "",
        end_date=row[4].isoformat() if isinstance(row[4], datetime) else "",
    )


def build_character_activity_summary_from_row(row: tuple) -> CharacterActivitySummary:
    return CharacterActivitySummary(
        level_event_count=row[0],
        location_event_count=row[1],
        guild_name_event_count=row[2],
        server_name_event_count=row[3],
        status_event_count=row[4],
    )


def build_character_activity_from_rows(rows: list[tuple]) -> list[dict]:
    return [
        {
            "timestamp": row[0].isoformat() if isinstance(row[0], datetime) else "",
            "data": row[1],
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
                conn.rollback()
                raise e


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
