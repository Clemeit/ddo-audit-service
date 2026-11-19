import orjson
import pytest

from business.alerts.base import RenderedAlert
from business.alerts.channels.web_push import (
    MAX_CONSECUTIVE_FAILURES,
    PUSH_TTL_SECONDS,
    WebPushChannel,
    build_payload,
)

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc"


class PushError(Exception):
    def __init__(self, status):
        super().__init__(f"push failed: {status}")
        self.response = type("Response", (), {"status_code": status})()


class FakeStore:
    def __init__(self, subs):
        self.subs = {s["id"]: dict(s) for s in subs}
        self.deleted = []
        self.successes = []
        self.failures = {}

    def load(self, user_id):
        return [s for s in self.subs.values() if s["user_id"] == user_id]

    def delete(self, sub_id):
        self.deleted.append(sub_id)
        self.subs.pop(sub_id, None)

    def mark_success(self, sub_id):
        self.successes.append(sub_id)
        self.failures[sub_id] = 0

    def record_failure(self, sub_id):
        self.failures[sub_id] = self.failures.get(sub_id, 0) + 1
        return self.failures[sub_id]


def _sub(sub_id, user_id=7, endpoint=ENDPOINT):
    return {"id": sub_id, "user_id": user_id, "endpoint": endpoint, "p256dh": "key", "auth": "auth"}


def _alert():
    return RenderedAlert(
        type="lfm_posted",
        rule_id=1,
        title="The Pit on Argonnessen",
        body="Leader · Level 20-24",
        url="/grouping/argonnessen",
        tag="lfm-argonnessen-10",
    )


def _channel(store, send):
    return WebPushChannel(
        "private-key",
        "mailto:test@example.com",
        load_subscriptions=store.load,
        delete_subscription=store.delete,
        mark_success=store.mark_success,
        record_failure=store.record_failure,
        send=send,
    )


def test_sends_to_every_subscription_with_vapid_and_ttl():
    store = FakeStore([_sub(1), _sub(2, endpoint="https://updates.push.services.mozilla.com/wpush/v2/x"), _sub(3, user_id=8)])
    calls = []
    channel = _channel(store, lambda **kw: calls.append(kw))

    assert channel.deliver(None, 7, _alert()) is True

    assert len(calls) == 2
    first = calls[0]
    assert first["subscription_info"] == {"endpoint": ENDPOINT, "keys": {"p256dh": "key", "auth": "auth"}}
    assert first["vapid_private_key"] == "private-key"
    assert first["vapid_claims"] == {"sub": "mailto:test@example.com"}
    assert first["ttl"] == PUSH_TTL_SECONDS
    assert first["headers"] == {"Urgency": "high", "Topic": "alerts"}
    assert orjson.loads(first["data"])["url"] == "/grouping/argonnessen"
    assert store.successes == [1, 2]


def test_each_send_gets_its_own_claims_dict():
    store = FakeStore([_sub(1), _sub(2)])
    claims = []

    def send(**kw):
        kw["vapid_claims"]["exp"] = 123  # pywebpush mutates the dict
        claims.append(kw["vapid_claims"])

    _channel(store, send).deliver(None, 7, _alert())

    assert claims[0] is not claims[1]


def test_no_subscriptions_means_nothing_sent():
    store = FakeStore([])
    assert _channel(store, lambda **kw: pytest.fail("should not send")).deliver(None, 7, _alert()) is False


@pytest.mark.parametrize("status", [404, 410])
def test_gone_subscription_is_deleted(status):
    store = FakeStore([_sub(1)])

    def send(**kw):
        raise PushError(status)

    assert _channel(store, send).deliver(None, 7, _alert()) is False
    assert store.deleted == [1]


def test_other_failures_are_counted_then_deleted():
    store = FakeStore([_sub(1)])

    def send(**kw):
        raise PushError(500)

    channel = _channel(store, send)
    for _ in range(MAX_CONSECUTIVE_FAILURES - 1):
        channel.deliver(None, 7, _alert())
    assert store.deleted == []
    channel.deliver(None, 7, _alert())
    assert store.deleted == [1]


def test_network_errors_without_response_are_counted():
    store = FakeStore([_sub(1)])

    def send(**kw):
        raise ConnectionError("down")

    _channel(store, send).deliver(None, 7, _alert())
    assert store.failures[1] == 1
    assert store.deleted == []


def test_disallowed_endpoint_is_never_sent_to():
    store = FakeStore([_sub(1, endpoint="http://sanic:8000/v1/lfms")])

    sent = _channel(store, lambda **kw: pytest.fail("should not send")).deliver(None, 7, _alert())

    assert sent is False
    assert store.deleted == [1]


def test_one_failing_subscription_does_not_block_others():
    store = FakeStore([_sub(1), _sub(2)])

    def send(**kw):
        if kw["subscription_info"]["endpoint"] == ENDPOINT and len(store.failures) == 0:
            raise PushError(410)

    store.subs[2]["endpoint"] = "https://web.push.apple.com/abc"
    assert _channel(store, send).deliver(None, 7, _alert()) is True
    assert store.deleted == [1]
    assert store.successes == [2]


def test_payload_is_small_and_truncated():
    alert = _alert()
    alert.body = "x" * 5000
    payload = orjson.loads(build_payload(alert))
    assert len(payload["body"]) == 500
    assert set(payload) == {"type", "title", "body", "url", "tag", "created_at"}
