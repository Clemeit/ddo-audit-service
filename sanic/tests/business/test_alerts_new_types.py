"""Alert types beyond lfm_posted: server status, friend login, Shard Exchange listing and outbid."""

import asyncio

import pytest
from pydantic import ValidationError

import business.alerts  # noqa: F401  (registers the alert types)
from business.alerts import registry
from business.alerts.base import AlertRule
from business.alerts.types.friend_login import FriendLoginAlertType, FriendLoginCriteria
from business.alerts.types.server_status import (
    ServerStatusAlertType,
    ServerStatusCriteria,
)
from business.alerts.types.shard_exchange_listed import (
    ShardExchangeListedAlertType,
    ShardExchangeListedCriteria,
)
from business.alerts.types.shard_exchange_outbid import (
    ShardExchangeOutbidAlertType,
    ShardExchangeOutbidCriteria,
)


def _rule(alert_type, criteria, rule_id=1, user_id=1):
    return AlertRule(
        id=rule_id,
        user_id=user_id,
        type=alert_type.name,
        criteria=alert_type.Criteria.model_validate(criteria),
    )


def test_new_types_are_registered_and_user_creatable():
    names = {t.name for t in registry.user_creatable_types()}
    assert {
        "server_status",
        "friend_login",
        "shard_exchange_listed",
        "shard_exchange_outbid",
    } <= names


# ----- server_status -----


def test_server_status_criteria_canonicalises_and_needs_a_direction():
    c = ServerStatusCriteria.model_validate({"server_names": ["thrane", "THRANE"]})
    assert c.server_names == ["Thrane"]
    with pytest.raises(ValidationError):
        ServerStatusCriteria.model_validate({"on_offline": False, "on_online": False})
    with pytest.raises(ValidationError):
        ServerStatusCriteria.model_validate({"server_names": ["Nowhere"]})


def test_server_status_matches_server_and_direction():
    t = ServerStatusAlertType()
    thrane_offline = _rule(t, {"server_names": ["Thrane"], "on_online": False}, 1)
    any_online = _rule(t, {"on_offline": False}, 2, user_id=2)
    index = t.build_index([thrane_offline, any_online])

    assert t.match({"server_name": "thrane", "is_online": False}, index) == [
        thrane_offline
    ]
    assert t.match({"server_name": "thrane", "is_online": True}, index) == [any_online]
    assert t.match({"server_name": "cormyr", "is_online": False}, index) == []
    assert t.match({"server_name": "cormyr"}, index) == []


def test_server_status_render_and_dedupe():
    t = ServerStatusAlertType()
    rule = _rule(t, {})
    alert = t.render({"server_name": "thrane", "is_online": False}, rule)
    assert alert.title == "Thrane is offline"
    assert alert.url == "/servers/thrane"
    assert t.dedupe_key({"server_name": "thrane", "is_online": True}) == (
        "server:thrane:online"
    )
    assert t.dedupe_ttl_secs < 6 * 60 * 60


# ----- friend_login -----


def test_friend_login_criteria():
    assert FriendLoginCriteria.model_validate({"character_ids": [5, 5, 6]}).character_ids == [5, 6]
    # all_friends ignores explicit IDs
    assert FriendLoginCriteria.model_validate(
        {"all_friends": True, "character_ids": [1]}
    ).character_ids == []
    with pytest.raises(ValidationError):
        FriendLoginCriteria.model_validate({})
    with pytest.raises(ValidationError):
        FriendLoginCriteria.model_validate({"character_ids": [0]})


def test_friend_login_expands_all_friends_from_user_context():
    loaded = []

    def loader(user_ids):
        loaded.append(sorted(user_ids))
        return {2: {"friends": {77, 88}, "verified": set()}}

    t = FriendLoginAlertType(context_loader=loader)
    picked = _rule(t, {"character_ids": [77]}, 1, user_id=1)
    everyone = _rule(t, {"all_friends": True}, 2, user_id=2)
    index = t.build_index([picked, everyone])

    assert loaded == [[2]]  # only users with all_friends rules are looked up
    assert set(r.id for r in t.match({"character_id": 77}, index)) == {1, 2}
    assert [r.id for r in t.match({"character_id": 88}, index)] == [2]
    assert t.match({"character_id": 99}, index) == []


def test_friend_login_survives_a_failing_context_loader():
    def loader(user_ids):
        raise RuntimeError("db down")

    t = FriendLoginAlertType(context_loader=loader)
    picked = _rule(t, {"character_ids": [77]})
    everyone = _rule(t, {"all_friends": True}, 2, user_id=2)
    index = t.build_index([picked, everyone])
    assert t.match({"character_id": 77}, index) == [picked]


def test_friend_login_render_has_no_ids():
    t = FriendLoginAlertType(context_loader=lambda ids: {})
    alert = t.render(
        {
            "character_id": 123456,
            "name": "Somebody",
            "server_name": "thrane",
            "total_level": 30,
            "classes": [{"name": "Fighter", "level": 20}, {"name": "Rogue", "level": 10}],
        },
        _rule(t, {"character_ids": [123456]}),
    )
    assert alert.title == "Somebody logged in"
    assert alert.body == "Level 30 · Fighter 20 / Rogue 10 · on Thrane"
    assert "123456" not in alert.title + alert.body + alert.url + alert.tag


