"""Persisting alert rules (table ``alert_rules``; see postgres/migrations/2026-11-alerts.sql).

Every user-facing query is scoped by ``user_id``. ``get_enabled_rules_by_type`` is sync because the
dispatch worker uses the sync pool, like the other workers.
"""

import json
import logging
from datetime import datetime
from typing import Optional

from services.postgres import get_async_dict_cursor, get_dict_cursor

logger = logging.getLogger(__name__)

MAX_RULES_PER_USER = 50

_COLUMNS = "id, user_id, type, criteria, enabled, expires_at, created_at, updated_at"


class RuleLimitReached(Exception):
    pass


async def async_list_rules(user_id: int) -> list[dict]:
    async with get_async_dict_cursor(commit=False) as cursor:
        await cursor.execute(
            f"SELECT {_COLUMNS} FROM alert_rules WHERE user_id = %s ORDER BY id",
            (user_id,),
        )
        return [dict(row) for row in await cursor.fetchall()]


async def async_create_rule(
    user_id: int,
    rule_type: str,
    criteria: dict,
    enabled: bool,
    expires_at: Optional[datetime],
) -> dict:
    """Insert a rule. Raises RuleLimitReached when the user already has MAX_RULES_PER_USER rules."""
    async with get_async_dict_cursor(commit=True) as cursor:
        # Serialise concurrent creates for one user so the cap holds.
        await cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", (7301, user_id))
        await cursor.execute(
            "SELECT count(*) AS n FROM alert_rules WHERE user_id = %s", (user_id,)
        )
        row = await cursor.fetchone()
        if row and int(row["n"]) >= MAX_RULES_PER_USER:
            raise RuleLimitReached()
        await cursor.execute(
            f"""
            INSERT INTO alert_rules (user_id, type, criteria, enabled, expires_at)
            VALUES (%s, %s, %s::jsonb, %s, %s)
            RETURNING {_COLUMNS}
            """,
            (user_id, rule_type, json.dumps(criteria), enabled, expires_at),
        )
        return dict(await cursor.fetchone())


async def async_update_rule(
    user_id: int,
    rule_id: int,
    criteria: dict,
    enabled: bool,
    expires_at: Optional[datetime],
) -> Optional[dict]:
    """Replace a rule's settings. Returns None if the rule does not exist for this user."""
    async with get_async_dict_cursor(commit=True) as cursor:
        await cursor.execute(
            f"""
            UPDATE alert_rules
            SET criteria = %s::jsonb, enabled = %s, expires_at = %s, updated_at = NOW()
            WHERE id = %s AND user_id = %s
            RETURNING {_COLUMNS}
            """,
            (json.dumps(criteria), enabled, expires_at, rule_id, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def async_get_rule(user_id: int, rule_id: int) -> Optional[dict]:
    async with get_async_dict_cursor(commit=False) as cursor:
        await cursor.execute(
            f"SELECT {_COLUMNS} FROM alert_rules WHERE id = %s AND user_id = %s",
            (rule_id, user_id),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def async_delete_rule(user_id: int, rule_id: int) -> bool:
    async with get_async_dict_cursor(commit=True) as cursor:
        await cursor.execute(
            "DELETE FROM alert_rules WHERE id = %s AND user_id = %s",
            (rule_id, user_id),
        )
        return cursor.rowcount > 0


def get_enabled_rules_by_type(rule_type: str) -> list[dict]:
    """All enabled, unexpired rules of one type (for the dispatch worker)."""
    with get_dict_cursor(commit=False) as cursor:
        cursor.execute(
            """
            SELECT id, user_id, type, criteria, expires_at
            FROM alert_rules
            WHERE type = %s AND enabled AND (expires_at IS NULL OR expires_at > NOW())
            """,
            (rule_type,),
        )
        return [dict(row) for row in cursor.fetchall()]
