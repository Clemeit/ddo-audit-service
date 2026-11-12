import gzip
import json

import pytest
from uuid import uuid4

import endpoints.shard_exchange as shard_endpoints
from services.shard_exchange_db import ShardScanResult


def _body(**overrides) -> dict:
    body = {
        "server": "cormyr",
        "scan_id": str(uuid4()),
        "started_unix": 1_794_000_100,
        "finished_unix": 1_794_000_400,
        "complete": True,
        "pages": 1,
        "listings": [{"auction_id": 1, "item_id": 2, "did": 3, "buyout": 10}],
    }
    body.update(overrides)
    return body


def _gzipped(body: dict) -> dict:
    return {
        "body": gzip.compress(json.dumps(body).encode("utf-8")),
        "headers": {"Content-Encoding": "gzip"},
    }


def _post(make_request, run_async, **request_args):
    request = make_request(method="POST", path="/v1/shard-exchange", **request_args)
    return run_async(shard_endpoints.post_shard_exchange_scan(request))


def _stub_handler(monkeypatch, result=None, error=None):
    calls = []

    async def fake_handle(request_body):
        calls.append(request_body)
        if error:
            raise error
        return result or ShardScanResult(listings=len(request_body.listings))

    monkeypatch.setattr(shard_endpoints, "handle_incoming_shard_exchange", fake_handle)
    monkeypatch.setattr(shard_endpoints, "logMessage", lambda **kwargs: None)
    return calls


def test_gzipped_scan_is_stored(monkeypatch, make_request, run_async, response_json):
    calls = _stub_handler(monkeypatch)

    response = _post(make_request, run_async, **_gzipped(_body()))

    assert response.status == 200
    payload = response_json(response)
    assert payload["message"] == "success"
    assert payload["listings"] == 1
    assert calls[0].server == "cormyr"
    assert calls[0].listings[0].buyout == 10


def test_plain_json_scan_is_stored(monkeypatch, make_request, run_async):
    calls = _stub_handler(monkeypatch)

    response = _post(
        make_request, run_async, body=json.dumps(_body()).encode("utf-8")
    )

    assert response.status == 200
    assert len(calls) == 1


def test_duplicate_scan_is_reported_as_success(
    monkeypatch, make_request, run_async, response_json
):
    _stub_handler(monkeypatch, result=ShardScanResult(duplicate=True))

    response = _post(make_request, run_async, **_gzipped(_body()))

    assert response.status == 200
    assert response_json(response)["duplicate"] is True


def test_corrupt_gzip_is_a_bad_request(monkeypatch, make_request, run_async):
    calls = _stub_handler(monkeypatch)

    response = _post(
        make_request,
        run_async,
        body=b"not gzip at all",
        headers={"Content-Encoding": "gzip"},
    )

    assert response.status == 400
    assert calls == []


def test_oversized_body_is_413(monkeypatch, make_request, run_async):
    calls = _stub_handler(monkeypatch)
    monkeypatch.setattr(
        shard_endpoints,
        "read_json_body",
        lambda request: (_ for _ in ()).throw(
            shard_endpoints.RequestBodyTooLarge("too big")
        ),
    )

    response = _post(make_request, run_async, **_gzipped(_body()))

    assert response.status == 413
    assert calls == []


def test_unknown_server_is_a_bad_request(monkeypatch, make_request, run_async):
    calls = _stub_handler(monkeypatch)

    response = _post(make_request, run_async, **_gzipped(_body(server="narnia")))

    assert response.status == 400
    assert calls == []


def test_a_json_array_body_is_a_bad_request(monkeypatch, make_request, run_async):
    calls = _stub_handler(monkeypatch)

    response = _post(make_request, run_async, body=b"[1, 2, 3]")

    assert response.status == 400
    assert calls == []


