"""Turning ingested data into alert events (pure functions, so they are easy to test).

Each helper diffs what was cached before an ingest against what arrived and returns the event data to
queue with ``events.emit_alert_events``. They guard against a cold cache: after Redis is flushed or a
collector restarts, everything looks new, and that must not turn into a flood of alerts.
"""

from __future__ import annotations

from typing import Iterable, Optional

SERVER_STATUS_ALERT_TYPE = "server_status"
FRIEND_LOGIN_ALERT_TYPE = "friend_login"
SHARD_EXCHANGE_LISTED_ALERT_TYPE = "shard_exchange_listed"
SHARD_EXCHANGE_OUTBID_ALERT_TYPE = "shard_exchange_outbid"

# More logins than this on one server in one batch means the cache was cold, not a real burst.
MAX_LOGINS_PER_BATCH_FOR_ALERTS = 300


def server_status_events(
    previous: Optional[dict[str, dict]], incoming: dict[str, dict]
) -> list[dict]:
    """Servers in ``incoming`` whose ``is_online`` flipped. ``previous`` is the cached ``servers`` dict.

    A server with no previous status (cold cache, new server) never produces an event, and neither does
    an update that does not set ``is_online``.
    """
    events = []
    previous = previous or {}
    for server_name, info in (incoming or {}).items():
        if not isinstance(info, dict) or "is_online" not in info:
            continue
        new_state = info.get("is_online")
        old_state = (previous.get(server_name) or {}).get("is_online")
        if not isinstance(new_state, bool) or not isinstance(old_state, bool):
            continue
        if new_state != old_state:
            events.append(
                {"server_name": str(server_name).lower(), "is_online": new_state}
            )
    return events


def _classes(character: dict) -> list[dict]:
    out = []
    for c in character.get("classes") or []:
        if isinstance(c, dict) and c.get("name"):
            out.append({"name": c["name"], "level": c.get("level")})
    return out


def friend_login_events(
    server_name: str,
    previous_character_ids: set[int],
    incoming_characters: dict[int, dict],
) -> list[dict]:
    """Characters that just appeared online on one server (anonymous characters are skipped)."""
    if not previous_character_ids:
        return []  # cold cache: everyone online would look like a fresh login
    logged_on = [
        cid for cid in incoming_characters if cid not in previous_character_ids
    ]
    if not logged_on or len(logged_on) > MAX_LOGINS_PER_BATCH_FOR_ALERTS:
        return []
    events = []
    for cid in logged_on:
        character = incoming_characters.get(cid) or {}
        if character.get("is_anonymous"):
            continue
        events.append(
            {
                "character_id": int(cid),
                "name": character.get("name"),
                "server_name": server_name.lower(),
                "total_level": character.get("total_level"),
                "classes": _classes(character),
            }
        )
    return events


def shard_exchange_listing_events(
    server_name: str, new_listings: Iterable[dict]
) -> list[dict]:
    """Event data for newly seen listings. Rows carry ``ref``, ``slug``, ``name`` and prices."""
    events = []
    for row in new_listings:
        if not row.get("slug") or not row.get("ref"):
            continue
        events.append(
            {
                "server_name": server_name.lower(),
                "ref": row["ref"],
                "slug": row["slug"],
                "name": row.get("name"),
                "quantity": row.get("quantity"),
                "min_bid": row.get("min_bid"),
                "bid": row.get("bid"),
                "buyout": row.get("buyout"),
            }
        )
    return events


def shard_exchange_outbid_events(
    server_name: str, bid_changes: Iterable[dict]
) -> list[dict]:
    """Event data for listings whose high bidder changed away from a known previous bidder."""
    events = []
    for row in bid_changes:
        prev_bidder = row.get("prev_bidder_id")
        if not prev_bidder or prev_bidder == row.get("new_bidder_id"):
            continue
        if not row.get("ref"):
            continue
        events.append(
            {
                "server_name": server_name.lower(),
                "ref": row["ref"],
                "slug": row.get("slug"),
                "name": row.get("name"),
                "prev_bidder_id": int(prev_bidder),
                "bid": row.get("bid"),
                "buyout": row.get("buyout"),
            }
        )
    return events
