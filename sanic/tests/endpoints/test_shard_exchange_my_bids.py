from datetime import datetime, timezone

import pytest

import endpoints.shard_exchange as shard_endpoints

TOKEN_A = "a" * 32
TOKEN_B = "b" * 32


def _row(**overrides) -> dict:
    row = {
        "character_id": 11,
        "my_amount": 50,
        "my_time": datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc),
        "server": "thrane",
        "auction_id": 1,
        "item_id": 2,
        "name": "Test Item",
        "slug": "test-item",
        "category": None,
        "quantity": None,
        "min_level": 5,
        "bound": False,
        "min_bid": 10,
        "bid": 50,
        "buyout": 100,
        "posted_at": None,
        "expires_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
        "ended_at": None,
        "is_top": True,
        "bids": [],
    }
    row.update(overrides)
    return row


def _stub(monkeypatch, resolved=None, rows=None, error=None):
    calls = {}

    async def fake_resolve(tokens):
        calls["tokens"] = tokens
        if error:
            raise error
        return resolved if resolved is not None else {TOKEN_A: 11}

    async def fake_bids(character_ids, limit):
        calls["character_ids"] = character_ids
        return rows if rows is not None else [_row()]

    async def fake_latest():
        return None

    monkeypatch.setattr(
        shard_endpoints.postgres_client,
        "async_get_character_ids_by_access_tokens",
        fake_resolve,
    )
    db = shard_endpoints.shard_exchange_db
    monkeypatch.setattr(db, "async_get_bids_for_characters", fake_bids)
    monkeypatch.setattr(db, "async_get_latest_scan_time", fake_latest)
    monkeypatch.setattr(shard_endpoints, "logMessage", lambda **kwargs: None)
    return calls


def _post(make_request, run_async, body):
    request = make_request(
        method="POST", path="/v1/shard-exchange/my-bids", json_body=body
    )
    return run_async(shard_endpoints.get_my_bids(request))


def test_my_bids_happy_path(monkeypatch, make_request, run_async, response_json):
    calls = _stub(monkeypatch)

    response = _post(
        make_request, run_async, {"access_tokens": [TOKEN_A, TOKEN_A, TOKEN_B]}
    )

    assert response.status == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert calls["tokens"] == [TOKEN_A, TOKEN_B]
    payload = response_json(response)
    assert payload["characters"] == [11]
    listing = payload["data"][0]
    assert listing["status"] == "leading"
    assert listing["server"] == "thrane"
    assert listing["my_bid"] == {
        "character_id": 11,
        "amount": 50,
        "at": "2026-11-11T18:00:00+00:00",
    }
    for key in ("auction_id", "item_id", "bidder_id", "poster_id", "is_top"):
        assert key not in listing


def test_unknown_tokens_return_nothing(
    monkeypatch, make_request, run_async, response_json
):
    calls = _stub(monkeypatch, resolved={}, rows=[])

    response = _post(make_request, run_async, {"access_tokens": [TOKEN_B]})

    assert response.status == 200
    assert response_json(response) == {"characters": [], "updated_at": None, "data": []}
    assert calls["character_ids"] == []


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"access_tokens": []},
        {"access_tokens": "abc"},
        {"access_tokens": [123]},
        {"access_tokens": ["has spaces in it"]},
        {"access_tokens": ["x" * 200]},
        {"access_tokens": [TOKEN_A] * 51},
        {"access_tokens": [TOKEN_A + "\n"]},
        {"access_tokens": ["' OR 1=1 --"]},
        {"access_tokens": [None]},
        [TOKEN_A],
        "text",
        None,
    ],
)
def test_bad_bodies_are_400(monkeypatch, make_request, run_async, body):
    _stub(monkeypatch)
    response = _post(make_request, run_async, body)
    assert response.status == 400


def test_oversized_body_is_413_without_touching_the_database(
    monkeypatch, make_request, run_async
):
    calls = _stub(monkeypatch)
    body = {"access_tokens": [TOKEN_A], "padding": "x" * (20 * 1024)}

    response = _post(make_request, run_async, body)

    assert response.status == 413
    assert "tokens" not in calls


def test_database_error_is_a_500_without_details(
    monkeypatch, make_request, run_async, response_json
):
    _stub(monkeypatch, error=RuntimeError("secret detail"))

    response = _post(make_request, run_async, {"access_tokens": [TOKEN_A]})

    assert response.status == 500
    assert "secret detail" not in response.body.decode()
