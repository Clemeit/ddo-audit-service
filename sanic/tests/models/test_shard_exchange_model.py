from uuid import uuid4

import pytest
from constants.shard_exchange import MAX_LISTINGS_PER_SCAN
from models.shard_exchange import (
    INT64_MAX,
    ShardExchangeRequestApiModel,
    ShardListingApiModel,
)
from pydantic import ValidationError


def _listing(**overrides):
    listing = {
        "auction_id": 1001,
        "item_id": 2001,
        "did": 3001,
        "quantity": 100,
        "min_level": 1,
        "bound": False,
        "min_bid": 98,
        "bid": 0,
        "buyout": 110,
        "bidder_id": 0,
        "poster_id": 4001,
        "posted_unix": 1_794_000_000,
        "expires_unix": 1_794_259_200,
        "duration_s": 259_200,
        "item_props": {"101": 1234},
    }
    listing.update(overrides)
    return listing


def _scan(**overrides):
    scan = {
        "server": "Cormyr",
        "scan_id": str(uuid4()),
        "started_unix": 1_794_000_100,
        "finished_unix": 1_794_000_400,
        "complete": True,
        "pages": 157,
        "listings": [_listing()],
    }
    scan.update(overrides)
    return scan


def test_valid_scan_parses():
    scan = ShardExchangeRequestApiModel(**_scan())
    assert scan.server == "cormyr"  # lowercased
    assert scan.listings[0].item_props == {"101": 1234}
    assert scan.complete is True


def test_unknown_values_use_the_defaults():
    listing = ShardListingApiModel(auction_id=1, item_id=2, did=3)
    assert listing.quantity == -1
    assert listing.min_level == -1
    assert listing.posted_unix == 0
    assert listing.item_props == {}


def test_unknown_server_is_rejected():
    with pytest.raises(ValidationError):
        ShardExchangeRequestApiModel(**_scan(server="Narnia"))


def test_scan_id_must_be_a_uuid():
    with pytest.raises(ValidationError):
        ShardExchangeRequestApiModel(**_scan(scan_id="not-a-uuid"))


def test_id_beyond_signed_64_bits_is_rejected():
    with pytest.raises(ValidationError):
        ShardExchangeRequestApiModel(
            **_scan(listings=[_listing(auction_id=INT64_MAX + 1)])
        )


def test_negative_price_is_rejected():
    with pytest.raises(ValidationError):
        ShardExchangeRequestApiModel(**_scan(listings=[_listing(buyout=-1)]))


def test_too_many_listings_is_rejected():
    listings = [{"auction_id": i, "item_id": i, "did": 1} for i in range(MAX_LISTINGS_PER_SCAN + 1)]
    with pytest.raises(ValidationError):
        ShardExchangeRequestApiModel(**_scan(listings=listings))


def test_missing_complete_flag_is_rejected():
    body = _scan()
    del body["complete"]
    with pytest.raises(ValidationError):
        ShardExchangeRequestApiModel(**body)


def test_an_empty_scan_is_valid():
    assert ShardExchangeRequestApiModel(**_scan(listings=[])).listings == []


def test_item_names_request_parses():
    from models.shard_exchange import ItemNamesRequestApiModel

    body = ItemNamesRequestApiModel(items=[{"did": 12345, "name": "Test Item"}])
    assert body.items[0].did == 12345


@pytest.mark.parametrize(
    "item",
    [
        {"did": 1, "name": ""},
        {"did": 1, "name": "x" * 201},
        {"did": -1, "name": "Name"},
        {"did": 2**63, "name": "Name"},
        {"name": "No did"},
    ],
)
def test_item_names_request_rejects_bad_items(item):
    from models.shard_exchange import ItemNamesRequestApiModel

    with pytest.raises(ValidationError):
        ItemNamesRequestApiModel(items=[item])



# ===== item names =====


def test_item_name_category_is_optional_and_checked():
    import pytest as _pytest

    from models.shard_exchange import ItemNameApiModel

    assert ItemNameApiModel(did=1, name="X").category is None
    assert ItemNameApiModel(did=1, name="X", category=" Armor.Cloth ").category == "armor.cloth"
    with _pytest.raises(Exception):
        ItemNameApiModel(did=1, name="X", category="armor.spoon")
    with _pytest.raises(Exception):
        ItemNameApiModel(did=1, name="X", min_level=-1)


def test_category_slugs_are_unique_and_well_formed():
    import re

    from constants.shard_exchange import SHARD_EXCHANGE_CATEGORIES, SHARD_EXCHANGE_CATEGORY_SLUGS

    def walk(categories):
        for c in categories:
            yield c["slug"]
            yield from walk(c["children"])

    slugs = list(walk(SHARD_EXCHANGE_CATEGORIES))
    assert len(slugs) == len(set(slugs)) == len(SHARD_EXCHANGE_CATEGORY_SLUGS) == 81
    assert all(re.match(r"^[a-z0-9-]+(\.[a-z0-9-]+)*$", s) for s in slugs)
    assert len(SHARD_EXCHANGE_CATEGORIES) == 19
