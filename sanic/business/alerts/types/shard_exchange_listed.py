"""Alert type ``shard_exchange_listed``: an item the user watches was put up on the Shard Exchange.

One rule watches one item (by slug), optionally on some servers and under price limits. Prices are for
the whole listing, in shards. Event data (emitted after a scan is stored, see
``business/shard_exchange.py``)::

    {"server_name": "thrane", "ref": "<opaque>", "slug": "some-item", "name": "Some Item",
     "quantity": 1, "min_bid": 100, "bid": 0, "buyout": 500}
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Optional

from business.alerts.base import AlertRule, AlertType, RenderedAlert
from business.alerts.types._common import (
    canonical_servers,
    display_server,
    format_shards,
    server_matches,
    shard_exchange_path,
)
from constants.server import SERVER_NAMES
from constants.shard_exchange import ITEM_SLUG_PATTERN, MAX_ITEM_SLUG_LENGTH
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(ITEM_SLUG_PATTERN)
MAX_PRICE = 2_000_000_000


class ShardExchangeListedCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_slug: str = Field(min_length=1, max_length=MAX_ITEM_SLUG_LENGTH)
    # Filled in by the server when the rule is saved, for display; whatever the client sends is replaced.
    item_name: Optional[str] = Field(default=None, max_length=200)
    server_names: list[str] = Field(default_factory=list, max_length=len(SERVER_NAMES))
    max_buyout: Optional[int] = Field(default=None, ge=1, le=MAX_PRICE)
    max_price: Optional[int] = Field(default=None, ge=1, le=MAX_PRICE)
    require_buyout: bool = False

    @field_validator("item_slug")
    @classmethod
    def _slug(cls, value: str) -> str:
        slug = value.strip().lower()
        if not _SLUG_RE.match(slug):
            raise ValueError("invalid item")
        return slug

    @field_validator("server_names")
    @classmethod
    def _known_servers(cls, value: list[str]) -> list[str]:
        return canonical_servers(value)


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def current_price(data: dict) -> int:
    return max(_int(data.get("bid")), _int(data.get("min_bid")))


def criteria_matches(criteria: ShardExchangeListedCriteria, data: dict) -> bool:
    if data.get("slug") != criteria.item_slug:
        return False
    if not server_matches(
        criteria.server_names, str(data.get("server_name") or "").lower()
    ):
        return False
    buyout = _int(data.get("buyout"))
    if (criteria.require_buyout or criteria.max_buyout is not None) and buyout <= 0:
        return False
    if criteria.max_buyout is not None and buyout > criteria.max_buyout:
        return False
    if criteria.max_price is not None and current_price(data) > criteria.max_price:
        return False
    return True


class ShardExchangeListedAlertType(AlertType):
    name = "shard_exchange_listed"
    label = "Shard Exchange listing"
    Criteria = ShardExchangeListedCriteria
    dedupe_ttl_secs = 24 * 60 * 60

    async def validate_for_user(self, criteria, user_id: int):
        import services.shard_exchange_db as shard_exchange_db

        try:
            item = await shard_exchange_db.async_get_item_by_slug(criteria.item_slug)
        except Exception as e:
            logger.error(f"Could not look up item {criteria.item_slug}: {e}")
            raise ValueError("Could not check that item. Please try again.")
        if not item:
            raise ValueError("Unknown item")
        criteria.item_name = item["name"]
        return criteria

    def build_index(self, rules: list[AlertRule]) -> dict[str, list[AlertRule]]:
        index: dict[str, list[AlertRule]] = defaultdict(list)
        for rule in rules:
            index[rule.criteria.item_slug].append(rule)
        return dict(index)

    def match(self, data: dict, index: dict[str, list[AlertRule]]) -> list[AlertRule]:
        slug = data.get("slug")
        if not slug:
            return []
        return [
            r for r in (index or {}).get(slug, []) if criteria_matches(r.criteria, data)
        ]

    def dedupe_key(self, data: dict) -> str:
        return f"sx-list:{data.get('ref')}"

    def render(self, data: dict, rule: AlertRule) -> RenderedAlert:
        server_lower = str(data.get("server_name") or "").lower()
        server = display_server(server_lower)
        name = data.get("name") or rule.criteria.item_name or "An item"
        quantity = _int(data.get("quantity"))
        details = []
        if quantity > 1:
            details.append(f"Stack of {quantity}")
        if _int(data.get("buyout")) > 0:
            details.append(f"Buyout {format_shards(data['buyout'])}")
        price = current_price(data)
        if price > 0:
            label = "Bid" if _int(data.get("bid")) > 0 else "Min bid"
            details.append(f"{label} {format_shards(price)}")
        return RenderedAlert(
            type=self.name,
            rule_id=rule.id,
            title=f"{name} listed on {server}",
            body=" · ".join(details) or "New listing",
            url=shard_exchange_path(server_lower, data.get("slug")),
            tag=f"sx-list-{data.get('ref')}",
        )
