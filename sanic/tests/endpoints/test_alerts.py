from datetime import datetime, timezone
from types import SimpleNamespace

import orjson
import pytest

import inspect

import endpoints.alerts as alerts_endpoints
from conftest import _amock


def _call(handler_name, *args):
    """Call the undecorated handler: sanic-ext's openapi.body wrapper needs a real sanic Request."""
    return inspect.unwrap(getattr(alerts_endpoints, handler_name))(*args)

CREATED = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)

VALID_BODY = {
    "type": "lfm_posted",
    "criteria": {"quest_ids": [100], "server_names": ["argonnessen"]},
}


def _row(rule_id=1, user_id=1, **overrides):
    row = {
        "id": rule_id,
        "user_id": user_id,
        "type": "lfm_posted",
        "criteria": {"quest_ids": [100], "server_names": ["Argonnessen"]},
        "enabled": True,
        "expires_at": None,
        "created_at": CREATED,
        "updated_at": CREATED,
    }
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def _rules_version(monkeypatch):
    bumps = []

    async def _bump():
        bumps.append(1)

    monkeypatch.setattr(alerts_endpoints, "async_bump_rules_version", _bump)
    return bumps


def _req(make_request, method="GET", path="/v1/user/alerts/rules", user_id=1, json_body=None, args=None):
    ctx = SimpleNamespace(user_id=user_id) if user_id else SimpleNamespace()
    request = make_request(method=method, path=path, json_body=json_body, ctx=ctx)
    request.args = args or {}
    return request


# ----- auth -----


@pytest.mark.parametrize(
    "handler,args",
    [
        ("get_alert_types", ()),
        ("get_alert_rules", ()),
        ("create_alert_rule", ()),
        ("update_alert_rule", (1,)),
        ("delete_alert_rule", (1,)),
        ("get_alert_inbox", ()),
        ("send_test_alert", ()),
    ],
)
def test_every_endpoint_requires_user(handler, args, make_request, run_async, response_json):
    request = _req(make_request, user_id=None, json_body=VALID_BODY)
    response = run_async(_call(handler, request, *args))
    assert response.status == 401


def test_alert_routes_are_jwt_protected():
    from types import SimpleNamespace as NS

    from utils.route import is_jwt_protected

    for path in ["/v1/user/alerts/rules", "/v1/user/alerts/rules/3", "/v1/user/alerts/inbox", "/v1/user/alerts/test"]:
        assert is_jwt_protected(NS(path=path))


# ----- types -----


def test_types_lists_only_user_creatable(make_request, run_async, response_json):
    response = run_async(_call("get_alert_types", _req(make_request)))
    types = [t["type"] for t in response_json(response)["data"]]
    assert "lfm_posted" in types
    assert "test" not in types


# ----- rules -----


def test_list_rules_serialises_rows(monkeypatch, make_request, run_async, response_json):
    seen = {}

    def _list(user_id):
        seen["user_id"] = user_id
        return [_row()]

    monkeypatch.setattr(alerts_endpoints.alerts_db, "async_list_rules", _amock(_list))

    response = run_async(_call("get_alert_rules", _req(make_request, user_id=5)))

    assert response.status == 200
    assert seen["user_id"] == 5
    rule = response_json(response)["data"][0]
    assert rule["created_at"] == CREATED.isoformat()
    assert "user_id" not in rule


def test_create_rule_validates_and_normalises_criteria(
    monkeypatch, make_request, run_async, response_json, _rules_version
):
    captured = {}

    def _create(user_id, rule_type, criteria, enabled, expires_at):
        captured.update(user_id=user_id, type=rule_type, criteria=criteria, enabled=enabled)
        return _row(criteria=criteria)

    monkeypatch.setattr(alerts_endpoints.alerts_db, "async_create_rule", _amock(_create))

    response = run_async(
        _call("create_alert_rule", _req(make_request, "POST", user_id=3, json_body=VALID_BODY))
    )

    assert response.status == 201
    assert _rules_version == [1]
    assert captured["user_id"] == 3
    assert captured["criteria"]["server_names"] == ["Argonnessen"]
    assert captured["criteria"]["difficulties"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"type": "nope", "criteria": {}},
        {"type": "test", "criteria": {}},
        {"type": "lfm_posted", "criteria": {"quest_ids": []}},
        {"type": "lfm_posted", "criteria": {"quest_ids": [1], "server_names": ["Nowhere"]}},
        {"type": "lfm_posted", "criteria": {"quest_ids": [1]}, "extra": True},
    ],
)
def test_create_rule_rejects_invalid_bodies(body, make_request, run_async, response_json):
    response = run_async(_call("create_alert_rule", _req(make_request, "POST", json_body=body)))
    assert response.status == 400
    assert response_json(response)["error"]


def test_create_rule_rejects_large_body(make_request, run_async):
    request = _req(make_request, "POST", json_body=VALID_BODY)
    request.body = b"x" * (9 * 1024)
    response = run_async(_call("create_alert_rule", request))
    assert response.status == 400


def test_create_rule_reports_limit(monkeypatch, make_request, run_async, response_json):
    def _create(*_a):
        raise alerts_endpoints.alerts_db.RuleLimitReached()

    monkeypatch.setattr(alerts_endpoints.alerts_db, "async_create_rule", _amock(_create))

    response = run_async(_call("create_alert_rule", _req(make_request, "POST", json_body=VALID_BODY)))

    assert response.status == 400
    assert "at most" in response_json(response)["error"]


