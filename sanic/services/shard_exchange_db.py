"""Persisting Shard Exchange (auction house) scans.

One scan is one transaction: the whole scan is loaded into a temporary table with COPY and the rest is
set-based SQL, so a few thousand listings cost a handful of statements. If anything fails nothing is kept
and the uploader (which treats a scan as delivered only after a 200) sends it again.

Tables: see postgres/init.sql (shard_exchange_*) and postgres/migrations/2026-11-shard-exchange.sql.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import orjson
from constants.shard_exchange import (
    END_REASON_EXPIRED,
    END_REASON_SOLD_OR_CANCELLED,
    END_REASON_UNKNOWN,
    EXPIRY_SLACK_SECONDS,
    MIN_ACTIVE_FRACTION_TO_END_LISTINGS,
    RETENTION_DAYS,
)
from services.postgres import get_async_dict_cursor

logger = logging.getLogger(__name__)


@dataclass
class ShardListingRow:
    """One listing, already in the shape the table wants (None for "the game did not say")."""

    auction_id: int
    item_id: int
    did: int
    quantity: int | None
    min_level: int | None
    bound: bool
    min_bid: int
    bid: int
    buyout: int
    bidder_id: int | None
    poster_id: int | None
    posted_at: datetime | None
    expires_at: datetime | None
    duration_s: int | None
    item_props: dict[str, int]


@dataclass
class ShardScanResult:
    duplicate: bool = False  # this scan_id had been stored already; nothing was changed
    listings: int = 0
    new_listings: int = 0
    bid_changes: int = 0
    price_rows: int = 0
    ended: int = 0
    ended_skipped_reason: str | None = None  # why a complete scan did not end anything
    previously_active: int = 0


_CREATE_TEMP = """
    CREATE TEMP TABLE _shard_scan_listings (
        auction_id bigint NOT NULL,
        item_id bigint NOT NULL,
        did bigint NOT NULL,
        quantity bigint,
        min_level integer,
        bound boolean NOT NULL,
        min_bid bigint NOT NULL,
        bid bigint NOT NULL,
        buyout bigint NOT NULL,
        bidder_id bigint,
        poster_id bigint,
        posted_at timestamptz,
        expires_at timestamptz,
        duration_s integer,
        item_props jsonb NOT NULL,
        PRIMARY KEY (auction_id, item_id)
    ) ON COMMIT DROP
"""

_COPY = """
    COPY _shard_scan_listings (auction_id, item_id, did, quantity, min_level, bound, min_bid, bid, buyout,
                               bidder_id, poster_id, posted_at, expires_at, duration_s, item_props)
    FROM STDIN
"""

# A listing whose bid differs from the stored one (or a new listing that already has a bid).
_INSERT_BIDS = """
    INSERT INTO shard_exchange_bids ("time", server, auction_id, item_id, did, bid, bidder_id)
    SELECT %(finished)s, %(server)s, t.auction_id, t.item_id, t.did, t.bid, t.bidder_id
    FROM _shard_scan_listings t
    LEFT JOIN shard_exchange_listings l
        ON l.server = %(server)s AND l.auction_id = t.auction_id AND l.item_id = t.item_id
    WHERE t.bid > 0 AND (l.bid IS NULL OR l.bid <> t.bid)
"""

# Everything but the bid, the bidder and the sighting is fixed when a listing is first seen.
# A scan that finished before the stored sighting (uploads arriving out of order) changes nothing.
_UPSERT_LISTINGS = """
    INSERT INTO shard_exchange_listings (
        server, auction_id, item_id, did, quantity, min_level, bound, min_bid, bid, buyout, bidder_id,
        poster_id, posted_at, expires_at, duration_s, item_props,
        first_seen_at, last_seen_at, first_scan_id, last_scan_id)
    SELECT %(server)s, t.auction_id, t.item_id, t.did, t.quantity, t.min_level, t.bound, t.min_bid, t.bid,
           t.buyout, t.bidder_id, t.poster_id, t.posted_at, t.expires_at, t.duration_s, t.item_props,
           %(finished)s, %(finished)s, %(scan)s, %(scan)s
    FROM _shard_scan_listings t
    ON CONFLICT (server, auction_id, item_id) DO UPDATE SET
        bid = EXCLUDED.bid,
        bidder_id = EXCLUDED.bidder_id,
        last_seen_at = EXCLUDED.last_seen_at,
        last_scan_id = EXCLUDED.last_scan_id,
        ended_at = NULL,
        end_reason = NULL
    WHERE shard_exchange_listings.last_seen_at <= EXCLUDED.last_seen_at