def test_database_failure_is_a_500(monkeypatch, make_request, run_async, response_json):
    _stub_handler(monkeypatch, error=RuntimeError("database is down"))

    response = _post(make_request, run_async, **_gzipped(_body()))

    assert response.status == 500
    assert "database is down" in response_json(response)["message"]


# ===== item names and price history =====


def _get(make_request, run_async, handler, **args):
    request = make_request(path="/v1/shard-exchange/x")
    request.args = {key: value for key, value in args.items() if value is not None}
    return run_async(handler(request))


def test_item_names_are_stored(monkeypatch, make_request, run_async, response_json):
    stored = []

    async def fake_upsert(items):
        stored.extend(items)
        return len(items)

    monkeypatch.setattr(shard_endpoints.shard_exchange_db, "async_upsert_item_names", fake_upsert)
    body = {
        "items": [
            {"did": 5, "name": "  Pinch  "},
            {"did": 6, "name": "   "},
            {"did": 7, "name": "Spoon", "category": "Weapons.Simple.Club", "min_level": 4},
        ]
    }

    response = _post_to(make_request, run_async, shard_endpoints.post_item_names, **_gzipped(body))

    assert response.status == 200
    assert response_json(response)["written"] == 2
    # trimmed; the blank name is skipped; the category is normalised
    assert stored == [(5, "Pinch", None, None), (7, "Spoon", "weapons.simple.club", 4)]


def test_item_names_with_an_unknown_category_are_400(make_request, run_async):
    body = {"items": [{"did": 5, "name": "Pinch", "category": "spoons"}]}
    response = _post_to(make_request, run_async, shard_endpoints.post_item_names, **_gzipped(body))
    assert response.status == 400


def test_item_names_bad_body_is_400(monkeypatch, make_request, run_async):
    response = _post_to(
        make_request, run_async, shard_endpoints.post_item_names, body=b'{"items": [{"did": 1}]}'
    )
    assert response.status == 400


def _post_to(make_request, run_async, handler, **request_args):
    request = make_request(method="POST", path="/v1/shard-exchange/items", **request_args)
    return run_async(handler(request))


def test_search_items_returns_matches(monkeypatch, make_request, run_async, response_json):
    seen = {}

    async def fake_search(name, limit, listed_only):
        seen.update(name=name, limit=limit, listed_only=listed_only)
        return [{"did": 0x3039, "name": "Test Item"}]

    monkeypatch.setattr(shard_endpoints.shard_exchange_db, "async_search_item_names", fake_search)

    response = _get(make_request, run_async, shard_endpoints.search_items, name=" widget ", limit="5", listed_only="false")

    assert response.status == 200
    assert response_json(response)["data"] == [
        {"did": 0x3039, "did_hex": "0x3039", "name": "Test Item"}
    ]
    assert seen == {"name": "widget", "limit": 5, "listed_only": False}


@pytest.mark.parametrize(
    "args",
    [{}, {"name": "a"}, {"name": "ab", "limit": "0"}, {"name": "ab", "limit": "101"}, {"name": "ab", "limit": "x"}],
)
def test_search_items_rejects_bad_requests(make_request, run_async, args):
    response = _get(make_request, run_async, shard_endpoints.search_items, **args)
    assert response.status == 400


def _stub_prices(monkeypatch, *, did_for_item=None, rows=None, name="Test Item"):
    calls = {}

    async def fake_history(server, did, since, bucket):
        calls.update(server=server, did=did, since=since, bucket=bucket)
        return rows if rows is not None else []

    async def fake_name(did):
        return name

    async def fake_did(item_id):
        calls["item_id"] = item_id
        return did_for_item

    db = shard_endpoints.shard_exchange_db
    monkeypatch.setattr(db, "async_get_price_history", fake_history)
    monkeypatch.setattr(db, "async_get_item_name", fake_name)
    monkeypatch.setattr(db, "async_get_did_for_item_id", fake_did)
    return calls


