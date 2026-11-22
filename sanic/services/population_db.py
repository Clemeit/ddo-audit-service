"""Hourly population rollups (table ``population_hourly``; see
postgres/migrations/2026-11-population-rollups.sql).

``game_info`` holds one JSONB snapshot every 5 minutes. Long-range reports read this table
instead: one row per server per UTC hour with the sample count and avg/min/max of player and LFM
counts. Coarser buckets (day/week/month) are derived here at query time, weighting each hour by
its sample count so hours with missed snapshots don't skew the average.

All bucket boundaries are UTC. Callers pass timezone-aware datetimes.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from models.game import PopulationDataPoint, PopulationPointInTime
from services.postgres import get_db_cursor
from utils.time import datetime_to_datetime_string

logger = logging.getLogger(__name__)

def iso_utc(value: datetime) -> str:
    """Format a naive-UTC or timezone-aware datetime as ``YYYY-MM-DDTHH:MM:SSZ``."""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc)
    return datetime_to_datetime_string(value)


# Units accepted by date_trunc for derived buckets. Never interpolate anything else into SQL.
BUCKET_UNITS = ("hour", "day", "week", "month")


def rollup_population_hours(start: datetime, end: datetime) -> int:
    """(Re)compute population_hourly rows for game_info snapshots in [start, end).

    Idempotent: existing rows for the same (server, hour) are overwritten, so re-running over a
    partially elapsed hour simply refreshes it. Returns the number of rows written.
    """
    with get_db_cursor(commit=True) as cursor:
        cursor.execute(
            """
            INSERT INTO public."population_hourly" AS ph
                (bucket, server_name, samples,
                 players_avg, players_min, players_max,
                 lfms_avg, lfms_min, lfms_max)
            SELECT
                date_trunc('hour', gi."timestamp" AT TIME ZONE 'UTC') AT TIME ZONE 'UTC',
                s.key,
                COUNT(*),
                AVG(v.players), MIN(v.players), MAX(v.players),
                AVG(v.lfms), MIN(v.lfms), MAX(v.lfms)
            FROM public."game_info" gi
            CROSS JOIN LATERAL jsonb_each(gi.data->'servers') AS s(key, value)
            CROSS JOIN LATERAL (
                SELECT
                    COALESCE((s.value->>'character_count')::int, 0) AS players,
                    COALESCE((s.value->>'lfm_count')::int, 0) AS lfms
            ) AS v
            WHERE gi."timestamp" >= %s AND gi."timestamp" < %s
              AND jsonb_typeof(gi.data->'servers') = 'object'
            GROUP BY 1, 2
            ON CONFLICT (server_name, bucket) DO UPDATE SET
                samples = EXCLUDED.samples,
                players_avg = EXCLUDED.players_avg,
                players_min = EXCLUDED.players_min,
                players_max = EXCLUDED.players_max,
                lfms_avg = EXCLUDED.lfms_avg,
                lfms_min = EXCLUDED.lfms_min,
                lfms_max = EXCLUDED.lfms_max
            """,
            (start, end),
        )
        return cursor.rowcount


def get_first_game_info_timestamp() -> Optional[datetime]:
    with get_db_cursor(commit=False) as cursor:
        cursor.execute('SELECT MIN("timestamp") FROM public."game_info"')
        row = cursor.fetchone()
        return row[0] if row else None


def get_latest_rollup_bucket() -> Optional[datetime]:
    with get_db_cursor(commit=False) as cursor:
        cursor.execute('SELECT MAX(bucket) FROM public."population_hourly"')
        row = cursor.fetchone()
        return row[0] if row else None


def get_population_buckets(start: datetime, end: datetime, unit: str) -> list[dict]:
    """Per-server population aggregated into ``unit`` buckets over [start, end).

    Returns rows ordered by bucket: {t, server_name, samples, players_avg/min/max, lfms_avg/min/max}
    where ``t`` is the bucket start as a naive UTC datetime.
    """
    if unit not in BUCKET_UNITS:
        raise ValueError(f"Invalid bucket unit '{unit}'")
    with get_db_cursor(commit=False) as cursor:
        cursor.execute(
            """
            SELECT
                date_trunc(%s, bucket AT TIME ZONE 'UTC') AS t,
                server_name,
                SUM(samples) AS samples,
                SUM(players_avg * samples) / SUM(samples) AS players_avg,
                MIN(players_min) AS players_min,
                MAX(players_max) AS players_max,
                SUM(lfms_avg * samples) / SUM(samples) AS lfms_avg,
                MIN(lfms_min) AS lfms_min,
                MAX(lfms_max) AS lfms_max
            FROM public."population_hourly"
            WHERE bucket >= %s AND bucket < %s
            GROUP BY 1, 2
            ORDER BY 1, 2
            """,
            (unit, start, end),
        )
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def get_total_population_buckets(start: datetime, end: datetime, unit: str) -> list[dict]:
    """All servers combined, aggregated into ``unit`` buckets over [start, end).

    Each hour's combined value is the sum of the servers' hourly averages. Per bucket this
    returns the mean of those hourly totals plus the lowest and highest hour, so min/max describe
    the combined population (summing per-server maxima would overstate the peak).
    Rows: {t, hours, players_avg/min/max, lfms_avg/min/max}, ``t`` naive UTC.
    """
    if unit not in BUCKET_UNITS:
        raise ValueError(f"Invalid bucket unit '{unit}'")
    with get_db_cursor(commit=False) as cursor:
        cursor.execute(
            """
            WITH hourly AS (
                SELECT bucket, SUM(players_avg) AS players, SUM(lfms_avg) AS lfms
                FROM public."population_hourly"
                WHERE bucket >= %s AND bucket < %s
                GROUP BY bucket
            )
            SELECT
                date_trunc(%s, bucket AT TIME ZONE 'UTC') AS t,
                COUNT(*) AS hours,
                AVG(players) AS players_avg,
                MIN(players) AS players_min,
                MAX(players) AS players_max,
                AVG(lfms) AS lfms_avg,
                MIN(lfms) AS lfms_min,
                MAX(lfms) AS lfms_max
            FROM hourly
            GROUP BY 1
            ORDER BY 1
            """,
            (start, end, unit),
        )
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def get_daily_population_points(
    start: datetime, end: datetime
) -> list[PopulationPointInTime]:
    """Daily average population in the legacy /timeseries response shape."""
    points: dict[str, dict[str, PopulationDataPoint]] = {}
    for row in get_population_buckets(start, end, "day"):
        timestamp = iso_utc(row["t"])
        points.setdefault(timestamp, {})[row["server_name"]] = PopulationDataPoint(
            character_count=float(row["players_avg"]),
            lfm_count=float(row["lfms_avg"]),
        )
    return [
        PopulationPointInTime(timestamp=timestamp, data=data)
        for timestamp, data in points.items()
    ]


def get_population_totals(start: datetime, end: datetime) -> dict[str, dict]:
    """Per-server sum of every 5-minute snapshot's counts over [start, end).

    Equivalent to summing the raw game_info rows (the legacy /totals semantics), reconstructed
    from each hour's average times its sample count.
    """
    with get_db_cursor(commit=False) as cursor:
        cursor.execute(
            """
            SELECT
                server_name,
                SUM(players_avg * samples) AS character_count,
                SUM(lfms_avg * samples) AS lfm_count
            FROM public."population_hourly"
            WHERE bucket >= %s AND bucket < %s
            GROUP BY server_name
            ORDER BY server_name
            """,
            (start, end),
        )
        return {
            server_name: {
                "character_count": round(float(character_count)),
                "lfm_count": round(float(lfm_count)),
            }
            for server_name, character_count, lfm_count in cursor.fetchall()
        }


def get_population_window(start: datetime, end: datetime) -> tuple[dict[str, dict], int]:
    """Sample-weighted average players/LFMs per server over [start, end), plus the number of
    distinct hours that have any data (for coverage checks)."""
    with get_db_cursor(commit=False) as cursor:
        cursor.execute(
            """
            SELECT
                server_name,
                SUM(players_avg * samples) / SUM(samples) AS players,
                SUM(lfms_avg * samples) / SUM(samples) AS lfms
            FROM public."population_hourly"
            WHERE bucket >= %s AND bucket < %s
            GROUP BY server_name
            ORDER BY server_name
            """,
            (start, end),
        )
        servers = {
            server_name: {"players": float(players), "lfms": float(lfms)}
            for server_name, players, lfms in cursor.fetchall()
        }
        cursor.execute(
            """
            SELECT COUNT(DISTINCT bucket)
            FROM public."population_hourly"
            WHERE bucket >= %s AND bucket < %s
            """,
            (start, end),
        )
        hours = cursor.fetchone()[0] or 0
        return servers, int(hours)


def get_peak_total_hour(start: datetime, end: datetime) -> Optional[dict]:
    """The hour with the highest combined average players across all servers."""
    with get_db_cursor(commit=False) as cursor:
        cursor.execute(
            """
            SELECT bucket, SUM(players_avg) AS players
            FROM public."population_hourly"
            WHERE bucket >= %s AND bucket < %s
            GROUP BY bucket
            ORDER BY players DESC, bucket DESC
            LIMIT 1
            """,
            (start, end),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {"timestamp": iso_utc(row[0]), "players": float(row[1])}


def get_peak_server_snapshot(start: datetime, end: datetime) -> Optional[dict]:
    """The highest single-server player count seen in any snapshot."""
    with get_db_cursor(commit=False) as cursor:
        cursor.execute(
            """
            SELECT server_name, players_max, bucket
            FROM public."population_hourly"
            WHERE bucket >= %s AND bucket < %s
            ORDER BY players_max DESC, bucket DESC
            LIMIT 1
            """,
            (start, end),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            "server_name": row[0],
            "players": int(row[1]),
            "hour": iso_utc(row[2]),
        }


def get_average_total_by_weekday_and_hour(
    start: datetime, end: datetime
) -> tuple[list[dict], list[dict]]:
    """Average combined players by UTC weekday (0 = Sunday) and by UTC hour of day."""
    totals_cte = """
        WITH totals AS (
            SELECT bucket AT TIME ZONE 'UTC' AS t, SUM(players_avg) AS players
            FROM public."population_hourly"
            WHERE bucket >= %s AND bucket < %s
            GROUP BY bucket
        )
    """
    with get_db_cursor(commit=False) as cursor:
        cursor.execute(
            totals_cte
            + "SELECT EXTRACT(DOW FROM t)::int, AVG(players) FROM totals GROUP BY 1 ORDER BY 1",
            (start, end),
        )
        by_weekday = [
            {"day": int(day), "players": float(players)}
            for day, players in cursor.fetchall()
        ]
        cursor.execute(
            totals_cte
            + "SELECT EXTRACT(HOUR FROM t)::int, AVG(players) FROM totals GROUP BY 1 ORDER BY 1",
            (start, end),
        )
        by_hour = [
            {"hour": int(hour), "players": float(players)}
            for hour, players in cursor.fetchall()
        ]
        return by_weekday, by_hour