"""

# Prices are per unit (price / stack size; an unknown stack size counts as 1).
_INSERT_PRICE_HISTORY = """
    INSERT INTO shard_exchange_price_history (
        "time", server, did, listing_count, unit_count, buyout_listing_count,
        min_buyout_unit, median_buyout_unit, max_buyout_unit, min_bid_unit)
    SELECT %(finished)s, %(server)s, s.did,
           count(*),
           sum(s.units),
           count(*) FILTER (WHERE s.buyout > 0),
           min(s.buyout::numeric / s.units) FILTER (WHERE s.buyout > 0),
           percentile_cont(0.5) WITHIN GROUP (ORDER BY s.buyout::numeric / s.units) FILTER (WHERE s.buyout > 0),
           max(s.buyout::numeric / s.units) FILTER (WHERE s.buyout > 0),
           min(GREATEST(s.bid, s.min_bid)::numeric / s.units)
    FROM (SELECT t.*, GREATEST(COALESCE(t.quantity, 1), 1) AS units FROM _shard_scan_listings t) s
    GROUP BY s.did
    ON CONFLICT (server, did, "time") DO NOTHING
"""

# Listings we had as active that this (complete) scan did not see.
_END_LISTINGS = f"""
    UPDATE shard_exchange_listings
    SET ended_at = %(finished)s,
        end_reason = CASE
            WHEN expires_at IS NULL THEN '{END_REASON_UNKNOWN}'
            WHEN expires_at <= %(finished)s + make_interval(secs => {EXPIRY_SLACK_SECONDS}) THEN '{END_REASON_EXPIRED}'
            ELSE '{END_REASON_SOLD_OR_CANCELLED}'
        END
    WHERE server = %(server)s
      AND ended_at IS NULL
      AND last_scan_id <> %(scan)s
      AND last_seen_at < %(finished)s
"""

_PRUNE_LISTINGS = f"""
    DELETE FROM shard_exchange_listings
    WHERE ended_at IS NOT NULL AND ended_at < now() - make_interval(days => {RETENTION_DAYS})
"""

_PRUNE_SCANS = f"""
    DELETE FROM shard_exchange_scans
    WHERE finished_at < now() - make_interval(days => {RETENTION_DAYS})