def test_prices_by_did(monkeypatch, make_request, run_async, response_json):
    calls = _stub_prices(monkeypatch, rows=[{"time": "2026-11-11T00:00:00+00:00", "min_buyout_unit": 3}])

    response = _get(make_request, run_async, shard_endpoints.get_price_history, server="Thrane", did="0x3039")

    assert response.status == 200
    payload = response_json(response)
    assert (payload["server"], payload["did_hex"], payload["name"]) == ("thrane", "0x3039", "Test Item")
    assert (payload["days"], payload["bucket"]) == (180, "day")
    assert payload["data"][0]["min_buyout_unit"] == 3
    assert (calls["server"], calls["did"], calls["bucket"]) == ("thrane", 0x3039, "day")


def test_prices_by_item_id_looks_up_the_type(monkeypatch, make_request, run_async, response_json):
    calls = _stub_prices(monkeypatch, did_for_item=0x3039)

    response = _get(make_request, run_async, shard_endpoints.get_price_history, server="thrane", item_id="0x2710", bucket="scan", days="30")

    assert response.status == 200
    assert calls["item_id"] == 0x2710
    assert calls["did"] == 0x3039
    assert calls["bucket"] == "scan"
    assert response_json(response)["days"] == 30


def test_prices_for_an_unseen_item_id_is_404(monkeypatch, make_request, run_async):
    _stub_prices(monkeypatch, did_for_item=None)

    response = _get(make_request, run_async, shard_endpoints.get_price_history, server="thrane", item_id="123")

    assert response.status == 404


@pytest.mark.parametrize(
    "args",
    [
        {"did": "1"},  # no server
        {"server": "narnia", "did": "1"},
        {"server": "thrane"},  # neither did nor item_id
        {"server": "thrane", "did": "1", "item_id": "2"},  # both
        {"server": "thrane", "did": "nope"},
        {"server": "thrane", "item_id": "nope"},
        {"server": "thrane", "did": "1", "days": "0"},
        {"server": "thrane", "did": "1", "days": "181"},
        {"server": "thrane", "did": "1", "days": "x"},
        {"server": "thrane", "did": "1", "bucket": "year"},
    ],
)
def test_prices_rejects_bad_requests(monkeypatch, make_request, run_async, args):
    _stub_prices(monkeypatch)
    response = _get(make_request, run_async, shard_endpoints.get_price_history, **args)
    assert response.status == 400



# ===== browsing: listings, categories, items by slug =====


def _listing_row(**overrides) -> dict:
    row = {
        "server": "thrane",
        "auction_id": 11,
        "item_id": 12,
        "name": "Test Item",
        "slug": "test-item",
        "category": "potions",
        "quantity": 5,
        "min_level": 3,
        "bound": False,
        "min_bid": 40,
        "bid": 0,
        "buyout": 100,
        "posted_at": None,
        "expires_at": None,
    }
    row.update(overrides)
    return row


def _stub_listings(monkeypatch, rows=None, total=None, last_scan=None):
    calls = {}

    async def fake_search(server, filters, *, sort, descending, limit, offset):
        calls.update(server=server, filters=filters, sort=sort, descending=descending, limit=limit, offset=offset)
        found = rows if rows is not None else [_listing_row()]
        return found, total if total is not None else len(found)

    async def fake_last_scan(server):
        return last_scan

    db = shard_endpoints.shard_exchange_db
    monkeypatch.setattr(db, "async_search_listings", fake_search)
    monkeypatch.setattr(db, "async_get_last_scan_time", fake_last_scan)
    monkeypatch.setattr(shard_endpoints, "logMessage", lambda **kwargs: None)
    return calls


def test_listings_defaults(monkeypatch, make_request, run_async, response_json):
    calls = _stub_listings(monkeypatch, total=45)

    response = _get(make_request, run_async, shard_endpoints.search_listings, server="Thrane")

    assert response.status == 200
    payload = response_json(response)
    assert (payload["page"], payload["limit"], payload["total"], payload["pages"]) == (1, 20, 45, 3)
    assert payload["updated_at"] is None
    assert (calls["server"], calls["sort"], calls["descending"], calls["limit"], calls["offset"]) == (
        "thrane", "name", False, 20, 0,
    )
    assert calls["filters"].words == []
    assert response.headers["Cache-Control"].startswith("public")


