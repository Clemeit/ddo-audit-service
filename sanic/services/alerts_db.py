"""Persisting alert rules (table ``alert_rules``; see postgres/migrations/2026-11-alerts.sql).

Every user-facing query is scoped by ``user_id``. ``get_enabled_rules_by_type`` is sync because the
dispatch worker uses the sync pool, like the other workers.
"""

import json
import logging
from datetime import datetime
from typing import Callable, Optional

from services.postgres import get_async_dict_cursor, get_dict_cursor

logger = logging.getLogger(__name__)

MAX_RULES_PER_USER = 50

_COLUMNS = "id, user_id, type, criteria, enabled, expires_at, created_at, updated_at"

# Fingerprints a rule's criteria (AlertType.criteria_key); equal keys mean duplicate rules.
CriteriaKey = Callable[[Optional[dict]], str]


class RuleLimitReached(Exception):
    pass


class DuplicateRule(Exception):
    """The user already has a rule of this type with the same criteria."""

    def __init__(self, rule: dict):
        super().__init__(f"duplicate of rule {rule['id']}")
        self.rule = rule


async def _lock_user_rules(cursor, user_id: int) -> None:
    # Serialise a user's rule writes (for the cap and the duplicate check); released on commit.
    await cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", (7301, user_id))


async def _raise_if_duplicate(
    cursor,
    user_id: int,
    rule_type: str,
    criteria: dict,
    criteria_key: CriteriaKey,
    exclude_rule_id: Optional[int] = None,
) -> None:
    await cursor.execute(
        f"""
        SELECT {_COLUMNS} FROM alert_rules
        WHERE user_id = %s AND type = %s AND id <> %s
        ORDER BY id
        """,
        (user_id, rule_type, exclude_rule_id or 0),
    )
    key = criteria_key(criteria)
    for row in await cursor.fetchall():
        if criteria_key(row["criteria"]) == key:
            raise DuplicateRule(dict(row))


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
    *,
    criteria_key: Optional[CriteriaKey] = None,
) -> dict:
    """Insert a rule. Raises RuleLimitReached when the user already has MAX_RULES_PER_USER rules,
    and DuplicateRule (when ``criteria_key`` is given) if they already have the same rule."""
    async with get_async_dict_cursor(commit=True) as cursor:
        await _lock_user_rules(cursor, user_id)
        await cursor.execute(
            "SELECT count(*) AS n FROM alert_rules WHERE user_id = %s", (user_id,)
        )
        row = await cursor.fetchone()
        if row and int(row["n"]) >= MAX_RULES_PER_USER:
            raise RuleLimitReached()
        if criteria_key:
            await _raise_if_duplicate(cursor, user_id, rule_type, criteria, criteria_key)
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
    *,
    rule_type: Optional[str] = None,
    criteria_key: Optional[CriteriaKey] = None,
) -> Optional[dict]:
    """Replace a rule's settings. Returns None if the rule does not exist for this user.

    With ``rule_type`` and ``criteria_key``, raises DuplicateRule if the new settings match
    another of the user's rules."""
    async with get_async_dict_cursor(commit=True) as cursor:
        if rule_type and criteria_key:
            await _lock_user_rules(cursor, user_id)
            await _raise_if_duplicate(
                cursor, user_id, rule_type, criteria, criteria_key, rule_id
            )
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


# ----- account-wide preferences (quiet hours; table ``alert_preferences``) -----

_PREFS_COLUMNS = "quiet_enabled, quiet_start_minute, quiet_end_minute, timezone"


async def async_get_preferences(user_id: int) -> Optional[dict]:
    """The user's preferences row, or None when they never saved any (callers apply defaults)."""
    async with get_async_dict_cursor(commit=False) as cursor:
        await cursor.execute(
            f"SELECT {_PREFS_COLUMNS} FROM alert_preferences WHERE user_id = %s",
            (user_id,),
        )
        row = await cursor.fetchone()
        return dict(row) if row else None


