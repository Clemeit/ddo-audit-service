"""
Shard Exchange (auction house) endpoints.
"""

import math
import re
from datetime import datetime, timedelta, timezone

import services.postgres as postgres_client
import services.shard_exchange_db as shard_exchange_db
from business.shard_exchange import (
    category_tree_with_counts,
    handle_incoming_shard_exchange,
    to_my_bid_listings,
    to_public_listing,
)
from constants.server import SERVER_NAMES_LOWERCASE
from constants.shard_exchange import (
    DEFAULT_PRICE_HISTORY_DAYS,
    ITEM_SLUG_PATTERN,
    LISTINGS_CACHE_SECONDS,
    LISTINGS_DEFAULT_LIMIT,
    LISTINGS_DEFAULT_SORT,
    LISTINGS_MAX_LEVEL,
    LISTINGS_MAX_LIMIT,
    LISTINGS_MAX_PAGE,
    LISTINGS_MAX_QUERY_LENGTH,
    LISTINGS_MAX_QUERY_WORDS,
    LISTINGS_SORTS,
    MAX_CATEGORY_SLUG_LENGTH,
    MAX_ITEM_SLUG_LENGTH,
    MAX_PRICE_HISTORY_DAYS,
    MY_BIDS_MAX_BODY_BYTES,
    MY_BIDS_MAX_RESULTS,
    SHARD_EXCHANGE_CATEGORIES,
    SHARD_EXCHANGE_CATEGORY_SLUGS,
)
from services.shard_exchange_db import ListingFilters
from models.shard_exchange import (
    ItemNamesRequestApiModel,
    MyBidsRequestApiModel,
    ShardExchangeRequestApiModel,
)
from sanic import Blueprint
from sanic.request import Request
from sanic.response import json
from sanic_ext import openapi
from utils.log import logMessage
from utils.request_body import (
    RequestBodyError,
    RequestBodyTooLarge,
    read_json_body,
)

shard_exchange_blueprint = Blueprint(
    "shard_exchange", url_prefix="/shard-exchange", version=1
)


# ======= Internal endpoints ========
@shard_exchange_blueprint.post("")
@openapi.exclude()
async def post_shard_exchange_scan(request: Request):
    """
    Method: POST

    Route: /shard-exchange

    Description: Store one scan of one server's Shard Exchange (the in-game auction house). Should only be
    called by DDO Audit Collections, once per complete scan, with the body gzipped (Content-Encoding: gzip).
    The scan is a full snapshot: a listing missing from a complete scan has ended. Posting the same scan_id
    again changes nothing.
    """
    try:
        payload = read_json_body(request)
    except RequestBodyTooLarge as e:
        return json({"message": str(e)}, status=413)
    except RequestBodyError as e:
        return json({"message": str(e)}, status=400)

    try:
        request_body = ShardExchangeRequestApiModel(**payload)
    except Exception:
        return json({"message": "Invalid request body"}, status=400)

    try:
        result = await handle_incoming_shard_exchange(request_body)
    except Exception as e:
        logMessage(
            message="Error handling incoming Shard Exchange scan",
            level="error",
            action="post_shard_exchange_scan",
            metadata={
                "error": str(e),
                "server": request_body.server,
                "scan_id": str(request_body.scan_id),
                "listings": len(request_body.listings),
            },
        )
        print(f"Error handling incoming Shard Exchange scan: {e}")
        return json({"message": str(e)}, status=500)

    return json(
        {
            "message": "success",
            "duplicate": result.duplicate,
            "listings": result.listings,
            "new_listings": result.new_listings,
            "ended": result.ended,
        }
    )


@shard_exchange_blueprint.post("/items")
@openapi.exclude()
async def post_item_names(request: Request):
    """
    Method: POST

    Route: /shard-exchange/items

    Description: Add or rename items in the item name table (type ID -> name). Internal: needs the API key.
    Names of items already stored are updated.
    """
    try:
        payload = read_json_body(request)
    except RequestBodyTooLarge as e:
        return json({"message": str(e)}, status=413)
    except RequestBodyError as e:
        return json({"message": str(e)}, status=400)

    try:
        request_body = ItemNamesRequestApiModel(**payload)
    except Exception:
        return json({"message": "Invalid request body"}, status=400)

    items = [
        (item.did, item.name.strip(), item.category, item.min_level)
        for item in request_body.items
        if item.name.strip()
    ]
    try:
        written = await shard_exchange_db.async_upsert_item_names(items)
    except Exception as e:
        logMessage(
            message="Error saving item names",
            level="error",
            action="post_item_names",
            metadata={"error": str(e), "items": len(items)},
        )
        return json({"message": str(e)}, status=500)

    return json({"message": "success", "written": written})