def test_listings_pass_their_filters(monkeypatch, make_request, run_async):
    calls = _stub_listings(monkeypatch)

    response = _get(
        make_request,
        run_async,
        shard_endpoints.search_listings,
        server="thrane",
        q="  jelly   cake ",
        category="Weapons.Simple",
        item="test-item",
        min_level="2",
        max_level="20",
        buyout_only="true",
        sort="unit_buyout",
        dir="desc",
        page="3",
        limit="50",
    )

    assert response.status == 200
    f = calls["filters"]
    assert f.words == ["jelly", "cake"]
    assert (f.category, f.item_slug, f.min_level, f.max_level, f.buyout_only) == (
        "weapons.simple", "test-item", 2, 20, True,
    )
    assert (calls["sort"], calls["descending"], calls["limit"], calls["offset"]) == ("unit_buyout", True, 50, 100)


def test_listings_show_no_game_ids(monkeypatch, make_request, run_async, response_json):
    _stub_listings(monkeypatch, rows=[_listing_row(bid=55)])

    response = _get(make_request, run_async, shard_endpoints.search_listings, server="thrane")

    listing = response_json(response)["data"][0]
    assert set(listing) == {
        "ref", "name", "slug", "category", "quantity", "min_level", "bound",
        "min_bid", "bid", "current_price", "buyout", "posted_at", "expires_at",
    }
    assert (listing["bid"], listing["current_price"], listing["buyout"]) == (55, 55, 100)
    assert len(listing["ref"]) == 16


def test_page_past_the_end_still_reports_the_total(monkeypatch, make_request, run_async, response_json):
    _stub_listings(monkeypatch, rows=[], total=0)

    payload = response_json(_get(make_request, run_async, shard_endpoints.search_listings, server="thrane", page="9"))

    assert (payload["total"], payload["pages"], payload["data"]) == (0, 1, [])


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"server": "narnia"},
        {"server": "thrane", "category": "spoons"},
        {"server": "thrane", "item": "Not A Slug"},
        {"server": "thrane", "item": "-bad"},
        {"server": "thrane", "min_level": "x"},
        {"server": "thrane", "min_level": "-1"},
        {"server": "thrane", "max_level": "101"},
        {"server": "thrane", "min_level": "10", "max_level": "5"},
        {"server": "thrane", "sort": "nonsense"},
        {"server": "thrane", "dir": "up"},
        {"server": "thrane", "page": "0"},
        {"server": "thrane", "limit": "101"},
        {"server": "thrane", "q": "x" * 101},
        {"server": "thrane", "q": "a b c d e f g h i"},
    ],
)
def test_listings_reject_bad_requests(monkeypatch, make_request, run_async, args):
    _stub_listings(monkeypatch)
    response = _get(make_request, run_async, shard_endpoints.search_listings, **args)
    assert response.status == 400


def test_listings_database_error_is_a_500_without_details(monkeypatch, make_request, run_async, response_json):
    _stub_listings(monkeypatch)

    async def broken(*args, **kwargs):
        raise RuntimeError("relation shard_exchange_listings is broken")

    monkeypatch.setattr(shard_endpoints.shard_exchange_db, "async_search_listings", broken)

    response = _get(make_request, run_async, shard_endpoints.search_listings, server="thrane")

    assert response.status == 500
    assert "shard_exchange_listings" not in response_json(response)["message"]


def test_categories_without_a_server_are_the_tree(make_request, run_async, response_json):
    response = _get(make_request, run_async, shard_endpoints.get_categories)

    assert response.status == 200
    tree = response_json(response)["data"]
    weapons = next(c for c in tree if c["slug"] == "weapons")
    assert [c["slug"] for c in weapons["children"]] == [
        "weapons.simple", "weapons.martial", "weapons.exotic", "weapons.throwing",
    ]
    assert "count" not in weapons


