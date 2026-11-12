from datetime import datetime, timezone

import services.shard_exchange_db as shard_exchange_db
from models.shard_exchange import ShardExchangeRequestApiModel, ShardListingApiModel
from services.shard_exchange_db import ShardListingRow, ShardScanResult


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
