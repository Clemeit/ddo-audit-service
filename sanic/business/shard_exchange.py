import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone

import services.shard_exchange_db as shard_exchange_db
from models.shard_exchange import ShardExchangeRequestApiModel, ShardListingApiModel
from services.shard_exchange_db import ShardListingRow, ShardScanResult


def _ref_key() -> bytes:
    """The key for listing refs: SHARD_EXCHANGE_REF_SECRET, else derived from JWT_SECRET_KEY. With
    neither set the refs are random per process (they then change when the service restarts)."""
    secret = os.getenv("SHARD_EXCHANGE_REF_SECRET") or os.getenv("JWT_SECRET_KEY")
    if not secret:
        return secrets.token_bytes(32)
    return hashlib.sha256(b"shard-exchange-listing-ref:" + secret.encode("utf-8")).digest()


_REF_KEY = _ref_key()


def listing_ref(server: str, auction_id: int, item_id: int) -> str:
    """A stable, opaque name for a listing, so clients can tell listings apart without seeing
    internal IDs (which cannot be worked back from it)."""
    message = f"{server}:{auction_id}:{item_id}".encode("utf-8")
    return hmac.new(_REF_KEY, message, hashlib.sha256).hexdigest()[:16]


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


def to_public_listing(row: dict) -> dict:
    """A listing as the public API shows it: no internal IDs, no characters, no raw item properties.
    Prices are whole listings (not per unit) in shards; bid and buyout are None when there is none."""
    bid = row["bid"] or None
    return {
        "ref": listing_ref(row["server"], row["auction_id"], row["item_id"]),
        "name": row["name"],
        "slug": row["slug"] or None,
        "category": row["category"],
        "quantity": row["quantity"],
        "min_level": row["min_level"],
        "bound": bool(row["bound"]),
        "min_bid": row["min_bid"],
        "bid": bid,
        "current_price": max(row["bid"], row["min_bid"]),
        "buyout": row["buyout"] or None,
        "posted_at": _iso(row["posted_at"]),
        "expires_at": _iso(row["expires_at"]),
    }


def category_tree_with_counts(
    categories: list[dict], counts: dict[str | None, int]
) -> list[dict]:
    """The category tree with each category's active listing count, its subcategories' included."""

    def walk(category: dict) -> dict:
        children = [walk(child) for child in category["children"]]
        own = counts.get(category["slug"], 0)
        return {
            "slug": category["slug"],
            "name": category["name"],
            "count": own + sum(child["count"] for child in children),
            "children": children,
        }

    return [walk(category) for category in categories]


def _from_unix(seconds: int) -> datetime | None:
    """Unix seconds as a UTC time; 0 means not known."""
    return datetime.fromtimestamp(seconds, tz=timezone.utc) if seconds > 0 else None


def to_listing_row(listing: ShardListingApiModel) -> ShardListingRow:
    """The upload's "none" values (0, -1) become NULLs, so a database query can tell "unknown" from a number."""
    return ShardListingRow(
        auction_id=listing.auction_id,
        item_id=listing.item_id,
        did=listing.did,
        quantity=listing.quantity if listing.quantity > 0 else None,
        min_level=listing.min_level if listing.min_level >= 0 else None,
        bound=listing.bound,
        min_bid=listing.min_bid,
        bid=listing.bid,
        buyout=listing.buyout,
        bidder_id=listing.bidder_id or None,
        poster_id=listing.poster_id or None,
        posted_at=_from_unix(listing.posted_unix),
        expires_at=_from_unix(listing.expires_unix),
        duration_s=listing.duration_s or None,
        item_props=listing.item_props,
    )


async def handle_incoming_shard_exchange(
    request_body: ShardExchangeRequestApiModel,
) -> ShardScanResult:
    """Stores one scan of one server's Shard Exchange. Raises on a database error."""
    return await shard_exchange_db.async_persist_shard_exchange_scan(
        server=request_body.server,
        scan_id=request_body.scan_id,
        started_at=_from_unix(request_body.started_unix),
        finished_at=_from_unix(request_body.finished_unix),
        pages=request_body.pages,
        complete=request_body.complete,
        listings=[to_listing_row(listing) for listing in request_body.listings],
    )
