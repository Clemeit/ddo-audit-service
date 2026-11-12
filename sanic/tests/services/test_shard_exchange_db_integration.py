"""Runs the Shard Exchange SQL against a real TimescaleDB. Skipped unless SHARD_EXCHANGE_TEST_DB=1.

The SQL (a temp table filled with COPY, an upsert, percentile_cont, hypertables) cannot be checked with
mocks. To run it, start a throwaway database from the production image, load postgres/init.sql into it,
and point the POSTGRES_* variables at it. They must be set before anything imports services.postgres, so
run this file on its own:

    docker run -d --name sx-test-db -e POSTGRES_USER=pgadmin -e POSTGRES_PASSWORD=test \
        -e POSTGRES_DB=ddoaudit -p 55432:5432 timescale/timescaledb:2.22.1-pg16
    docker cp postgres/init.sql sx-test-db:/tmp/init.sql
    docker exec sx-test-db psql -U pgadmin -d ddoaudit -f /tmp/init.sql
    SHARD_EXCHANGE_TEST_DB=1 POSTGRES_HOST=localhost POSTGRES_PORT=55432 POSTGRES_DB=ddoaudit \
        POSTGRES_USER=pgadmin POSTGRES_PASSWORD=test \
        pytest tests/services/test_shard_exchange_db_integration.py

Every test uses its own server name, so they do not see each other's rows.
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("SHARD_EXCHANGE_TEST_DB") != "1",
    reason="needs a TimescaleDB with postgres/init.sql loaded (see this file's docstring)",
)

NOW = datetime(2026, 11, 11, 20, 0, 0, tzinfo=timezone.utc)


def _row(auction_id, *, did=1, quantity=None, bid=0, min_bid=10, buyout=0, expires=None, bidder=None, **extra):
    from services.shard_exchange_db import ShardListingRow

    return ShardListingRow(
        auction_id=auction_id,
        item_id=auction_id + 1000,
        did=did,
        quantity=quantity,
        min_level=extra.get("min_level"),
        bound=False,
        min_bid=min_bid,
        bid=bid,
        buyout=buyout,
        bidder_id=bidder,
        poster_id=77,
        posted_at=NOW - timedelta(hours=1),
        expires_at=expires,
        duration_s=259200,
        item_props=extra.get("props", {"101": 5}),
    )


def _run(test):
    """Runs an async test body with the connection pool open."""

    async def main():
        import services.postgres as postgres

        await postgres.initialize_async_postgres()
        try:
            await test(postgres)
        finally:
            await postgres.close_async_postgres()

    # psycopg's async mode cannot use Windows' default (Proactor) event loop; the service runs on Linux.
    loop_factory = asyncio.SelectorEventLoop if sys.platform == "win32" else None
    asyncio.run(main(), loop_factory=loop_factory)


async def _persist(server, rows, *, complete=True, finished=NOW, scan_id=None):
    from services.shard_exchange_db import async_persist_shard_exchange_scan

    return await async_persist_shard_exchange_scan(
        server=server,
        scan_id=scan_id or uuid4(),
        started_at=finished - timedelta(minutes=3),
        finished_at=finished,
        pages=max(1, len(rows) // 20),
        complete=complete,
        listings=rows,
    )


async def _one(postgres, sql, *params):
    async with postgres.get_async_dict_cursor(commit=False) as cur:
        await cur.execute(sql, params)
        return await cur.fetchone()


async def _all(postgres, sql, *params):
    async with postgres.get_async_dict_cursor(commit=False) as cur:
        await cur.execute(sql, params)
        return await cur.fetchall()


def _server():
    return f"test-{uuid4().hex[:12]}"


def test_first_scan_stores_listings_bids_and_prices():
    server = _server()

    async def test(pg):
        rows = [
            _row(1, did=100, quantity=10, buyout=100, min_bid=50),
            _row(2, did=100, quantity=10, buyout=300, bid=120, bidder=5),
            _row(3, did=100, quantity=10, buyout=200, min_bid=80),
            _row(4, did=200, quantity=None, buyout=0, min_bid=7),
        ]
        result = await _persist(server, rows)

        assert (result.duplicate, result.listings, result.new_listings) == (False, 4, 4)
        assert result.bid_changes == 1
        assert result.ended == 0

        stored = await _one(pg, "SELECT * FROM shard_exchange_listings WHERE server=%s AND auction_id=2", server)
        assert stored["bid"] == 120 and stored["bidder_id"] == 5
        assert stored["quantity"] == 10
        assert stored["item_props"] == {"101": 5}
        assert stored["ended_at"] is None
        unstacked = await _one(pg, "SELECT quantity, bidder_id FROM shard_exchange_listings WHERE server=%s AND auction_id=4", server)
        assert unstacked["quantity"] is None and unstacked["bidder_id"] is None

        # did 100: per unit buyouts 10, 20, 30; the lowest current price per unit is min(50, 120, 80) / 10
        prices = await _one(pg, "SELECT * FROM shard_exchange_price_history WHERE server=%s AND did=100", server)
        assert prices["listing_count"] == 3 and prices["unit_count"] == 30
        assert prices["buyout_listing_count"] == 3
        assert (prices["min_buyout_unit"], prices["median_buyout_unit"], prices["max_buyout_unit"]) == (10, 20, 30)
        assert prices["min_bid_unit"] == 5
        # did 200 has no buyout and one unit
        no_buyout = await _one(pg, "SELECT * FROM shard_exchange_price_history WHERE server=%s AND did=200", server)
        assert no_buyout["buyout_listing_count"] == 0 and no_buyout["min_buyout_unit"] is None
        assert no_buyout["min_bid_unit"] == 7 and no_buyout["unit_count"] == 1

    _run(test)


def test_posting_the_same_scan_again_changes_nothing():
    server = _server()

    async def test(pg):
        scan_id = uuid4()
        rows = [_row(1, bid=30, bidder=5), _row(2)]
        await _persist(server, rows, scan_id=scan_id)
        again = await _persist(server, rows, scan_id=scan_id, finished=NOW + timedelta(hours=1))

        assert again.duplicate is True
        assert (await _one(pg, "SELECT count(*) n FROM shard_exchange_scans WHERE server=%s", server))["n"] == 1
        assert (await _one(pg, "SELECT count(*) n FROM shard_exchange_bids WHERE server=%s", server))["n"] == 1
        last = await _one(pg, "SELECT max(last_seen_at) t FROM shard_exchange_listings WHERE server=%s", server)
        assert last["t"] == NOW

    _run(test)


def test_listings_missing_from_a_complete_scan_are_ended_with_a_reason():
    server = _server()

    async def test(pg):
        later = NOW + timedelta(hours=1)
        rows = [_row(i) for i in range(1, 11)]
        rows[0] = _row(1, expires=later + timedelta(hours=20))  # still had a day: sold or cancelled
        rows[1] = _row(2, expires=later - timedelta(minutes=10))  # ran out of time: expired
        rows[2] = _row(3, expires=None)  # unknown
        rows[3] = _row(4, expires=later + timedelta(seconds=60))  # within the expiry slack: expired
        await _persist(server, rows)

        result = await _persist(server, rows[4:] + [_row(11)], finished=later)  # 7 of 10 still there, plus one new

        assert result.ended == 4 and result.new_listings == 1
        reasons = {
            r["auction_id"]: r["end_reason"]
            for r in await _all(pg, "SELECT auction_id, end_reason FROM shard_exchange_listings WHERE server=%s AND ended_at IS NOT NULL", server)
        }
        assert reasons == {1: "sold_or_cancelled", 2: "expired", 3: "unknown", 4: "expired"}
        active = await _one(pg, "SELECT count(*) n FROM shard_exchange_listings WHERE server=%s AND ended_at IS NULL", server)
        assert active["n"] == 7

    _run(test)


def test_a_changed_bid_is_recorded_once_and_updates_the_listing():
    server = _server()

    async def test(pg):
        await _persist(server, [_row(1, bid=0), _row(2, bid=50, bidder=5)])
        result = await _persist(
            server,
            [_row(1, bid=40, bidder=6), _row(2, bid=50, bidder=5)],
            finished=NOW + timedelta(hours=1),
        )

        assert result.bid_changes == 1  # listing 2's bid did not change
        bids = await _all(pg, 'SELECT auction_id, bid FROM shard_exchange_bids WHERE server=%s ORDER BY "time", auction_id', server)
        assert [(b["auction_id"], b["bid"]) for b in bids] == [(2, 50), (1, 40)]
        stored = await _one(pg, "SELECT bid, bidder_id FROM shard_exchange_listings WHERE server=%s AND auction_id=1", server)
        assert (stored["bid"], stored["bidder_id"]) == (40, 6)

    _run(test)


def test_a_partial_scan_adds_and_updates_but_ends_nothing_and_has_no_prices():
    server = _server()

    async def test(pg):
        await _persist(server, [_row(i) for i in range(1, 7)])
        result = await _persist(server, [_row(1, bid=9, bidder=5)], complete=False, finished=NOW + timedelta(hours=1))

        assert result.ended == 0 and result.price_rows == 0
        active = await _one(pg, "SELECT count(*) n FROM shard_exchange_listings WHERE server=%s AND ended_at IS NULL", server)
        assert active["n"] == 6
        prices = await _one(pg, "SELECT count(*) n FROM shard_exchange_price_history WHERE server=%s", server)
        assert prices["n"] == 1  # the first scan's one did; the partial scan added none

    _run(test)


def test_a_scan_far_smaller_than_the_active_set_ends_nothing():
    server = _server()

    async def test(pg):
        await _persist(server, [_row(i) for i in range(1, 11)])
        result = await _persist(server, [_row(1), _row(2)], finished=NOW + timedelta(hours=1))

        assert result.ended == 0
        assert result.ended_skipped_reason is not None
        active = await _one(pg, "SELECT count(*) n FROM shard_exchange_listings WHERE server=%s AND ended_at IS NULL", server)
        assert active["n"] == 10

    _run(test)


def test_an_empty_complete_scan_ends_nothing():
    server = _server()

    async def test(pg):
        await _persist(server, [_row(1), _row(2)])
        result = await _persist(server, [], finished=NOW + timedelta(hours=1))

        assert result.ended == 0 and result.ended_skipped_reason is not None

    _run(test)


def test_an_ended_listing_that_reappears_is_active_again():
    server = _server()

    async def test(pg):
        rows = [_row(i) for i in range(1, 5)]
        await _persist(server, rows)
        await _persist(server, rows[:3], finished=NOW + timedelta(hours=1))
        assert (await _one(pg, "SELECT ended_at FROM shard_exchange_listings WHERE server=%s AND auction_id=4", server))["ended_at"] is not None

        await _persist(server, rows, finished=NOW + timedelta(hours=2))
        again = await _one(pg, "SELECT ended_at, end_reason FROM shard_exchange_listings WHERE server=%s AND auction_id=4", server)
        assert again["ended_at"] is None and again["end_reason"] is None

    _run(test)


def test_an_older_scan_arriving_late_does_not_overwrite_a_newer_one():
    server = _server()

    async def test(pg):
        await _persist(server, [_row(1, bid=90, bidder=6)], finished=NOW + timedelta(hours=2))
        await _persist(server, [_row(1, bid=50, bidder=5)], finished=NOW + timedelta(hours=1))

        stored = await _one(pg, "SELECT bid, bidder_id, last_seen_at FROM shard_exchange_listings WHERE server=%s AND auction_id=1", server)
        assert (stored["bid"], stored["bidder_id"]) == (90, 6)
        assert stored["last_seen_at"] == NOW + timedelta(hours=2)

    _run(test)


def test_the_same_listing_twice_in_one_scan_is_stored_once():
    server = _server()

    async def test(pg):
        result = await _persist(server, [_row(1, bid=10, bidder=5), _row(1, bid=20, bidder=6)])

        assert result.listings == 1
        stored = await _one(pg, "SELECT bid FROM shard_exchange_listings WHERE server=%s", server)
        assert stored["bid"] == 20

    _run(test)


def test_old_ended_listings_and_scans_are_pruned():
    server = _server()

    async def test(pg):
        # Written straight to the tables: a scan that old would prune itself the moment it was stored.
        old = datetime.now(timezone.utc) - timedelta(days=200)
        async with pg.get_async_dict_cursor(commit=True) as cur:
            await cur.execute(
                "INSERT INTO shard_exchange_scans (scan_id, server, started_at, finished_at, complete) VALUES (%s, %s, %s, %s, true)",
                (uuid4(), server, old, old),
            )
            for auction_id, ended_at in ((1, old), (2, None)):
                await cur.execute(
                    """INSERT INTO shard_exchange_listings
                       (server, auction_id, item_id, did, first_seen_at, last_seen_at, first_scan_id, last_scan_id, ended_at, end_reason)
                       VALUES (%s, %s, 1, 1, %s, %s, %s, %s, %s, %s)""",
                    (server, auction_id, old, old, uuid4(), uuid4(), ended_at, "expired" if ended_at else None),
                )

        await _persist(server, [_row(3)], finished=datetime.now(timezone.utc))  # any scan prunes

        remaining = await _all(pg, "SELECT auction_id FROM shard_exchange_listings WHERE server=%s ORDER BY auction_id", server)
        assert [r["auction_id"] for r in remaining] == [2, 3]  # the old ended one is gone, the old active one is not
        scans = await _one(pg, "SELECT count(*) n FROM shard_exchange_scans WHERE server=%s", server)
        assert scans["n"] == 1

    _run(test)


# ===== item names and price history =====


def test_item_names_are_added_and_renamed():
    from services.shard_exchange_db import async_get_item_name, async_upsert_item_names

    did = 900_000 + int(uuid4().hex[:4], 16)

    async def test(pg):
        assert await async_upsert_item_names([(did, "Old Name", None, None)]) == 1
        assert await async_get_item_name(did) == "Old Name"
        assert await async_upsert_item_names([(did, "Old Name", None, None)]) == 0  # unchanged: nothing written
        assert await async_upsert_item_names([(did, "New Name", None, None)]) == 1
        assert await async_get_item_name(did) == "New Name"
        assert await async_get_item_name(did + 100000) is None

    _run(test)


def test_item_search_ranks_exact_then_prefix_and_can_require_a_listing():
    from services.shard_exchange_db import async_search_item_names, async_upsert_item_names

    tag = uuid4().hex[:8]
    base = 1_000_000 + int(tag[:5], 16)
    names = [
        (base + 1, f"Large Test Widget {tag}"),
        (base + 2, f"Test Widget {tag} Extra"),
        (base + 3, f"test widget {tag}"),
        (base + 4, f"100%_{tag}"),
    ]
    server = _server()

    async def test(pg):
        await async_upsert_item_names([(d, n, None, None) for d, n in names])

        found = await async_search_item_names(f"test widget {tag}", 10, listed_only=False)
        assert [r["did"] for r in found] == [base + 3, base + 2, base + 1]  # exact, starts with, contains

        # only items that were on the exchange (have price history)
        assert await async_search_item_names(f"test widget {tag}", 10, listed_only=True) == []
        await _persist(server, [_row(1, did=base + 2, buyout=10)])
        listed = await async_search_item_names(f"test widget {tag}", 10, listed_only=True)
        assert [r["did"] for r in listed] == [base + 2]

        # % and _ in the search are literal, not wildcards
        assert [r["did"] for r in await async_search_item_names(f"1%{tag}", 10, False)] == []
        assert [r["did"] for r in await async_search_item_names(f"100%_{tag}", 10, False)] == [base + 4]
        assert len(await async_search_item_names(f"{tag}", 2, False)) == 2  # the limit

    _run(test)


def test_item_id_resolves_to_its_type_on_any_server():
    from services.shard_exchange_db import async_get_did_for_item_id

    server = _server()
    did = 2_000_000 + int(uuid4().hex[:5], 16)

    async def test(pg):
        row = _row(10**9 + int(uuid4().hex[:6], 16), did=did)  # item IDs are unique in the game, so here too
        await _persist(server, [row])
        assert await async_get_did_for_item_id(row.item_id) == did
        assert await async_get_did_for_item_id(row.item_id + 10**12) is None

    _run(test)


def test_price_history_per_scan_and_per_day():
    from services.shard_exchange_db import async_get_price_history

    server = _server()
    did = 3_000_000 + int(uuid4().hex[:5], 16)
    day1 = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 11, 11, 6, 0, tzinfo=timezone.utc)

    async def test(pg):
        # day 1: two scans; day 2: one scan. The ids differ per scan so each scan sees its own listings.
        await _persist(server, [_row(1, did=did, quantity=10, buyout=100, min_bid=40), _row(2, did=did, quantity=10, buyout=300)], finished=day1)
        await _persist(server, [_row(1, did=did, quantity=10, buyout=100, min_bid=40), _row(2, did=did, quantity=10, buyout=500)], finished=day1 + timedelta(hours=1))
        await _persist(server, [_row(1, did=did, quantity=10, buyout=200, min_bid=60)], finished=day2)
        since = datetime(2026, 11, 1, tzinfo=timezone.utc)

        scans = await async_get_price_history(server, did, since, "scan")
        assert [r["time"] for r in scans] == [t.isoformat() for t in (day1, day1 + timedelta(hours=1), day2)]
        assert scans[0]["min_buyout_unit"] == 10 and scans[0]["median_buyout_unit"] == 20 and scans[0]["max_buyout_unit"] == 30
        assert scans[0]["listing_count"] == 2 and scans[0]["unit_count"] == 20
        assert scans[2]["median_buyout_unit"] == 20 and scans[2]["listing_count"] == 1

        days = await async_get_price_history(server, did, since, "day")
        assert [r["time"] for r in days] == ["2026-11-10T00:00:00+00:00", "2026-11-11T00:00:00+00:00"]
        first = days[0]
        assert first["scans"] == 2
        assert first["min_buyout_unit"] == 10  # the lowest of the day
        assert first["max_buyout_unit"] == 50  # the highest of the day
        assert first["median_buyout_unit"] == 25  # median of the scans' medians (20 and 30)
        assert first["min_bid_unit"] == 1  # lowest current price per unit: the 10-shard minimum bid on 10 units
        assert days[1]["scans"] == 1 and days[1]["min_buyout_unit"] == 20

        # a window that starts after the data, another server, another item
        assert await async_get_price_history(server, did, day2 + timedelta(days=1), "scan") == []
        assert await async_get_price_history(server + "x", did, since, "scan") == []
        assert await async_get_price_history(server, did + 1, since, "day") == []

    _run(test)



# ===== browsing =====


def _browse_setup(tag):
    """Item names for one test: (did, name, category, min_level). The tag keeps names and slugs unique."""
    base = 4_000_000 + int(tag[:5], 16) * 10
    return base, [
        (base + 1, f"Alpha Ring {tag}", "jewelry.rings", None),
        (base + 2, f"Alpha Ring {tag}", "jewelry.rings", None),  # a second item type with the same name
        (base + 3, f"Beta's Club {tag}", "weapons.simple.club", 12),
        (base + 4, f"Gamma Potion {tag}", "potions", None),
    ]


def test_item_slugs_come_from_the_name():
    from services.shard_exchange_db import async_upsert_item_names

    did = 5_000_000 + int(uuid4().hex[:5], 16)

    async def test(pg):
        await async_upsert_item_names([(did, "Dragon's Hoard: +2 Ring (Epic)", None, None)])
        row = await _one(pg, "SELECT slug FROM item_names WHERE did = %s", did)
        assert row["slug"] == "dragons-hoard-2-ring-epic"
        await async_upsert_item_names([(did, "  Renamed -- Item  ", None, None)])
        assert (await _one(pg, "SELECT slug FROM item_names WHERE did = %s", did))["slug"] == "renamed-item"

    _run(test)


def test_item_category_and_level_are_kept_when_not_sent():
    from services.shard_exchange_db import async_upsert_item_names

    did = 5_100_000 + int(uuid4().hex[:5], 16)

    async def test(pg):
        assert await async_upsert_item_names([(did, "Kept", "potions", 5)]) == 1
        assert await async_upsert_item_names([(did, "Kept", None, None)]) == 0
        assert await async_upsert_item_names([(did, "Kept", "scrolls", None)]) == 1
        row = await _one(pg, "SELECT category, min_level FROM item_names WHERE did = %s", did)
        assert (row["category"], row["min_level"]) == ("scrolls", 5)

    _run(test)


def test_listings_filter_sort_and_page():
    from services.shard_exchange_db import (
        ListingFilters,
        async_get_category_counts,
        async_search_listings,
        async_upsert_item_names,
    )

    tag = uuid4().hex[:8]
    server = _server()
    base, names = _browse_setup(tag)
    now = datetime.now(timezone.utc)

    async def test(pg):
        await async_upsert_item_names(names)
        rows = [
            _row(1, did=base + 1, buyout=500, min_bid=100, min_level=20, expires=now + timedelta(hours=5)),
            _row(2, did=base + 2, buyout=0, min_bid=50, bid=70, bidder=9, min_level=8, expires=now + timedelta(hours=1)),
            _row(3, did=base + 3, buyout=300, min_bid=10),  # level from the item type: 12
            _row(4, did=base + 4, quantity=10, buyout=200, min_bid=20, expires=now + timedelta(days=2)),
            _row(5, did=base + 4, quantity=2, buyout=100, min_bid=30),
            _row(6, did=base + 9, buyout=50, min_bid=50),  # no name known
        ]
        await _persist(server, rows, finished=now - timedelta(minutes=5))
        async with pg.get_async_dict_cursor(commit=True) as cur:
            # one past its expiry time (not ended yet) and one ended: neither is shown
            await cur.execute(
                "UPDATE shard_exchange_listings SET expires_at = %s WHERE server = %s AND auction_id = 5",
                (now - timedelta(minutes=1), server),
            )
        await _persist(server, rows[:4] + [rows[5]], finished=now - timedelta(minutes=1))
        await _persist(server, rows[:4] + [rows[5], _row(7, did=base + 4, buyout=1)], finished=now)
        async with pg.get_async_dict_cursor(commit=True) as cur:
            await cur.execute(
                "UPDATE shard_exchange_listings SET ended_at = now() WHERE server = %s AND auction_id = 7",
                (server,),
            )

        async def search(sort="name", descending=False, limit=20, offset=0, **f):
            found, total = await async_search_listings(
                server, ListingFilters(words=f.pop("words", []), **f),
                sort=sort, descending=descending, limit=limit, offset=offset,
            )
            return [r["auction_id"] for r in found], total

        # by name (unknown name last), every active listing once
        ids, total = await search()
        assert total == 5 and ids[-1] == 6 and sorted(ids) == [1, 2, 3, 4, 6]
        assert ids[:2] in ([1, 2], [2, 1])  # the two Alpha Rings first

        # words: all must match, any case
        assert (await search(words=["alpha", tag.upper()]))[0] in ([1, 2], [2, 1])
        assert (await search(words=["alpha", "potion"]))[1] == 0

        # a category includes its subcategories
        assert (await search(category="weapons"))[0] == [3]
        assert (await search(category="weapons.simple.club"))[0] == [3]
        assert (await search(category="weapons.simple.dagger"))[1] == 0
        assert sorted((await search(category="jewelry"))[0]) == [1, 2]

        # one item by slug: both item types named Alpha Ring
        assert sorted((await search(item_slug=f"alpha-ring-{tag}"))[0]) == [1, 2]

        # level: the listing's own, else the item type's (12 for the club); unknown counts as 1
        assert sorted((await search(min_level=10))[0]) == [1, 3]
        assert sorted((await search(max_level=8))[0]) == [2, 4, 6]
        assert (await search(min_level=10, max_level=15))[0] == [3]

        assert sorted((await search(buyout_only=True))[0]) == [1, 3, 4, 6]

        # buyout: listings without one last, both ways
        assert (await search(sort="buyout"))[0] == [6, 4, 3, 1, 2]
        assert (await search(sort="buyout", descending=True))[0] == [1, 3, 4, 6, 2]
        # current price: the bid if there is one, else the minimum bid (10, 20, 50, 70, 100)
        assert (await search(sort="price"))[0] == [3, 4, 6, 2, 1]
        # per unit: the potions are 200 for 10
        assert (await search(sort="unit_buyout"))[0] == [4, 6, 3, 1, 2]
        # time left: unknown expiry last
        assert (await search(sort="time_left"))[0][:3] == [2, 1, 4]

        # paging
        page2, total = await search(sort="buyout", limit=2, offset=2)
        assert page2 == [3, 1] and total == 5
        past_end, total = await search(limit=2, offset=10)
        assert past_end == [] and total == 5

        counts = await async_get_category_counts(server)
        assert counts == {"jewelry.rings": 2, "weapons.simple.club": 1, "potions": 1, None: 1}

    _run(test)


def test_item_by_slug_and_its_combined_price_history():
    from services.shard_exchange_db import (
        async_get_item_by_slug,
        async_get_price_history_by_slug,
        async_upsert_item_names,
    )

    tag = uuid4().hex[:8]
    server = _server()
    base, names = _browse_setup(tag)
    day = datetime(2026, 11, 10, 6, 0, tzinfo=timezone.utc)

    async def test(pg):
        await async_upsert_item_names(names)
        slug = f"alpha-ring-{tag}"

        item = await async_get_item_by_slug(slug)
        assert (item["name"], item["category"]) == (f"Alpha Ring {tag}", "jewelry.rings")
        assert await async_get_item_by_slug(f"no-such-item-{tag}") is None

        # two item types with that name in one scan: their statistics are combined
        await _persist(
            server,
            [
                _row(1, did=base + 1, buyout=100, min_bid=40),
                _row(2, did=base + 2, buyout=300, min_bid=20),
                _row(3, did=base + 2, buyout=500, min_bid=60),
                _row(4, did=base + 4, buyout=7),  # another item
            ],
            finished=day,
        )
        since = datetime(2026, 11, 1, tzinfo=timezone.utc)
        scans = await async_get_price_history_by_slug(server, slug, since, "scan")
        assert len(scans) == 1
        s = scans[0]
        assert (s["listing_count"], s["buyout_listing_count"]) == (3, 3)
        assert (s["min_buyout_unit"], s["max_buyout_unit"], s["min_bid_unit"]) == (100, 500, 20)
        assert s["median_buyout_unit"] == 250  # the median of the two types' medians, 100 and 400

        days = await async_get_price_history_by_slug(server, slug, since, "day")
        assert [d["time"] for d in days] == ["2026-11-10T00:00:00+00:00"] and days[0]["scans"] == 1
        assert await async_get_price_history_by_slug(server + "x", slug, since, "day") == []

    _run(test)