async def async_upsert_preferences(
    user_id: int,
    quiet_enabled: bool,
    start_minute: int,
    end_minute: int,
    timezone_name: str,
) -> dict:
    async with get_async_dict_cursor(commit=True) as cursor:
        await cursor.execute(
            f"""
            INSERT INTO alert_preferences
                (user_id, quiet_enabled, quiet_start_minute, quiet_end_minute, timezone)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (user_id) DO UPDATE SET
                quiet_enabled = EXCLUDED.quiet_enabled,
                quiet_start_minute = EXCLUDED.quiet_start_minute,
                quiet_end_minute = EXCLUDED.quiet_end_minute,
                timezone = EXCLUDED.timezone,
                updated_at = NOW()
            RETURNING {_PREFS_COLUMNS}
            """,
            (user_id, quiet_enabled, start_minute, end_minute, timezone_name),
        )
        return dict(await cursor.fetchone())


def get_all_quiet_hours() -> list[dict]:
    """Every enabled quiet-hours window (sync, for the dispatch worker)."""
    with get_dict_cursor(commit=False) as cursor:
        cursor.execute(
            f"SELECT user_id, {_PREFS_COLUMNS} FROM alert_preferences WHERE quiet_enabled"
        )
        return [dict(row) for row in cursor.fetchall()]


# ----- per-user context for alert types (friends, verified characters) -----
#
# The UI syncs a user's friends (character IDs) and verified-character access tokens into
# ``user_settings.settings`` under "friends" and "access-tokens". Alert types that mean "all my friends"
# or "all my verified characters" resolve them here. Tokens are joined to ``access_tokens`` so only
# characters the user really verified count.

_FRIEND_IDS_SQL = """
    COALESCE((
        SELECT array_agg(DISTINCT f::bigint)
        FROM jsonb_array_elements_text(
            CASE WHEN jsonb_typeof(us.settings->'friends') = 'array'
                 THEN us.settings->'friends' ELSE '[]'::jsonb END
        ) AS f
        WHERE f ~ '^[0-9]{1,18}$'
    ), '{}') AS friend_ids
"""

_VERIFIED_IDS_SQL = """
    COALESCE((
        SELECT array_agg(DISTINCT tok.character_id)
        FROM jsonb_array_elements(
            CASE WHEN jsonb_typeof(us.settings->'access-tokens') = 'array'
                 THEN us.settings->'access-tokens' ELSE '[]'::jsonb END
        ) AS t
        JOIN access_tokens tok
          ON jsonb_typeof(t) = 'object'
         AND tok.access_token = t->>'access_token'
         AND tok.character_id::text = t->>'character_id'
    ), '{}') AS verified_ids
"""


def get_user_alert_context(user_ids: list[int]) -> dict[int, dict[str, set[int]]]:
    """``{user_id: {"friends": {...}, "verified": {...}}}`` for the given users (sync, for the worker)."""
    if not user_ids:
        return {}
    with get_dict_cursor(commit=False) as cursor:
        cursor.execute(
            f"""
            SELECT us.user_id, {_FRIEND_IDS_SQL}, {_VERIFIED_IDS_SQL}
            FROM user_settings us
            WHERE us.user_id = ANY(%s)
            """,
            (list(set(user_ids)),),
        )
        return {
            int(row["user_id"]): {
                "friends": {int(i) for i in row["friend_ids"] or []},
                "verified": {int(i) for i in row["verified_ids"] or []},
            }
            for row in cursor.fetchall()
        }


async def async_get_verified_character_ids(user_id: int) -> set[int]:
    """Characters this user has verified (via access tokens synced to their settings)."""
    async with get_async_dict_cursor(commit=False) as cursor:
        await cursor.execute(
            f"SELECT {_VERIFIED_IDS_SQL} FROM user_settings us WHERE us.user_id = %s",
            (user_id,),
        )
        row = await cursor.fetchone()
        return {int(i) for i in (row["verified_ids"] if row else None) or []}
