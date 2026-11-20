from business.alerts.producers import (
    MAX_LOGINS_PER_BATCH_FOR_ALERTS,
    friend_login_events,
    server_status_events,
    shard_exchange_listing_events,
    shard_exchange_outbid_events,
)


# ----- server status -----


def test_server_status_events_only_for_flips():
    previous = {
        "thrane": {"is_online": True},
        "cormyr": {"is_online": False},
        "moonsea": {"is_online": True},
    }
    incoming = {
        "thrane": {"is_online": False},  # went down
        "cormyr": {"is_online": True},  # came up
        "moonsea": {"is_online": True},  # unchanged
    }
    assert server_status_events(previous, incoming) == [
        {"server_name": "thrane", "is_online": False},
        {"server_name": "cormyr", "is_online": True},
    ]


def test_server_status_events_ignore_cold_cache_and_partial_updates():
    assert server_status_events(None, {"thrane": {"is_online": False}}) == []
    assert server_status_events({}, {"thrane": {"is_online": False}}) == []
    assert (
        server_status_events(
            {"thrane": {"is_online": True}}, {"thrane": {"character_count": 3}}
        )
        == []
    )
    assert (
        server_status_events(
            {"thrane": {"is_online": None}}, {"thrane": {"is_online": True}}
        )
        == []
    )


# ----- friend login -----


def _char(cid, **kw):
    return {"id": cid, "name": f"C{cid}", "total_level": 20, "classes": [], **kw}


def test_friend_login_events_for_new_characters_only():
    events = friend_login_events("Thrane", {1, 2}, {2: _char(2), 3: _char(3)})
    assert [e["character_id"] for e in events] == [3]
    assert events[0]["server_name"] == "thrane"
    assert events[0]["name"] == "C3"


def test_friend_login_events_skip_anonymous_and_cold_cache():
    assert friend_login_events("thrane", {1}, {3: _char(3, is_anonymous=True)}) == []
    assert friend_login_events("thrane", set(), {3: _char(3)}) == []
    burst = {i: _char(i) for i in range(10, 10 + MAX_LOGINS_PER_BATCH_FOR_ALERTS + 1)}
    assert friend_login_events("thrane", {1}, burst) == []


# ----- shard exchange -----


def test_listing_events_need_ref_and_slug():
    rows = [
        {"ref": "r1", "slug": "a", "name": "A", "buyout": 5, "min_bid": 1, "bid": 0},
        {"ref": "r2", "slug": None, "name": "Unknown"},
    ]
    events = shard_exchange_listing_events("Thrane", rows)
    assert [e["ref"] for e in events] == ["r1"]
    assert "auction_id" not in events[0]


def test_outbid_events_need_a_previous_bidder_who_changed():
    rows = [
        {"ref": "r1", "slug": "a", "prev_bidder_id": 7, "new_bidder_id": 8, "bid": 9},
        {"ref": "r2", "slug": "a", "prev_bidder_id": None, "new_bidder_id": 8},
        {"ref": "r3", "slug": "a", "prev_bidder_id": 7, "new_bidder_id": 7},
    ]
    events = shard_exchange_outbid_events("thrane", rows)
    assert [(e["ref"], e["prev_bidder_id"]) for e in events] == [("r1", 7)]