def test_update_rule_404_for_other_users_rule(monkeypatch, make_request, run_async):
    monkeypatch.setattr(alerts_endpoints.alerts_db, "async_get_rule", _amock(lambda user_id, rule_id: None))

    response = run_async(
        _call("update_alert_rule", _req(make_request, "PUT", user_id=2, json_body=VALID_BODY), 1)
    )

    assert response.status == 404


def test_update_rule_cannot_change_type(monkeypatch, make_request, run_async):
    monkeypatch.setattr(
        alerts_endpoints.alerts_db,
        "async_get_rule",
        _amock(lambda user_id, rule_id: _row(type="something_else")),
    )

    response = run_async(_call("update_alert_rule", _req(make_request, "PUT", json_body=VALID_BODY), 1))

    assert response.status == 400


def test_update_rule_saves(monkeypatch, make_request, run_async, response_json):
    monkeypatch.setattr(alerts_endpoints.alerts_db, "async_get_rule", _amock(lambda user_id, rule_id: _row()))
    monkeypatch.setattr(
        alerts_endpoints.alerts_db,
        "async_update_rule",
        _amock(lambda user_id, rule_id, criteria, enabled, expires_at: _row(enabled=enabled)),
    )

    body = {**VALID_BODY, "enabled": False}
    response = run_async(_call("update_alert_rule", _req(make_request, "PUT", json_body=body), 1))

    assert response.status == 200
    assert response_json(response)["data"]["enabled"] is False


def test_delete_rule(monkeypatch, make_request, run_async):
    monkeypatch.setattr(alerts_endpoints.alerts_db, "async_delete_rule", _amock(lambda user_id, rule_id: rule_id == 1))

    ok = run_async(_call("delete_alert_rule", _req(make_request, "DELETE"), 1))
    missing = run_async(_call("delete_alert_rule", _req(make_request, "DELETE"), 2))

    assert ok.status == 200
    assert missing.status == 404


# ----- inbox -----


class FakeAsyncRedis:
    def __init__(self, entries):
        self.entries = entries
        self.calls = []

    async def xrevrange(self, key, count=None):
        self.calls.append(("xrevrange", key))
        return list(reversed(self.entries))[:count]

    async def xrange(self, key, min="-", count=None):
        self.calls.append(("xrange", key, min))
        after = min.lstrip("(")
        return [e for e in self.entries if tuple(map(int, e[0].decode().split("-"))) > tuple(map(int, after.split("-")))][:count]


def _entry(entry_id, title):
    return (entry_id.encode(), {b"alert": orjson.dumps({"title": title})})


def _patch_redis(monkeypatch, entries):
    fake = FakeAsyncRedis(entries)

    async def _client():
        return fake

    monkeypatch.setattr(alerts_endpoints.redis_client, "get_async_redis_client", _client)
    return fake


def test_inbox_without_after_returns_latest_id_only(monkeypatch, make_request, run_async, response_json):
    fake = _patch_redis(monkeypatch, [_entry("1-0", "a"), _entry("2-0", "b")])

    response = run_async(_call("get_alert_inbox", _req(make_request, user_id=9)))

    assert response_json(response)["data"] == {"alerts": [], "latest_id": "2-0"}
    assert fake.calls[0] == ("xrevrange", "alerts:inbox:9")


def test_inbox_empty_stream_starts_at_zero(monkeypatch, make_request, run_async, response_json):
    _patch_redis(monkeypatch, [])
    response = run_async(_call("get_alert_inbox", _req(make_request)))
    assert response_json(response)["data"]["latest_id"] == "0-0"


def test_inbox_after_returns_newer_alerts(monkeypatch, make_request, run_async, response_json):
    _patch_redis(monkeypatch, [_entry("1-0", "a"), _entry("2-0", "b"), _entry("3-0", "c")])

    response = run_async(_call("get_alert_inbox", _req(make_request, args={"after": "1-0"})))

    data = response_json(response)["data"]
    assert [a["title"] for a in data["alerts"]] == ["b", "c"]
    assert data["latest_id"] == "3-0"


def test_inbox_after_with_nothing_new_keeps_cursor(monkeypatch, make_request, run_async, response_json):
    _patch_redis(monkeypatch, [_entry("1-0", "a")])
    response = run_async(_call("get_alert_inbox", _req(make_request, args={"after": "1-0"})))
    assert response_json(response)["data"] == {"alerts": [], "latest_id": "1-0"}


def test_inbox_rejects_bad_after(make_request, run_async):
    response = run_async(_call("get_alert_inbox", _req(make_request, args={"after": "1-0) OR"})))
    assert response.status == 400


# ----- test alert -----


def test_send_test_alert_queues_event_for_user(monkeypatch, make_request, run_async):
    queued = []

    async def _emit(alert_type, data):
        queued.append((alert_type, data))
        return True

    monkeypatch.setattr(alerts_endpoints, "async_emit_alert_event", _emit)

    response = run_async(_call("send_test_alert", _req(make_request, "POST", user_id=4)))

    assert response.status == 202
    assert queued[0][0] == "test"
    assert queued[0][1]["user_id"] == 4
    assert queued[0][1]["nonce"]
