from datetime import datetime, timedelta, timezone

import orjson
import pytest

import business.alerts  # noqa: F401  (registers the alert types)
from business.alerts import registry
from business.alerts.base import AlertChannel
from business.alerts.channels.inbox import INBOX_FIELD, InboxChannel, inbox_key
from business.alerts.dispatcher import (
    USER_CAP,
    AlertDispatcher,
    parse_rule,
    record_metrics,
)

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.ops = []

    def __getattr__(self, name):
        def queue(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self

        return queue

    def execute(self):
        return [getattr(self.redis, name)(*a, **kw) for name, a, kw in self.ops]


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.streams = {}
        self.hashes = {}
        self.expiries = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return None
        self.values[key] = value
        if ex:
            self.expiries[key] = ex
        return True

    def incr(self, key):
        self.values[key] = int(self.values.get(key, 0)) + 1
        return self.values[key]

    def expire(self, key, seconds):
        self.expiries[key] = seconds
        return True

    def xadd(self, key, fields, maxlen=None, approximate=False):
        self.streams.setdefault(key, []).append(fields)
        return f"{len(self.streams[key])}-0"

    def hincrby(self, key, field, amount):
        h = self.hashes.setdefault(key, {})
        h[field] = h.get(field, 0) + amount
        return h[field]

    def pipeline(self, transaction=True):
        return FakePipeline(self)


class RecordingChannel(AlertChannel):
    name = "recording"

    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def deliver(self, client, user_id, alert):
        if self.fail:
            raise RuntimeError("boom")
        self.sent.append((user_id, alert))
        return True


def _row(rule_id, user_id, quest_ids=(100,), expires_at=None, **criteria):
    return {
        "id": rule_id,
        "user_id": user_id,
        "type": "lfm_posted",
        "criteria": {"quest_ids": list(quest_ids), **criteria},
        "expires_at": expires_at,
    }


def _event(**data):
    payload = {
        "server_name": "argonnessen",
        "lfm_id": 10,
        "quest_id": 100,
        "difficulty": "Elite",
        "minimum_level": 20,
        "maximum_level": 24,
        "leader_name": "Leader",
        "member_count": 2,
    }
    payload.update(data)
    return {"type": "lfm_posted", "occurred_at": NOW.isoformat(), "data": payload}


@pytest.fixture(autouse=True)
def _quest_names(monkeypatch):
    monkeypatch.setattr(
        registry.get_type("lfm_posted"), "_quest_name_loader", lambda: {100: "The Pit"}
    )


def _dispatcher(rows, channels):
    d = AlertDispatcher(
        load_rules=lambda t: rows if t == "lfm_posted" else [],
        channels=channels,
        now=lambda: NOW,
    )
    d.refresh()
    return d


def test_parse_rule_skips_unknown_type_and_invalid_criteria():
    assert parse_rule({**_row(1, 1), "type": "nope"}) is None
    assert parse_rule({**_row(1, 1), "criteria": {"quest_ids": []}}) is None
    rule = parse_rule(_row(1, 1))
    assert rule.criteria.quest_ids == [100]


def test_match_delivers_one_alert_per_user():
    channel = RecordingChannel()
    d = _dispatcher([_row(1, 7), _row(2, 7), _row(3, 8)], [channel])

    stats = d.process_event(FakeRedis(), _event())

    assert stats["matched"] == 2
    assert stats["delivered_recording"] == 2
    assert sorted(uid for uid, _ in channel.sent) == [7, 8]
    assert channel.sent[0][1].title == "The Pit on Argonnessen"


def test_same_event_is_deduped_per_user():
    channel = RecordingChannel()
    redis = FakeRedis()
    d = _dispatcher([_row(1, 7)], [channel])

    d.process_event(redis, _event())
    stats = d.process_event(redis, _event())

    assert stats["deduped"] == 1
    assert len(channel.sent) == 1


def test_quest_change_on_same_lfm_is_a_new_alert():
    channel = RecordingChannel()
    redis = FakeRedis()
    d = _dispatcher([_row(1, 7, quest_ids=(100, 200))], [channel])

    d.process_event(redis, _event())
    d.process_event(redis, _event(quest_id=200))

    assert len(channel.sent) == 2


def test_user_cap_limits_alerts():
    channel = RecordingChannel()
    redis = FakeRedis()
    d = _dispatcher([_row(1, 7)], [channel])

    for lfm_id in range(USER_CAP + 3):
        d.process_event(redis, _event(lfm_id=lfm_id))

    assert len(channel.sent) == USER_CAP


def test_expired_rules_are_ignored():
    channel = RecordingChannel()
    d = _dispatcher(
        [_row(1, 7, expires_at=NOW - timedelta(minutes=1)), _row(2, 8, expires_at=NOW + timedelta(hours=1))],
        [channel],
    )

    d.process_event(FakeRedis(), _event())

    assert [uid for uid, _ in channel.sent] == [8]


def test_non_matching_criteria_do_not_deliver():
    channel = RecordingChannel()
    d = _dispatcher([_row(1, 7, server_names=["Cannith"])], [channel])

    stats = d.process_event(FakeRedis(), _event())

    assert stats["matched"] == 0
    assert channel.sent == []


def test_failing_channel_does_not_stop_other_channels():
    good = RecordingChannel()
    bad = RecordingChannel(fail=True)
    bad.name = "bad"
    d = _dispatcher([_row(1, 7)], [bad, good])

    stats = d.process_event(FakeRedis(), _event())

    assert stats["failed_bad"] == 1
    assert len(good.sent) == 1


def test_unknown_type_and_malformed_events_are_counted():
    d = _dispatcher([], [])
    assert d.process_event(FakeRedis(), {"type": "nope", "data": {}})["unknown_type"] == 1
    assert d.process_raw(FakeRedis(), b"not json")["malformed"] == 1


def test_test_event_reaches_only_that_user():
    channel = RecordingChannel()
    d = _dispatcher([_row(1, 7)], [channel])

    stats = d.process_event(FakeRedis(), {"type": "test", "data": {"user_id": 42, "nonce": "abc"}})

    assert stats["delivered_recording"] == 1
    assert channel.sent[0][0] == 42
    assert channel.sent[0][1].title == "Test alert"


def test_refresh_keeps_previous_index_when_loading_fails():
    channel = RecordingChannel()
    rows = [_row(1, 7)]
    calls = {"n": 0}

    def loader(alert_type):
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("db down")
        return rows

    d = AlertDispatcher(load_rules=loader, channels=[channel], now=lambda: NOW)
    d.refresh()
    d.refresh()
    d.process_event(FakeRedis(), _event())

    assert len(channel.sent) == 1


def test_refresh_if_due_respects_interval():
    clock = {"t": 0.0}
    loads = []
    d = AlertDispatcher(
        load_rules=lambda t: loads.append(t) or [],
        channels=[],
        refresh_seconds=30,
        clock=lambda: clock["t"],
        now=lambda: NOW,
    )
    d.refresh_if_due()
    clock["t"] = 10
    d.refresh_if_due()
    clock["t"] = 31
    d.refresh_if_due()

    assert loads.count("lfm_posted") == 2


def test_inbox_channel_appends_to_user_stream():
    redis = FakeRedis()
    d = _dispatcher([_row(1, 7)], [InboxChannel()])

    d.process_event(redis, _event())

    entries = redis.streams[inbox_key(7)]
    assert len(entries) == 1
    alert = orjson.loads(entries[0][INBOX_FIELD])
    assert alert["tag"] == "lfm-argonnessen-10"
    assert alert["url"] == "/grouping/argonnessen"
    assert redis.expiries[inbox_key(7)] == 24 * 60 * 60


def test_record_metrics_increments_hash():
    redis = FakeRedis()
    from collections import Counter

    record_metrics(redis, Counter(events=1, matched=2))
    record_metrics(redis, Counter(events=1))

    assert redis.hashes["alerts:metrics"] == {"events": 2, "matched": 2}


def test_refresh_if_due_reloads_when_rules_version_changes():
    clock = {"t": 0.0}
    version = {"v": b"1"}
    loads = []
    d = AlertDispatcher(
        load_rules=lambda t: loads.append(t) or [],
        channels=[],
        refresh_seconds=30,
        clock=lambda: clock["t"],
        now=lambda: NOW,
        rules_version=lambda: version["v"],
    )
    d.refresh_if_due()
    clock["t"] = 1
    d.refresh_if_due()  # same version, interval not reached
    version["v"] = b"2"
    clock["t"] = 2
    d.refresh_if_due()  # version bumped by the API

    assert loads.count("lfm_posted") == 2


def test_refresh_if_due_tolerates_version_read_errors():
    def broken():
        raise RuntimeError("redis down")

    d = AlertDispatcher(load_rules=lambda t: [], channels=[], now=lambda: NOW, rules_version=broken)
    d.refresh_if_due()
    d.refresh_if_due()


def test_dedupe_marker_uses_the_types_ttl():
    from business.alerts.types.server_status import ServerStatusAlertType

    server_status = registry.get_type("server_status")
    rule_row = {
        "id": 1,
        "user_id": 1,
        "type": "server_status",
        "criteria": {},
        "expires_at": None,
    }
    dispatcher = AlertDispatcher(
        load_rules=lambda name: [rule_row] if name == "server_status" else [],
        channels=[],
        now=lambda: NOW,
    )
    dispatcher.refresh()
    redis = FakeRedis()
    dispatcher.process_event(
        redis,
        {"type": "server_status", "data": {"server_name": "thrane", "is_online": False}},
    )
    key = "alerts:sent:1:server:thrane:offline"
    assert redis.expiries[key] == ServerStatusAlertType.dedupe_ttl_secs == server_status.dedupe_ttl_secs
