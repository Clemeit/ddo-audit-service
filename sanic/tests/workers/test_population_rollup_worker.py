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


def test_refresh_recent_covers_previous_and_current_hour(monkeypatch):
    calls = _record_rollups(monkeypatch)

    worker.refresh_recent(now=datetime(2026, 11, 21, 12, 7, tzinfo=UTC))

    assert calls == [
        (datetime(2026, 11, 21, 11, tzinfo=UTC), datetime(2026, 11, 21, 13, tzinfo=UTC))
    ]