# ----- shard_exchange_listed -----

LISTING = {
    "server_name": "thrane",
    "ref": "abc123",
    "slug": "some-item",
    "name": "Some Item",
    "quantity": 1,
    "min_bid": 100,
    "bid": 0,
    "buyout": 500,
}


def test_listed_criteria():
    c = ShardExchangeListedCriteria.model_validate({"item_slug": " Some-Item "})
    assert c.item_slug == "some-item"
    for bad in ({}, {"item_slug": "not a slug"}, {"item_slug": "x", "max_buyout": 0}):
        with pytest.raises(ValidationError):
            ShardExchangeListedCriteria.model_validate(bad)


@pytest.mark.parametrize(
    "criteria,data,expected",
    [
        ({}, {}, True),
        ({"server_names": ["Cormyr"]}, {}, False),
        ({"server_names": ["Thrane"]}, {}, True),
        ({"max_buyout": 500}, {}, True),
        ({"max_buyout": 499}, {}, False),
        ({"max_buyout": 1000}, {"buyout": 0}, False),
        ({"require_buyout": True}, {"buyout": 0}, False),
        ({"max_price": 100}, {}, True),
        ({"max_price": 100}, {"bid": 150}, False),
        ({}, {"slug": "other-item"}, False),
    ],
)
def test_listed_matching(criteria, data, expected):
    t = ShardExchangeListedAlertType()
    rule = _rule(t, {"item_slug": "some-item", **criteria})
    index = t.build_index([rule])
    assert (t.match({**LISTING, **data}, index) == [rule]) is expected


def test_listed_render_uses_slug_path_and_ref_only():
    t = ShardExchangeListedAlertType()
    alert = t.render(LISTING, _rule(t, {"item_slug": "some-item"}))
    assert alert.title == "Some Item listed on Thrane"
    assert alert.body == "Buyout 500 · Min bid 100"
    assert alert.url == "/shard-exchange/thrane?history=some-item"
    assert t.dedupe_key(LISTING) == "sx-list:abc123"


def test_listed_validate_for_user_fills_the_name(monkeypatch):
    import services.shard_exchange_db as db

    async def _item(slug):
        return {"name": "Some Item", "category": None} if slug == "some-item" else None

    monkeypatch.setattr(db, "async_get_item_by_slug", _item)
    t = ShardExchangeListedAlertType()
    c = t.Criteria.model_validate({"item_slug": "some-item", "item_name": "spoofed"})
    assert asyncio.run(t.validate_for_user(c, 1)).item_name == "Some Item"
    with pytest.raises(ValueError):
        asyncio.run(
            t.validate_for_user(t.Criteria.model_validate({"item_slug": "nope"}), 1)
        )


# ----- shard_exchange_outbid -----


def test_outbid_index_only_uses_verified_characters():
    def loader(user_ids):
        return {1: {"friends": set(), "verified": {10, 11}}, 2: {"verified": {20}}}

    t = ShardExchangeOutbidAlertType(context_loader=loader)
    picked = _rule(t, {"character_ids": [10, 99]}, 1, user_id=1)  # 99 is not verified
    everyone = _rule(t, {}, 2, user_id=2)
    index = t.build_index([picked, everyone])

    assert t.match({"prev_bidder_id": 10}, index) == [picked]
    assert t.match({"prev_bidder_id": 11}, index) == []
    assert t.match({"prev_bidder_id": 99}, index) == []
    assert t.match({"prev_bidder_id": 20}, index) == [everyone]


def test_outbid_validate_for_user_checks_ownership(monkeypatch):
    import services.alerts_db as alerts_db

    async def _verified(user_id):
        return {10} if user_id == 1 else set()

    monkeypatch.setattr(alerts_db, "async_get_verified_character_ids", _verified)
    t = ShardExchangeOutbidAlertType(context_loader=lambda ids: {})
    ok = ShardExchangeOutbidCriteria.model_validate({"character_ids": [10]})
    assert asyncio.run(t.validate_for_user(ok, 1)) is ok
    with pytest.raises(ValueError):
        asyncio.run(
            t.validate_for_user(
                ShardExchangeOutbidCriteria.model_validate({"character_ids": [11]}), 1
            )
        )
    with pytest.raises(ValueError):  # nothing verified at all
        asyncio.run(t.validate_for_user(ShardExchangeOutbidCriteria(), 2))


def test_outbid_render_has_no_ids():
    t = ShardExchangeOutbidAlertType(context_loader=lambda ids: {})
    data = {
        "server_name": "thrane",
        "ref": "abc123",
        "slug": "some-item",
        "name": "Some Item",
        "prev_bidder_id": 424242,
        "bid": 1500,
    }
    alert = t.render(data, _rule(t, {}))
    assert alert.title == "Outbid on Some Item"
    assert alert.body == "The bid is now 1,500 shards on Thrane."
    assert "424242" not in alert.title + alert.body + alert.url + alert.tag
    assert t.dedupe_key(data) == "sx-outbid:abc123:1500"
