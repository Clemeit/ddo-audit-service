from datetime import datetime, timezone
from uuid import uuid4

import business.shard_exchange as shard_business
from models.shard_exchange import ShardExchangeRequestApiModel, ShardListingApiModel
from services.shard_exchange_db import ShardScanResult


def _listing(**overrides) -> ShardListingApiModel:
    values = {"auction_id": 1, "item_id": 2, "did": 3}
    values.update(overrides)
    return ShardListingApiModel(**values)


def test_none_values_become_nulls():
    row = shard_business.to_listing_row(_listing())
    assert row.quantity is None
    assert row.min_level is None
    assert row.bidder_id is None
    assert row.poster_id is None
    assert row.posted_at is None
    assert row.expires_at is None
    assert row.duration_s is None


def test_known_values_are_kept():
    row = shard_business.to_listing_row(
        _listing(
            quantity=100,
            min_level=1,
            bidder_id=7,
            poster_id=8,
            posted_unix=1_794_000_000,
            expires_unix=1_794_259_200,
            duration_s=259_200,
            item_props={"1": 2},
        )
    )
    assert row.quantity == 100
    assert row.min_level == 1
    assert row.bidder_id == 7
    assert row.poster_id == 8
    assert row.posted_at == datetime.fromtimestamp(1_794_000_000, tz=timezone.utc)
    assert row.expires_at == datetime.fromtimestamp(1_794_259_200, tz=timezone.utc)
    assert row.duration_s == 259_200
    assert row.item_props == {"1": 2}


def test_a_stack_of_one_and_a_level_zero_item_are_kept():
    row = shard_business.to_listing_row(_listing(quantity=1, min_level=0))
    assert row.quantity == 1
    assert row.min_level == 0


def test_handle_passes_the_scan_to_the_database_layer(monkeypatch, run_async):
    calls = []

    async def fake_persist(**kwargs):
        calls.append(kwargs)
        return ShardScanResult(listings=1)

    monkeypatch.setattr(
        shard_business.shard_exchange_db, "async_persist_shard_exchange_scan", fake_persist
    )
    scan_id = uuid4()
    body = ShardExchangeRequestApiModel(
        server="Thrane",
        scan_id=scan_id,
        started_unix=1_794_000_100,
        finished_unix=1_794_000_400,
        complete=False,
        pages=5,
        listings=[_listing()],
    )

    result = run_async(shard_business.handle_incoming_shard_exchange(body))

    assert result.listings == 1
    assert len(calls) == 1
    call = calls[0]
    assert call["server"] == "thrane"
    assert call["scan_id"] == scan_id
    assert call["started_at"] == datetime.fromtimestamp(1_794_000_100, tz=timezone.utc)
    assert call["finished_at"] == datetime.fromtimestamp(1_794_000_400, tz=timezone.utc)
    assert call["pages"] == 5
    assert call["complete"] is False
    assert [row.auction_id for row in call["listings"]] == [1]



# ===== public listings =====


def _db_row(**overrides) -> dict:
    row = {
        "server": "thrane",
        "auction_id": 123456789,
        "item_id": 987654321,
        "name": "Test Item",
        "slug": "test-item",
        "category": None,
        "quantity": None,
        "min_level": None,
        "bound": True,
        "min_bid": 10,
        "bid": 0,
        "buyout": 0,
        "posted_at": datetime(2026, 11, 11, 18, 0, tzinfo=timezone.utc),
        "expires_at": None,
    }
    row.update(overrides)
    return row


def test_public_listing_has_no_ids_and_nulls_for_none():
    listing = shard_business.to_public_listing(_db_row())

    assert not {"auction_id", "item_id", "did", "bidder_id", "poster_id", "item_props"} & set(listing)
    assert "123456789" not in listing["ref"] and "987654321" not in listing["ref"]
    assert (listing["bid"], listing["buyout"], listing["current_price"]) == (None, None, 10)
    assert listing["bound"] is True
    assert listing["posted_at"] == "2026-11-11T18:00:00+00:00"
    assert listing["expires_at"] is None


def test_listing_ref_is_stable_and_distinct():
    a = shard_business.listing_ref("thrane", 1, 2)
    assert a == shard_business.listing_ref("thrane", 1, 2)
    assert a != shard_business.listing_ref("thrane", 1, 3)
    assert a != shard_business.listing_ref("cormyr", 1, 2)
    assert len(a) == 16


def test_category_counts_roll_up():
    tree = [
        {"slug": "a", "name": "A", "children": [
            {"slug": "a.b", "name": "B", "children": []},
            {"slug": "a.c", "name": "C", "children": []},
        ]},
    ]
    result = shard_business.category_tree_with_counts(tree, {"a": 1, "a.b": 2})
    assert result[0]["count"] == 3
    assert [c["count"] for c in result[0]["children"]] == [2, 0]
