"""Alert type ``shard_exchange_outbid``: someone outbid one of the user's verified characters.

A rule covers chosen verified characters, or all of them (empty ``character_ids``). Ownership comes from
the access tokens the site syncs to the account, checked when the rule is saved and again whenever the
worker rebuilds its index. Event data (emitted after a scan is stored, see ``business/shard_exchange.py``)::

    {"server_name": "thrane", "ref": "<opaque>", "slug": "some-item", "name": "Some Item",
     "prev_bidder_id": 123, "bid": 600, "buyout": 1000}
"""

from __future__ import annotations

import logging
from collections import defaultdict

from business.alerts.base import AlertRule, AlertType, RenderedAlert
from business.alerts.types._common import (
    UserContextLoader,
    default_user_context_loader,
    display_server,
    format_shards,
    load_user_context,
    shard_exchange_path,
    unique_positive_ids,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

MAX_CHARACTERS_PER_RULE = 50


class ShardExchangeOutbidCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Empty means all the user's verified characters.
    character_ids: list[int] = Field(
        default_factory=list, max_length=MAX_CHARACTERS_PER_RULE
    )

    @field_validator("character_ids")
    @classmethod
    def _unique(cls, value: list[int]) -> list[int]:
        return unique_positive_ids(value)


class ShardExchangeOutbidAlertType(AlertType):
    name = "shard_exchange_outbid"
    label = "Outbid on the Shard Exchange"
    Criteria = ShardExchangeOutbidCriteria
    dedupe_ttl_secs = 24 * 60 * 60

    def __init__(self, context_loader: UserContextLoader | None = None) -> None:
        self._context_loader = context_loader or default_user_context_loader

    async def validate_for_user(self, criteria, user_id: int):
        import services.alerts_db as alerts_db

        try:
            verified = await alerts_db.async_get_verified_character_ids(user_id)
        except Exception as e:
            logger.error(f"Could not load verified characters for user {user_id}: {e}")
            raise ValueError(
                "Could not check your verified characters. Please try again."
            )
        if not verified:
            raise ValueError(
                "Verify a character first. If you just did, try again in a moment."
            )
        if any(cid not in verified for cid in criteria.character_ids):
            raise ValueError("You can only choose characters you have verified.")
        return criteria

    def build_index(self, rules: list[AlertRule]) -> dict[int, list[AlertRule]]:
        # Ownership is re-checked here too, so a rule stops matching once its token is gone.
        context = load_user_context(self._context_loader, [r.user_id for r in rules])
        index: dict[int, list[AlertRule]] = defaultdict(list)
        for rule in rules:
            verified = context.get(rule.user_id, {}).get("verified", set())
            if rule.criteria.character_ids:
                ids = [c for c in rule.criteria.character_ids if c in verified]
            else:
                ids = list(verified)
            for cid in ids:
                index[int(cid)].append(rule)
        return dict(index)

    def match(self, data: dict, index: dict[int, list[AlertRule]]) -> list[AlertRule]:
        bidder = data.get("prev_bidder_id")
        if not isinstance(bidder, int):
            return []
        return list((index or {}).get(bidder, []))

    def dedupe_key(self, data: dict) -> str:
        return f"sx-outbid:{data.get('ref')}:{data.get('bid')}"

    def render(self, data: dict, rule: AlertRule) -> RenderedAlert:
        server_lower = str(data.get("server_name") or "").lower()
        server = display_server(server_lower)
        name = data.get("name") or "an item"
        return RenderedAlert(
            type=self.name,
            rule_id=rule.id,
            title=f"Outbid on {name}",
            body=f"The bid is now {format_shards(data.get('bid'))} shards on {server}.",
            url=shard_exchange_path(server_lower, data.get("slug")),
            tag=f"sx-outbid-{data.get('ref')}",
        )