"""


def _copy_row(row: ShardListingRow) -> tuple:
    # item_props goes in as JSON text: COPY's text format has no adapter for a dict.
    return (
        row.auction_id,
        row.item_id,
        row.did,
        row.quantity,
        row.min_level,
        row.bound,
        row.min_bid,
        row.bid,
        row.buyout,
        row.bidder_id,
        row.poster_id,
        row.posted_at,
        row.expires_at,
        row.duration_s,
        orjson.dumps(row.item_props).decode("utf-8"),
    )


async def async_persist_shard_exchange_scan(
    *,
    server: str,
    scan_id: UUID,
    started_at: datetime,
    finished_at: datetime,
    pages: int,
    complete: bool,
    listings: list[ShardListingRow],
) -> ShardScanResult:
    """Stores one scan in a single transaction; see the module docstring. Raises on a database error."""
    # The same listing twice in one scan would make the upsert touch a row twice. A scan holds each one
    # once; this keeps a bad payload from failing the whole scan.
    unique: dict[tuple[int, int], ShardListingRow] = {}
    for row in listings:
        unique[(row.auction_id, row.item_id)] = row
    rows = list(unique.values())

    result = ShardScanResult(listings=len(rows))
    params = {"server": server, "scan": scan_id, "finished": finished_at}

    async with get_async_dict_cursor(commit=True) as cur:
        # Two uploads for one server at once (a retry racing the original) take turns.
        await cur.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))", (f"shard_exchange:{server}",)
        )

        await cur.execute(
            """
            INSERT INTO shard_exchange_scans (scan_id, server, started_at, finished_at, pages, listing_count, complete)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (scan_id) DO NOTHING
            RETURNING scan_id
            """,
            (scan_id, server, started_at, finished_at, pages, len(rows), complete),
        )
        if await cur.fetchone() is None:
            result.duplicate = True
            return result

        await cur.execute(
            "SELECT count(*) AS n FROM shard_exchange_listings WHERE server = %s AND ended_at IS NULL",
            (server,),
        )
        result.previously_active = (await cur.fetchone())["n"]

        await cur.execute(_CREATE_TEMP)
        async with cur.copy(_COPY) as copy:
            for row in rows:
                await copy.write_row(_copy_row(row))

        # In this order: the bid history and the new-listing count compare against the stored rows,
        # so they have to be worked out before the upsert changes them.
        await cur.execute(_INSERT_BIDS, params)
        result.bid_changes = cur.rowcount

        await cur.execute(
            """
            SELECT count(*) AS n FROM _shard_scan_listings t
            WHERE NOT EXISTS (
                SELECT 1 FROM shard_exchange_listings l
                WHERE l.server = %s AND l.auction_id = t.auction_id AND l.item_id = t.item_id)
            """,
            (server,),
        )
        result.new_listings = (await cur.fetchone())["n"]

        await cur.execute(_UPSERT_LISTINGS, params)

        if complete:
            # Statistics from a scan that stopped early would be biased, so only a full one gets a row.
            await cur.execute(_INSERT_PRICE_HISTORY, params)
            result.price_rows = cur.rowcount

            if not rows:
                result.ended_skipped_reason = "the scan has no listings"
            elif (
                result.previously_active
                and len(rows)
                < result.previously_active * MIN_ACTIVE_FRACTION_TO_END_LISTINGS
            ):
                result.ended_skipped_reason = f"the scan has {len(rows)} listings but {result.previously_active} are active"
            else:
                await cur.execute(_END_LISTINGS, params)
                result.ended = cur.rowcount

        await cur.execute(_PRUNE_LISTINGS)
        await cur.execute(_PRUNE_SCANS)

    if result.ended_skipped_reason:
        logger.warning(
            "Shard Exchange scan %s of %s ended no listings: %s",
            scan_id,
            server,
            result.ended_skipped_reason,
        )
    return result


# =============================================
# Reading: item names and price history
# =============================================

PRICE_COLUMNS = (
    "listing_count",
    "unit_count",
    "buyout_listing_count",
    "min_buyout_unit",
    "median_buyout_unit",
    "max_buyout_unit",
    "min_bid_unit",
)


def _plain(value):
    """Numbers out of the database as JSON-friendly Python values (numeric comes back as Decimal)."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


async def async_upsert_item_names(
    items: list[tuple[int, str, str | None, int | None]],
) -> int:
    """Adds or renames items by data ID: (did, name, category, min_level). A category or minimum level
    of None keeps the stored one. Returns how many rows were written."""
    if not items:
        return 0
    async with get_async_dict_cursor(commit=True) as cur:
        await cur.executemany(
            """
            INSERT INTO item_names (did, name, category, min_level) VALUES (%s, %s, %s, %s)
            ON CONFLICT (did) DO UPDATE SET
                name = EXCLUDED.name,
                category = COALESCE(EXCLUDED.category, item_names.category),
                min_level = COALESCE(EXCLUDED.min_level, item_names.min_level),
                updated_at = now()
            WHERE item_names.name IS DISTINCT FROM EXCLUDED.name
               OR (EXCLUDED.category IS NOT NULL AND item_names.category IS DISTINCT FROM EXCLUDED.category)
               OR (EXCLUDED.min_level IS NOT NULL AND item_names.min_level IS DISTINCT FROM EXCLUDED.min_level)
            """,
            items,
        )
        return cur.rowcount


