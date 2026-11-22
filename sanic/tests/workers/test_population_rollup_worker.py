from datetime import datetime, timedelta, timezone

import workers.population_rollup_worker as worker

UTC = timezone.utc


def _record_rollups(monkeypatch, rows=5):
    calls = []

    def fake_rollup(start, end):
        calls.append((start, end))
        return rows

    monkeypatch.setattr(worker.population_db, "rollup_population_hours", fake_rollup)
    return calls


def test_rollup_range_chunks_and_sums_rows(monkeypatch):
    calls = _record_rollups(monkeypatch)
    start = datetime(2026, 1, 1, 0, 30, tzinfo=UTC)
    end = datetime(2026, 1, 1, 5, tzinfo=UTC)

    written = worker.rollup_range(start, end, chunk=timedelta(hours=2))

    assert calls == [
        (datetime(2026, 1, 1, 0, tzinfo=UTC), datetime(2026, 1, 1, 2, tzinfo=UTC)),
        (datetime(2026, 1, 1, 2, tzinfo=UTC), datetime(2026, 1, 1, 4, tzinfo=UTC)),
        (datetime(2026, 1, 1, 4, tzinfo=UTC), end),
    ]
    assert written == 15


def test_catch_up_resumes_from_latest_bucket(monkeypatch):
    calls = _record_rollups(monkeypatch)
    latest = datetime(2026, 11, 21, 10, tzinfo=UTC)
    monkeypatch.setattr(worker.population_db, "get_latest_rollup_bucket", lambda: latest)

    worker.catch_up(now=datetime(2026, 11, 21, 12, 7, tzinfo=UTC))

    assert calls == [(latest, datetime(2026, 11, 21, 13, tzinfo=UTC))]


def test_catch_up_backfills_from_first_snapshot_when_empty(monkeypatch):
    calls = _record_rollups(monkeypatch)
    monkeypatch.setattr(worker.population_db, "get_latest_rollup_bucket", lambda: None)
    monkeypatch.setattr(
        worker.population_db,
        "get_first_game_info_timestamp",
        lambda: datetime(2026, 9, 1, 3, 2, tzinfo=UTC),
    )

    worker.catch_up(now=datetime(2026, 11, 21, 12, 7, tzinfo=UTC))

    assert calls[0][0] == datetime(2026, 9, 1, 3, tzinfo=UTC)
    assert calls[-1][1] == datetime(2026, 11, 21, 13, tzinfo=UTC)
    assert len(calls) == 3  # 31-day chunks over ~81 days


def test_catch_up_noop_when_no_game_info(monkeypatch):
    calls = _record_rollups(monkeypatch)
    monkeypatch.setattr(worker.population_db, "get_latest_rollup_bucket", lambda: None)
    monkeypatch.setattr(worker.population_db, "get_first_game_info_timestamp", lambda: None)

    assert worker.catch_up() == 0
    assert calls == []


def test_refresh_since_steady_state_covers_previous_and_current_hour(monkeypatch):
    calls = _record_rollups(monkeypatch)
    now = datetime(2026, 11, 21, 12, 7, tzinfo=UTC)

    worker.refresh_since(now - timedelta(minutes=10), now)

    assert calls == [
        (datetime(2026, 11, 21, 11, tzinfo=UTC), datetime(2026, 11, 21, 13, tzinfo=UTC))
    ]


def test_refresh_since_backfills_hours_missed_during_an_outage(monkeypatch):
    calls = _record_rollups(monkeypatch)
    now = datetime(2026, 11, 21, 12, 7, tzinfo=UTC)
    last_success = datetime(2026, 11, 21, 7, 55, tzinfo=UTC)

    worker.refresh_since(last_success, now)

    assert calls == [
        (datetime(2026, 11, 21, 7, tzinfo=UTC), datetime(2026, 11, 21, 13, tzinfo=UTC))
    ]


def _h(day, hour):
    return datetime(2026, 11, day, hour, tzinfo=UTC)


def test_hour_ranges_collapses_contiguous_hours():
    hours = [_h(21, 1), _h(21, 2), _h(21, 3), _h(21, 7), _h(21, 9), _h(21, 10)]
    assert worker.hour_ranges(hours) == [
        (_h(21, 1), _h(21, 4)),
        (_h(21, 7), _h(21, 8)),
        (_h(21, 9), _h(21, 11)),
    ]


def test_repair_gaps_rolls_up_each_missing_range(monkeypatch):
    calls = _record_rollups(monkeypatch)
    seen = {}

    def fake_missing(end):
        seen["end"] = end
        return [_h(20, 3), _h(20, 4), _h(20, 10)]

    monkeypatch.setattr(worker.population_db, "get_missing_rollup_hours", fake_missing)

    written = worker.repair_gaps(now=datetime(2026, 11, 21, 12, 7, tzinfo=UTC))

    assert seen["end"] == _h(21, 12)
    assert calls == [(_h(20, 3), _h(20, 5)), (_h(20, 10), _h(20, 11))]
    assert written == 10


def test_repair_gaps_noop_without_missing_hours(monkeypatch):
    calls = _record_rollups(monkeypatch)
    monkeypatch.setattr(worker.population_db, "get_missing_rollup_hours", lambda end: [])

    assert worker.repair_gaps() == 0
    assert calls == []


def test_sync_catches_up_then_repairs(monkeypatch):
    order = []

    def fake_catch_up(now):
        order.append("catch_up")
        return 3

    def fake_repair(now):
        order.append("repair")
        return 4

    monkeypatch.setattr(worker, "catch_up", fake_catch_up)
    monkeypatch.setattr(worker, "repair_gaps", fake_repair)

    assert worker.sync(now=datetime(2026, 11, 21, tzinfo=UTC)) == 7
    assert order == ["catch_up", "repair"]
