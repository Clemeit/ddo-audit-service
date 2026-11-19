"""Persisting Web Push subscriptions (table ``push_subscriptions``; see
postgres/migrations/2026-11-push-subscriptions.sql).

The async functions serve the API; the sync ones serve the dispatch worker (sync pool, like the other
workers).
"""

import logging
from typing import Optional

from services.postgres import get_async_dict_cursor, get_dict_cursor

logger = logging.getLogger(__name__)

# A user rarely has more than a few browsers; keep the newest ones if they somehow pile up.
MAX_SUBSCRIPTIONS_PER_USER = 20


async def async_upsert_subscription(
    user_id: int, endpoint: str, p256dh: str, auth: str, user_agent: Optional[str]
) -> dict:
    """Save a subscription. An endpoint already saved for another user moves to this one (the browser
    is now logged in as this user)."""
    async with get_async_dict_cursor(commit=True) as cursor:
        await cursor.execute(
            """
            INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, user_agent)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (endpoint) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                p256dh = EXCLUDED.p256dh,
                auth = EXCLUDED.auth,
                user_agent = EXCLUDED.user_agent,
                failure_count = 0,
                updated_at = NOW()
            RETURNING id, created_at, updated_at
            """,
            (user_id, endpoint, p256dh, auth, user_agent),
        )
        row = dict(await cursor.fetchone())
        await cursor.execute(
            """
            DELETE FROM push_subscriptions
            WHERE user_id = %s AND id NOT IN (
                SELECT id FROM push_subscriptions WHERE user_id = %s
                ORDER BY updated_at DESC LIMIT %s
            )
            """,
            (user_id, user_id, MAX_SUBSCRIPTIONS_PER_USER),
        )
        return row


async def async_delete_subscription(user_id: int, endpoint: str) -> bool:
    async with get_async_dict_cursor(commit=True) as cursor:
        await cursor.execute(
            "DELETE FROM push_subscriptions WHERE user_id = %s AND endpoint = %s",
            (user_id, endpoint),
        )
        return cursor.rowcount > 0


def get_subscriptions_for_user(user_id: int) -> list[dict]:
    with get_dict_cursor(commit=False) as cursor:
        cursor.execute(
            "SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE user_id = %s",
            (user_id,),
        )
        return [dict(row) for row in cursor.fetchall()]


def delete_subscription(subscription_id: int) -> None:
    with get_dict_cursor(commit=True) as cursor:
        cursor.execute(
            "DELETE FROM push_subscriptions WHERE id = %s", (subscription_id,)
        )


def mark_subscription_success(subscription_id: int) -> None:
    with get_dict_cursor(commit=True) as cursor:
        cursor.execute(
            """
            UPDATE push_subscriptions
            SET failure_count = 0, last_success_at = NOW()
            WHERE id = %s
            """,
            (subscription_id,),
        )


def record_subscription_failure(subscription_id: int) -> int:
    """Count a failed send; returns the new consecutive-failure count (0 if the row is gone)."""
    with get_dict_cursor(commit=True) as cursor:
        cursor.execute(
            """
            UPDATE push_subscriptions SET failure_count = failure_count + 1
            WHERE id = %s
            RETURNING failure_count
            """,
            (subscription_id,),
        )
        row = cursor.fetchone()
        return int(row["failure_count"]) if row else 0
