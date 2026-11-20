"""Small pieces several alert types share."""

from __future__ import annotations

import logging
from typing import Callable
from urllib.parse import quote

from constants.server import SERVER_NAMES

logger = logging.getLogger(__name__)

SERVER_BY_LOWER = {s.lower(): s for s in SERVER_NAMES}

# {user_id: {"friends": {character ids}, "verified": {character ids}}}
UserContext = dict[int, dict[str, set[int]]]
UserContextLoader = Callable[[list[int]], UserContext]


def canonical_servers(value: list[str]) -> list[str]:
    """Server names in their usual spelling, de-duplicated; raises ValueError for an unknown one."""
    out = []
    for name in value:
        canonical = SERVER_BY_LOWER.get(str(name).strip().lower())
        if not canonical:
            raise ValueError(f"unknown server: {name}")
        out.append(canonical)
    return list(dict.fromkeys(out))


def server_matches(server_names: list[str], server_lower: str) -> bool:
    """Empty means any server."""
    return not server_names or server_lower in {s.lower() for s in server_names}


def display_server(server_lower: str) -> str:
    return SERVER_BY_LOWER.get(server_lower, server_lower.title())


def unique_positive_ids(value: list[int]) -> list[int]:
    if any(i <= 0 for i in value):
        raise ValueError("character IDs must be positive")
    return list(dict.fromkeys(value))


def default_user_context_loader(user_ids: list[int]) -> UserContext:
    from services.alerts_db import get_user_alert_context  # lazily: needs a DB

    return get_user_alert_context(user_ids)


def load_user_context(loader: UserContextLoader, user_ids: list[int]) -> UserContext:
    """The loader's answer, or an empty context if it fails (those rules then match nothing this round)."""
    if not user_ids:
        return {}
    try:
        return loader(user_ids) or {}
    except Exception as e:
        logger.warning(f"Could not load alert user context: {e}")
        return {}


def shard_exchange_path(server_lower: str, slug: str | None) -> str:
    """The UI's Shard Exchange page for a server, opened on an item's price history when there is one."""
    base = f"/shard-exchange/{quote(server_lower)}"
    return f"{base}?history={quote(slug)}" if slug else base


def format_shards(amount) -> str:
    try:
        return f"{int(amount):,}"
    except (TypeError, ValueError):
        return "?"
