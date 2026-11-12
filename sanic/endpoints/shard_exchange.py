"""
Shard Exchange (auction house) endpoints.
"""

from datetime import datetime, timedelta, timezone

import services.shard_exchange_db as shard_exchange_db
from business.shard_exchange import handle_incoming_shard_exchange
from constants.server import SERVER_NAMES_LOWERCASE
from constants.shard_exchange import (
    DEFAULT_PRICE_HISTORY_DAYS,
    ITEM_SEARCH_DEFAULT_LIMIT,
    ITEM_SEARCH_MAX_LIMIT,
    ITEM_SEARCH_MIN_LENGTH,
    MAX_PRICE_HISTORY_DAYS,
    PRICE_HISTORY_BUCKETS,
)
from models.shard_exchange import (
    ItemNamesRequestApiModel,
    ShardExchangeRequestApiModel,
)
from sanic import Blueprint
from sanic.request import Request
from sanic.response import json
from sanic_ext import openapi
from utils.game_ids import parse_game_id
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
        (item.did, item.name.strip())
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
@shard_exchange_blueprint.get("/items")
@openapi.summary("Find items by name")
@openapi.parameter(
    "name", str, "query", description="Part of the item's name (at least 2 letters)"
)
@openapi.parameter(
    "limit",
    int,
    "query",
    description="Most results to return (default 20, at most 100)",
)
@openapi.parameter(
    "listed_only",
    bool,
    "query",
    description="Only items that have been on the Shard Exchange (default true)",
)
@openapi.response(
    200, {"application/json": {"description": "Matching items: did, did_hex, name"}}
)
@openapi.response(400, description="Missing or too short a name, or a bad limit")
async def search_items(request: Request):
    """
    Method: GET

    Route: /shard-exchange/items?name=<text>

    Description: Find items by name. Each result's `did` is the item's type, which is what
    /shard-exchange/prices takes. Exact matches come first, then names that start with the text.
    """
    name = (request.args.get("name") or "").strip()
    if len(name) < ITEM_SEARCH_MIN_LENGTH:
        return json(
            {"message": f"name must be at least {ITEM_SEARCH_MIN_LENGTH} characters"},
            status=400,
        )
    try:
        limit = int(request.args.get("limit", ITEM_SEARCH_DEFAULT_LIMIT))
    except ValueError:
        return json({"message": "limit must be a number"}, status=400)
    if not 1 <= limit <= ITEM_SEARCH_MAX_LIMIT:
        return json(
            {"message": f"limit must be 1 to {ITEM_SEARCH_MAX_LIMIT}"}, status=400
        )
    listed_only = request.args.get("listed_only", "true").lower() not in (
        "false",
        "0",
        "no",
    )

    try:
        rows = await shard_exchange_db.async_search_item_names(name, limit, listed_only)
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json(
        {
            "data": [
                {"did": row["did"], "did_hex": hex(row["did"]), "name": row["name"]}
                for row in rows
            ]
        }
    )


@shard_exchange_blueprint.get("/prices")
@openapi.summary("Get the price history of an item type on a server")
@openapi.parameter("server", str, "query", required=True, description="Server name")
@openapi.parameter(
    "did",
    str,
    "query",
    description="The item's type (data ID), decimal or 0x hex. Give this or item_id",
)
@openapi.parameter(
    "item_id",
    str,
    "query",
    description="An item instance seen on the exchange; its type is looked up. Give this or did",
)
@openapi.parameter(
    "days", int, "query", description="How far back (default 180, at most 180)"
)
@openapi.parameter(
    "bucket",
    str,
    "query",
    description="'day' (default): one row per UTC day. 'scan': one row per scan, about hourly",
)
@openapi.response(
    200, {"application/json": {"description": "The item and its price history"}}
)
@openapi.response(400, description="A missing or invalid parameter")
@openapi.response(404, description="item_id was never seen in a listing")
async def get_price_history(request: Request):
    """
    Method: GET

    Route: /shard-exchange/prices?server=<server>&did=<did>

    Description: The asking-price history of one item type on one server, from the Shard Exchange.
    Prices are shards per unit (price divided by stack size) and are what sellers ask, not what items
    sold for. `min_buyout_unit`, `median_buyout_unit` and `max_buyout_unit` are over the listings that
    have a buyout price; `min_bid_unit` is the lowest current price (the bid if there is one, else the
    minimum bid). Items with random properties share a type, so their prices are mixed together.
    """
    server = (request.args.get("server") or "").strip().lower()
    if server not in SERVER_NAMES_LOWERCASE:
        return json({"message": "Invalid server name"}, status=400)

    did_text = request.args.get("did")
    item_id_text = request.args.get("item_id")
    if (did_text is None) == (item_id_text is None):
        return json({"message": "Give exactly one of did or item_id"}, status=400)

    try:
        days = int(request.args.get("days", DEFAULT_PRICE_HISTORY_DAYS))
    except ValueError:
        return json({"message": "days must be a number"}, status=400)
    if not 1 <= days <= MAX_PRICE_HISTORY_DAYS:
        return json(
            {"message": f"days must be 1 to {MAX_PRICE_HISTORY_DAYS}"}, status=400
        )

    bucket = (request.args.get("bucket") or "day").lower()
    if bucket not in PRICE_HISTORY_BUCKETS:
        return json(
            {"message": f"bucket must be one of: {', '.join(PRICE_HISTORY_BUCKETS)}"},
            status=400,
        )

    try:
        if did_text is not None:
            did = parse_game_id(did_text)
            if did is None:
                return json({"message": "did is not a valid ID"}, status=400)
        else:
            item_id = parse_game_id(item_id_text)
            if item_id is None:
                return json({"message": "item_id is not a valid ID"}, status=400)
            did = await shard_exchange_db.async_get_did_for_item_id(item_id)
            if did is None:
                return json(
                    {"message": "That item has not been seen on the Shard Exchange"},
                    status=404,
                )

        since = datetime.now(timezone.utc) - timedelta(days=days)
        data = await shard_exchange_db.async_get_price_history(
            server, did, since, bucket
        )
        name = await shard_exchange_db.async_get_item_name(did)
    except Exception as e:
        return json({"message": str(e)}, status=500)

    return json(
        {
            "server": server,
            "did": did,
            "did_hex": hex(did),
            "name": name,
            "days": days,
            "bucket": bucket,
            "data": data,
        }
    )
