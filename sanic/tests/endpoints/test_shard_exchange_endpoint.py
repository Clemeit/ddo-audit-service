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
    body = {"items": [{"did": 5, "name": "  Pinch  "}, {"did": 6, "name": "   "}]}

    response = _post_to(make_request, run_async, shard_endpoints.post_item_names, **_gzipped(body))

    assert response.status == 200
    assert response_json(response)["written"] == 1
    assert stored == [(5, "Pinch")]  # trimmed; the blank name is skipped


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