# ===================================


# ======= Client-facing endpoints =======


# ======= Browsing (no internal IDs in these responses) =======

_SLUG_RE = re.compile(ITEM_SLUG_PATTERN)

# The item endpoint's price buckets: its name for each -> the database's.
_ITEM_BUCKETS = {"day": "day", "hour": "scan"}


class _BadParameter(Exception):
    pass


def _int_arg(request: Request, name: str, default, low: int, high: int):
    text = request.args.get(name)
    if text is None or str(text).strip() == "":
        return default
    try:
        value = int(text)
    except ValueError:
        raise _BadParameter(f"{name} must be a number")
    if not low <= value <= high:
        raise _BadParameter(f"{name} must be {low} to {high}")
    return value


def _bool_arg(request: Request, name: str) -> bool:
    return (request.args.get(name) or "").strip().lower() in ("true", "1", "yes")


def _server_arg(request: Request, required: bool = True) -> str | None:
    server = (request.args.get("server") or "").strip().lower()
    if not server and not required:
        return None
    if server not in SERVER_NAMES_LOWERCASE:
        raise _BadParameter("Invalid server name")
    return server


def _category_arg(request: Request) -> str | None:
    category = (request.args.get("category") or "").strip().lower()
    if not category:
        return None
    if (
        len(category) > MAX_CATEGORY_SLUG_LENGTH
        or category not in SHARD_EXCHANGE_CATEGORY_SLUGS
    ):
        raise _BadParameter("Unknown category")
    return category


def _slug_arg(value: str | None) -> str | None:
    slug = (value or "").strip().lower()
    if not slug:
        return None
    if len(slug) > MAX_ITEM_SLUG_LENGTH or not _SLUG_RE.match(slug):
        raise _BadParameter("Invalid item")
    return slug


def _query_words(request: Request) -> list[str]:
    text = (request.args.get("q") or "").strip()
    if len(text) > LISTINGS_MAX_QUERY_LENGTH:
        raise _BadParameter(f"q must be at most {LISTINGS_MAX_QUERY_LENGTH} characters")
    words = text.split()
    if len(words) > LISTINGS_MAX_QUERY_WORDS:
        raise _BadParameter(f"q must be at most {LISTINGS_MAX_QUERY_WORDS} words")
    return words


def _cached(response):
    response.headers["Cache-Control"] = f"public, max-age={LISTINGS_CACHE_SECONDS}"
    return response


def _private(response):
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