def _like_pattern(text: str) -> str:
    """The text as a LIKE pattern that matches it literally."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def async_search_item_names(
    name: str, limit: int, listed_only: bool
) -> list[dict]:
    """Items whose name contains `name` (any case): exact matches first, then names that start with it.
    listed_only keeps just the items that have price history, i.e. that were on the exchange.
    """
    escaped = _like_pattern(name)
    query = """
        SELECT n.did, n.name
        FROM item_names n
        WHERE n.name ILIKE %(contains)s
          {listed}
        ORDER BY (lower(n.name) = lower(%(name)s)) DESC,
                 (n.name ILIKE %(prefix)s) DESC,
                 n.name, n.did
        LIMIT %(limit)s
    """.format(
        listed=(
            "AND EXISTS (SELECT 1 FROM shard_exchange_price_history p WHERE p.did = n.did)"
            if listed_only
            else ""
        )
    )
    async with get_async_dict_cursor(commit=False) as cur:
        await cur.execute(
            query,
            {
                "contains": f"%{escaped}%",
                "prefix": f"{escaped}%",
                "name": name,
                "limit": limit,
            },
        )
        return list(await cur.fetchall())


async def async_get_item_name(did: int) -> str | None:
    async with get_async_dict_cursor(commit=False) as cur:
        await cur.execute("SELECT name FROM item_names WHERE did = %s", (did,))
        row = await cur.fetchone()
        return row["name"] if row else None


async def async_get_did_for_item_id(item_id: int) -> int | None:
    """The data ID (the item's type) of an item instance that appeared in a listing, on any server."""
    async with get_async_dict_cursor(commit=False) as cur:
        await cur.execute(
            "SELECT did FROM shard_exchange_listings WHERE item_id = %s ORDER BY last_seen_at DESC LIMIT 1",
            (item_id,),
        )
        row = await cur.fetchone()
        return row["did"] if row else None


async def async_get_price_history(
    server: str, did: int, since: datetime, bucket: str
) -> list[dict]:
    """Price history of one item type on one server since `since`, oldest first.

    bucket "scan": one row per scan (about hourly). bucket "day": one row per UTC day, combining that
    day's scans: the lowest minimum, the median of the scans' medians, the highest maximum, the lowest
    current price, and the average listing and unit counts."""
    if bucket == "day":
        query = """
            SELECT time_bucket('1 day', "time") AS "time",
                   round(avg(listing_count))::int AS listing_count,
                   round(avg(unit_count))::bigint AS unit_count,
                   round(avg(buyout_listing_count))::int AS buyout_listing_count,
                   min(min_buyout_unit) AS min_buyout_unit,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY median_buyout_unit) AS median_buyout_unit,
                   max(max_buyout_unit) AS max_buyout_unit,
                   min(min_bid_unit) AS min_bid_unit,
                   count(*)::int AS scans
            FROM shard_exchange_price_history
            WHERE server = %s AND did = %s AND "time" >= %s
            GROUP BY 1
            ORDER BY 1
        """
    else:
        query = """
            SELECT "time", listing_count, unit_count, buyout_listing_count, min_buyout_unit,
                   median_buyout_unit, max_buyout_unit, min_bid_unit, 1 AS scans
            FROM shard_exchange_price_history
            WHERE server = %s AND did = %s AND "time" >= %s
            ORDER BY "time"
        """
    async with get_async_dict_cursor(commit=False) as cur:
        await cur.execute(query, (server, did, since))
        rows = await cur.fetchall()
    return _price_rows(rows)


def _price_rows(rows) -> list[dict]:
    return [
        {
            "time": row["time"].astimezone(timezone.utc).isoformat(),
            **{column: _plain(row[column]) for column in PRICE_COLUMNS},
            "scans": row["scans"],
        }
        for row in rows
    ]


# =============================================
# Reading: browsing the active listings
# =============================================

# A listing is active until a complete scan misses it; one past its expiry time is over already.
_ACTIVE_LISTING = (
    "l.server = %(server)s AND l.ended_at IS NULL"
    " AND (l.expires_at IS NULL OR l.expires_at > now())"
)

# The item's minimum level: the listing's own (randomised items), else the item type's.
_EFFECTIVE_LEVEL = "COALESCE(l.min_level, n.min_level)"
_UNITS = "GREATEST(COALESCE(l.quantity, 1), 1)"

# Each sort is a list of ORDER BY terms; "{dir}" takes ASC or DESC. Listings without the sorted value
# (no name, no expiry, no buyout) go last either way.
_LISTING_SORTS: dict[str, list[str]] = {
    "name": ["(n.name IS NULL)", "lower(n.name) {dir}"],
    "time_left": ["(l.expires_at IS NULL)", "l.expires_at {dir}"],
    "price": ["GREATEST(l.bid, l.min_bid) {dir}"],
    "buyout": ["(l.buyout = 0)", "l.buyout {dir}"],
    "unit_price": ["GREATEST(l.bid, l.min_bid)::numeric / " + _UNITS + " {dir}"],
    "unit_buyout": ["(l.buyout = 0)", "l.buyout::numeric / " + _UNITS + " {dir}"],
}
# Makes the order total, so paging never repeats or skips a listing.
_LISTING_TIEBREAK = ["lower(n.name)", "l.expires_at", "l.auction_id", "l.item_id"]


@dataclass
class ListingFilters:
    words: list[str]
    category: str | None = None
    item_slug: str | None = None
    min_level: int | None = None
    max_level: int | None = None
    buyout_only: bool = False


def _listing_where(server: str, filters: ListingFilters) -> tuple[str, dict]:
    clauses = [_ACTIVE_LISTING]
    params: dict = {"server": server}
    for i, word in enumerate(filters.words):
        clauses.append(f"n.name ILIKE %(word{i})s")
        params[f"word{i}"] = f"%{_like_pattern(word)}%"
    if filters.category:
        clauses.append(
            "(n.category = %(category)s OR n.category LIKE %(category_prefix)s)"
        )
        params["category"] = filters.category
        params["category_prefix"] = f"{_like_pattern(filters.category)}.%"
    if filters.item_slug:
        clauses.append("n.slug = %(item_slug)s")
        params["item_slug"] = filters.item_slug
    if filters.min_level is not None:
        clauses.append(f"COALESCE({_EFFECTIVE_LEVEL}, 1) >= %(min_level)s")
        params["min_level"] = filters.min_level
    if filters.max_level is not None:
        clauses.append(f"COALESCE({_EFFECTIVE_LEVEL}, 1) <= %(max_level)s")
        params["max_level"] = filters.max_level
    if filters.buyout_only:
        clauses.append("l.buyout > 0")
    return " AND ".join(clauses), params


def _listing_order(sort: str, descending: bool) -> str:
    direction = "DESC" if descending else "ASC"
    terms = [term.format(dir=direction) for term in _LISTING_SORTS[sort]]
    return ", ".join(terms + _LISTING_TIEBREAK)


async def async_search_listings(
    server: str,
    filters: ListingFilters,
    *,
    sort: str,
    descending: bool,
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    """One page of a server's active listings, and how many match in all."""
    if sort not in _LISTING_SORTS:
        raise ValueError(f"unknown sort: {sort}")
    where, params = _listing_where(server, filters)
    source = f"""
        FROM shard_exchange_listings l
        LEFT JOIN item_names n ON n.did = l.did
        WHERE {where}
    """
    query = f"""
        SELECT l.server, l.auction_id, l.item_id, n.name, n.slug, n.category, l.quantity,
               {_EFFECTIVE_LEVEL} AS min_level, l.bound, l.min_bid, l.bid, l.buyout,
               l.posted_at, l.expires_at
        {source}
        ORDER BY {_listing_order(sort, descending)}
        LIMIT %(limit)s OFFSET %(offset)s
    """
    async with get_async_dict_cursor(commit=False) as cur:
        await cur.execute(f"SELECT count(*) AS n {source}", params)
        total = (await cur.fetchone())["n"]
        rows: list[dict] = []
        if total > offset:
            await cur.execute(query, {**params, "limit": limit, "offset": offset})
            rows = list(await cur.fetchall())
    return rows, total


async def async_get_category_counts(server: str) -> dict[str | None, int]:
    """How many active listings each stored category has on a server (None: no category known)."""
    async with get_async_dict_cursor(commit=False) as cur:
        await cur.execute(
            f"""
            SELECT n.category, count(*) AS n
            FROM shard_exchange_listings l
            LEFT JOIN item_names n ON n.did = l.did
            WHERE {_ACTIVE_LISTING}
            GROUP BY n.category
            """,
            {"server": server},
        )
        return {row["category"]: row["n"] for row in await cur.fetchall()}


async def async_get_last_scan_time(server: str) -> datetime | None:
    """When the last complete scan of a server finished."""
    async with get_async_dict_cursor(commit=False) as cur:
        await cur.execute(
            "SELECT max(finished_at) AS t FROM shard_exchange_scans WHERE server = %s AND complete",
            (server,),
        )
        row = await cur.fetchone()
        return row["t"] if row else None


async def async_get_item_by_slug(slug: str) -> dict | None:
    """An item as players know it, by its name's slug: the name most of the item types with that slug
    have, and their most common category. Items with the same name (random loot, for example) are one
    item here."""
    async with get_async_dict_cursor(commit=False) as cur:
        await cur.execute(
            """
            SELECT
                (SELECT name FROM item_names WHERE slug = %(slug)s
                 GROUP BY name ORDER BY count(*) DESC, name LIMIT 1) AS name,
                (SELECT category FROM item_names WHERE slug = %(slug)s AND category IS NOT NULL
                 GROUP BY category ORDER BY count(*) DESC, category LIMIT 1) AS category
            """,
            {"slug": slug},
        )
        row = await cur.fetchone()
        return row if row and row["name"] is not None else None


async def async_get_price_history_by_slug(
    server: str, slug: str, since: datetime, bucket: str
) -> list[dict]:
    """Price history of every item type with one name (by slug) on one server, oldest first.

    Each scan's rows are combined first (the lowest minimum, the median of the medians, the highest
    maximum, the lowest current price, the listing and unit counts added up); bucket "day" then
    combines a day's scans the way async_get_price_history does."""
    per_scan = """
        WITH per_scan AS (
            SELECT p."time",
                   sum(p.listing_count) AS listing_count,
                   sum(p.unit_count) AS unit_count,
                   sum(p.buyout_listing_count) AS buyout_listing_count,
                   min(p.min_buyout_unit) AS min_buyout_unit,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY p.median_buyout_unit) AS median_buyout_unit,
                   max(p.max_buyout_unit) AS max_buyout_unit,
                   min(p.min_bid_unit) AS min_bid_unit
            FROM shard_exchange_price_history p
            JOIN item_names n ON n.did = p.did
            WHERE n.slug = %(slug)s AND p.server = %(server)s AND p."time" >= %(since)s
            GROUP BY p."time"
        )
    """
    if bucket == "day":
        query = per_scan + """
            SELECT time_bucket('1 day', "time") AS "time",
                   round(avg(listing_count))::int AS listing_count,
                   round(avg(unit_count))::bigint AS unit_count,
                   round(avg(buyout_listing_count))::int AS buyout_listing_count,
                   min(min_buyout_unit) AS min_buyout_unit,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY median_buyout_unit) AS median_buyout_unit,
                   max(max_buyout_unit) AS max_buyout_unit,
                   min(min_bid_unit) AS min_bid_unit,
                   count(*)::int AS scans
            FROM per_scan
            GROUP BY 1
            ORDER BY 1
        """
    else:
        query = per_scan + """
            SELECT "time", listing_count, unit_count, buyout_listing_count, min_buyout_unit,
                   median_buyout_unit, max_buyout_unit, min_bid_unit, 1 AS scans
            FROM per_scan
            ORDER BY "time"
        """
    async with get_async_dict_cursor(commit=False) as cur:
        await cur.execute(query, {"server": server, "slug": slug, "since": since})
        return _price_rows(await cur.fetchall())