def test_categories_with_a_server_are_counted(monkeypatch, make_request, run_async, response_json):
    async def fake_counts(server):
        return {"weapons.simple.club": 2, "weapons.martial.maul": 3, "potions": 4, None: 5, "gone": 1}

    async def fake_last_scan(server):
        return None

    db = shard_endpoints.shard_exchange_db
    monkeypatch.setattr(db, "async_get_category_counts", fake_counts)
    monkeypatch.setattr(db, "async_get_last_scan_time", fake_last_scan)

    response = _get(make_request, run_async, shard_endpoints.get_categories, server="thrane")

    payload = response_json(response)
    assert (payload["total"], payload["uncategorized"]) == (15, 6)
    weapons = next(c for c in payload["data"] if c["slug"] == "weapons")
    assert weapons["count"] == 5
    simple = next(c for c in weapons["children"] if c["slug"] == "weapons.simple")
    assert simple["count"] == 2


def test_categories_reject_an_unknown_server(make_request, run_async):
    response = _get(make_request, run_async, shard_endpoints.get_categories, server="narnia")
    assert response.status == 400


def _stub_item(monkeypatch, item=None, rows=None, missing=False):
    calls = {}

    async def fake_item(slug):
        calls["slug"] = slug
        if missing:
            return None
        return item or {"name": "Test Item", "category": "potions"}

    async def fake_history(server, slug, since, bucket):
        calls.update(server=server, history_slug=slug, bucket=bucket)
        return rows or []

    db = shard_endpoints.shard_exchange_db
    monkeypatch.setattr(db, "async_get_item_by_slug", fake_item)
    monkeypatch.setattr(db, "async_get_price_history_by_slug", fake_history)
    monkeypatch.setattr(shard_endpoints, "logMessage", lambda **kwargs: None)
    return calls


def _get_item(make_request, run_async, slug, **args):
    request = make_request(path=f"/v1/shard-exchange/items/{slug}")
    request.args = {key: value for key, value in args.items() if value is not None}
    return run_async(shard_endpoints.get_item_by_slug(request, slug))


def test_item_by_slug(monkeypatch, make_request, run_async, response_json):
    calls = _stub_item(monkeypatch, rows=[{"time": "2026-11-11T00:00:00+00:00", "min_buyout_unit": 3, "scans": 1}])

    response = _get_item(make_request, run_async, "test-item", server="thrane", bucket="hour", days="7")

    assert response.status == 200
    payload = response_json(response)
    assert set(payload) == {"server", "slug", "name", "category", "days", "bucket", "data"}
    assert (payload["name"], payload["days"], payload["bucket"]) == ("Test Item", 7, "hour")
    assert payload["data"] == [{"time": "2026-11-11T00:00:00+00:00", "min_buyout_unit": 3, "updates": 1}]
    assert (calls["server"], calls["history_slug"], calls["bucket"]) == ("thrane", "test-item", "scan")


def test_unknown_item_slug_is_404(monkeypatch, make_request, run_async):
    _stub_item(monkeypatch, missing=True)
    response = _get_item(make_request, run_async, "nothing-here", server="thrane")
    assert response.status == 404


@pytest.mark.parametrize(
    "slug,args",
    [
        ("test-item", {}),
        ("test-item", {"server": "narnia"}),
        ("Test Item", {"server": "thrane"}),
        ("test-item", {"server": "thrane", "days": "0"}),
        ("test-item", {"server": "thrane", "bucket": "year"}),
        ("test-item", {"server": "thrane", "bucket": "scan"}),
    ],
)
def test_item_by_slug_rejects_bad_requests(monkeypatch, make_request, run_async, slug, args):
    _stub_item(monkeypatch)
    response = _get_item(make_request, run_async, slug, **args)
    assert response.status == 400