@shard_exchange_blueprint.get("/listings")
@openapi.summary("Search a server's active Shard Exchange listings")
@openapi.parameter("server", str, "query", required=True, description="Server name")
@openapi.parameter("q", str, "query", description="Words that must all be in the item's name")
@openapi.parameter("category", str, "query", description="A category slug from /shard-exchange/categories")
@openapi.parameter("item", str, "query", description="An item slug: only that item's listings")
@openapi.parameter("min_level", int, "query", description="Lowest minimum level")
@openapi.parameter("max_level", int, "query", description="Highest minimum level")
@openapi.parameter("buyout_only", bool, "query", description="Only listings with a buyout price")
@openapi.parameter(
    "sort",
    str,
    "query",
    description="name (default), time_left, price, buyout, unit_price or unit_buyout",
)
@openapi.parameter("dir", str, "query", description="asc (default) or desc")
@openapi.parameter("page", int, "query", description="Page number, from 1")
@openapi.parameter("limit", int, "query", description=f"Listings per page (default {LISTINGS_DEFAULT_LIMIT}, at most {LISTINGS_MAX_LIMIT})")
@openapi.response(200, {"application/json": {"description": "One page of listings"}})
@openapi.response(400, description="A missing or invalid parameter")
async def search_listings(request: Request):
    """
    Method: GET

    Route: /shard-exchange/listings?server=<server>

    Description: The listings on a server's Shard Exchange as of the last update (about hourly), filtered,
    sorted and paged. Prices are in shards for the whole listing; `current_price` is the bid if there is
    one, else the minimum bid. `updated_at` says how fresh the data is.
    """
    try:
        server = _server_arg(request)
        filters = ListingFilters(
            words=_query_words(request),
            category=_category_arg(request),
            item_slug=_slug_arg(request.args.get("item")),
            min_level=_int_arg(request, "min_level", None, 0, LISTINGS_MAX_LEVEL),
            max_level=_int_arg(request, "max_level", None, 0, LISTINGS_MAX_LEVEL),
            buyout_only=_bool_arg(request, "buyout_only"),
        )
        if (
            filters.min_level is not None
            and filters.max_level is not None
            and filters.min_level > filters.max_level
        ):
            raise _BadParameter("min_level must not be above max_level")
        sort = (request.args.get("sort") or LISTINGS_DEFAULT_SORT).strip().lower()
        if sort not in LISTINGS_SORTS:
            raise _BadParameter(f"sort must be one of: {', '.join(LISTINGS_SORTS)}")
        direction = (request.args.get("dir") or "asc").strip().lower()
        if direction not in ("asc", "desc"):
            raise _BadParameter("dir must be asc or desc")
        page = _int_arg(request, "page", 1, 1, LISTINGS_MAX_PAGE)
        limit = _int_arg(request, "limit", LISTINGS_DEFAULT_LIMIT, 1, LISTINGS_MAX_LIMIT)
    except _BadParameter as e:
        return json({"message": str(e)}, status=400)

    try:
        rows, total = await shard_exchange_db.async_search_listings(
            server,
            filters,
            sort=sort,
            descending=direction == "desc",
            limit=limit,
            offset=(page - 1) * limit,
        )
        updated_at = await shard_exchange_db.async_get_last_scan_time(server)
    except Exception as e:
        logMessage(
            message="Error searching Shard Exchange listings",
            level="error",
            action="search_listings",
            metadata={"error": str(e), "server": server},
        )
        return json({"message": "Error searching listings"}, status=500)

    return _cached(
        json(
            {
                "server": server,
                "page": page,
                "limit": limit,
                "total": total,
                "pages": max(1, math.ceil(total / limit)),
                "updated_at": _iso(updated_at),
                "data": [to_public_listing(row) for row in rows],
            }
        )
    )


@shard_exchange_blueprint.post("/my-bids")
@openapi.summary("Bids placed by your verified characters")
@openapi.body({"application/json": MyBidsRequestApiModel})
@openapi.response(200, {"application/json": {"description": "Listings your characters bid on"}})
@openapi.response(400, description="A missing or invalid body")
async def get_my_bids(request: Request):
    """
    Method: POST

    Route: /shard-exchange/my-bids

    Description: Listings that your verified characters have bid on, including ended ones. Each has a
    `status` (leading, outbid or ended) and `my_bid`. Unknown tokens are ignored.
    """
    try:
        payload = read_json_body(request, max_bytes=MY_BIDS_MAX_BODY_BYTES)
        if not isinstance(payload, dict):
            raise ValueError("body must be an object")
        request_body = MyBidsRequestApiModel(**payload)
    except RequestBodyTooLarge as e:
        return json({"message": str(e)}, status=413)
    except Exception:
        return json({"message": "Invalid request body"}, status=400)

    try:
        resolved = await postgres_client.async_get_character_ids_by_access_tokens(
            request_body.access_tokens
        )
        character_ids = sorted(set(resolved.values()))
        rows = await shard_exchange_db.async_get_bids_for_characters(
            character_ids, MY_BIDS_MAX_RESULTS
        )
        updated_at = (
            await shard_exchange_db.async_get_latest_scan_time() if character_ids else None
        )
    except Exception as e:
        logMessage(
            message="Error reading Shard Exchange bids",
            level="error",
            action="get_my_bids",
            metadata={"error": str(e)},
        )
        return _private(json({"message": "Error reading bids"}, status=500))

    return _private(
        json(
            {
                "characters": character_ids,
                "updated_at": _iso(updated_at),
                "data": to_my_bid_listings(rows),
            }
        )
    )


