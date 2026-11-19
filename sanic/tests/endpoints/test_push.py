import inspect
from types import SimpleNamespace

import pytest

import endpoints.push as push_endpoints
from conftest import _amock
from models.push import is_allowed_push_endpoint

VALID = {
    "endpoint": "https://fcm.googleapis.com/fcm/send/abc123",
    "expirationTime": None,
    "keys": {"p256dh": "BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM", "auth": "tBHItJI5svbpez7KI4CCXg"},
}


def _call(handler_name, *args):
    return inspect.unwrap(getattr(push_endpoints, handler_name))(*args)


def _req(make_request, method="POST", user_id=1, json_body=None):
    ctx = SimpleNamespace(user_id=user_id) if user_id else SimpleNamespace()
    request = make_request(method=method, path="/v1/user/push-subscriptions", json_body=json_body, ctx=ctx)
    request.headers = {"User-Agent": "Mozilla/5.0 test"}
    return request


@pytest.fixture
def vapid(monkeypatch):
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "BPublicKey")


# ----- endpoint allowlist -----


@pytest.mark.parametrize(
    "endpoint,allowed",
    [
        ("https://fcm.googleapis.com/fcm/send/abc", True),
        ("https://updates.push.services.mozilla.com/wpush/v2/abc", True),
        ("https://web.push.apple.com/abc", True),
        ("https://wns2-bl2p.notify.windows.com/w/?token=abc", True),
        ("http://fcm.googleapis.com/fcm/send/abc", False),
        ("https://sanic:8000/v1/lfms", False),
        ("https://evil.example/fcm.googleapis.com", False),
        ("https://fcm.googleapis.com.evil.example/x", False),
        ("https://user:pw@fcm.googleapis.com/x", False),
        ("https://fcm.googleapis.com:8443/x", False),
        ("not a url", False),
    ],
)
def test_is_allowed_push_endpoint(endpoint, allowed):
    assert is_allowed_push_endpoint(endpoint) is allowed


# ----- VAPID public key -----


def test_public_key_503_when_not_configured(monkeypatch, make_request, run_async):
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    response = run_async(_call("get_vapid_public_key", make_request()))
    assert response.status == 503


def test_public_key_returned(vapid, make_request, run_async, response_json):
    response = run_async(_call("get_vapid_public_key", make_request()))
    assert response_json(response)["data"]["public_key"] == "BPublicKey"


def test_push_routes_are_jwt_protected_and_key_is_public():
    from utils.route import is_jwt_protected

    assert is_jwt_protected(SimpleNamespace(path="/v1/user/push-subscriptions"))
    assert not is_jwt_protected(SimpleNamespace(path="/v1/push/vapid-public-key"))


# ----- save -----


def test_save_requires_user(vapid, make_request, run_async):
    response = run_async(_call("save_push_subscription", _req(make_request, user_id=None, json_body=VALID)))
    assert response.status == 401


def test_save_upserts_for_user(vapid, monkeypatch, make_request, run_async):
    saved = {}

    def _upsert(user_id, endpoint, p256dh, auth, user_agent):
        saved.update(user_id=user_id, endpoint=endpoint, p256dh=p256dh, auth=auth, user_agent=user_agent)
        return {"id": 1}

    monkeypatch.setattr(push_endpoints.push_db, "async_upsert_subscription", _amock(_upsert))

    response = run_async(_call("save_push_subscription", _req(make_request, user_id=4, json_body=VALID)))

    assert response.status == 201
    assert saved["user_id"] == 4
    assert saved["endpoint"] == VALID["endpoint"]
    assert saved["auth"] == VALID["keys"]["auth"]
    assert saved["user_agent"] == "Mozilla/5.0 test"


@pytest.mark.parametrize(
    "body",
    [
        {**VALID, "endpoint": "https://internal.example/x"},
        {**VALID, "keys": {"p256dh": "short", "auth": "tBHItJI5svbpez7KI4CCXg"}},
        {**VALID, "keys": {"p256dh": VALID["keys"]["p256dh"], "auth": "not base64url!!"}},
        {"endpoint": VALID["endpoint"]},
    ],
)
def test_save_rejects_invalid(vapid, body, make_request, run_async):
    response = run_async(_call("save_push_subscription", _req(make_request, json_body=body)))
    assert response.status == 400


def test_save_503_when_push_not_configured(monkeypatch, make_request, run_async):
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    response = run_async(_call("save_push_subscription", _req(make_request, json_body=VALID)))
    assert response.status == 503


# ----- delete -----


def test_delete_only_own_subscription(monkeypatch, make_request, run_async, response_json):
    seen = {}

    def _delete(user_id, endpoint):
        seen.update(user_id=user_id, endpoint=endpoint)
        return True

    monkeypatch.setattr(push_endpoints.push_db, "async_delete_subscription", _amock(_delete))

    response = run_async(
        _call("delete_push_subscription", _req(make_request, "DELETE", user_id=9, json_body={"endpoint": VALID["endpoint"]}))
    )

    assert response.status == 200
    assert seen == {"user_id": 9, "endpoint": VALID["endpoint"]}
    assert response_json(response)["data"]["deleted"] is True


def test_delete_requires_endpoint(make_request, run_async):
    response = run_async(_call("delete_push_subscription", _req(make_request, "DELETE", json_body={})))
    assert response.status == 400