@shard_exchange_blueprint.get("/categories")
@openapi.summary("The Shard Exchange's item categories")
@openapi.parameter(
    "server",
    str,
    "query",
    description="Optional: include each category's active listing count on this server",
)
@openapi.response(200, {"application/json": {"description": "The category tree"}})
@openapi.response(400, description="An invalid server name")
async def get_categories(request: Request):
    """
    Method: GET

    Route: /shard-exchange/categories

    Description: The category tree (slug, name, children). With a server, each category also has
    `count`, its active listings including its subcategories', and the response has `total` and
    `uncategorized` (listings whose item's category is not known).
    """
    try:
        server = _server_arg(request, required=False)
    except _BadParameter as e:
        return json({"message": str(e)}, status=400)

    if server is None:
        return _cached(json({"data": SHARD_EXCHANGE_CATEGORIES}))

    try:
        counts = await shard_exchange_db.async_get_category_counts(server)
        updated_at = await shard_exchange_db.async_get_last_scan_time(server)
    except Exception as e:
        logMessage(
            message="Error counting Shard Exchange categories",
            level="error",
            action="get_categories",
            metadata={"error": str(e), "server": server},
        )
        return json({"message": "Error counting categories"}, status=500)

    known = {
        category: count
        for category, count in counts.items()
        if category in SHARD_EXCHANGE_CATEGORY_SLUGS
    }
    return _cached(
        json(
            {
                "server": server,
                "updated_at": _iso(updated_at),
                "total": sum(counts.values()),
                "uncategorized": sum(counts.values()) - sum(known.values()),
                "data": category_tree_with_counts(SHARD_EXCHANGE_CATEGORIES, known),
            }
        )
    )


@shard_exchange_blueprint.get("/items/<slug:str>")
@openapi.summary("An item and its price history on a server, by slug")
@openapi.parameter("server", str, "query", required=True, description="Server name")
@openapi.parameter("days", int, "query", description="How far back (default 180, at most 180)")
@openapi.parameter("bucket", str, "query", description="'day' (default) or 'hour'")
@openapi.response(200, {"application/json": {"description": "The item and its price history"}})
@openapi.response(400, description="A missing or invalid parameter")
@openapi.response(404, description="No item has that slug")
async def get_item_by_slug(request: Request, slug: str):
    """
    Method: GET

    Route: /shard-exchange/items/<slug>?server=<server>

    Description: An item by its slug (from a listing) and the asking-price history of everything with
    that name on one server. Items that share a name (random loot, for example) are combined, so their
    prices are mixed together. Prices are shards per unit. bucket "hour" gives a row per update (about
    hourly), "day" a row per UTC day.
    """
    try:
        item_slug = _slug_arg(slug)
        if item_slug is None:
            raise _BadParameter("Invalid item")
        server = _server_arg(request)
        days = _int_arg(
            request, "days", DEFAULT_PRICE_HISTORY_DAYS, 1, MAX_PRICE_HISTORY_DAYS
        )
        bucket = (request.args.get("bucket") or "day").strip().lower()
        if bucket not in _ITEM_BUCKETS:
            raise _BadParameter(f"bucket must be one of: {', '.join(_ITEM_BUCKETS)}")
    except _BadParameter as e:
        return json({"message": str(e)}, status=400)

    try:
        item = await shard_exchange_db.async_get_item_by_slug(item_slug)
        if item is None:
            return json({"message": "No item has that name"}, status=404)
        since = datetime.now(timezone.utc) - timedelta(days=days)
        rows = await shard_exchange_db.async_get_price_history_by_slug(
            server, item_slug, since, _ITEM_BUCKETS[bucket]
        )
        # "updates": how many data points went into the row
        data = [
            {**{k: v for k, v in row.items() if k != "scans"}, "updates": row["scans"]}
            for row in rows
        ]
    except Exception as e:
        logMessage(
            message="Error reading a Shard Exchange item",
            level="error",
            action="get_item_by_slug",
            metadata={"error": str(e), "server": server, "slug": item_slug},
        )
        return json({"message": "Error reading the item"}, status=500)

    return _cached(
        json(
            {
                "server": server,
                "slug": item_slug,
                "name": item["name"],
                "category": item["category"],
                "days": days,
                "bucket": bucket,
                "data": data,
            }
        )
    )
