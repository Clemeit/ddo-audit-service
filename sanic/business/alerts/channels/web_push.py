"""Web Push channel: sends each alert to every browser the user turned push notifications on for.

Uses VAPID (no vendor account): the browser picks the push service (FCM for Chrome/Edge, Mozilla for
Firefox, Apple for Safari) and we POST the encrypted payload to the subscription's endpoint.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import orjson
from business.alerts.base import AlertChannel, RenderedAlert
from models.push import is_allowed_push_endpoint

logger = logging.getLogger(__name__)

# Drop the push if the device stays offline this long: a "quest posted" alert an hour late is noise.
PUSH_TTL_SECONDS = 3 * 60
# Push services keep only the newest undelivered message per topic, so a device that was offline (e.g. the
# browser was closed) gets at most one alert when it comes back instead of a backlog. Delivery to an online
# device is unaffected. Topics are at most 32 URL-safe base64 characters.
PUSH_TOPIC = "alerts"
PUSH_HEADERS = {"Urgency": "high", "Topic": PUSH_TOPIC}
PUSH_TIMEOUT_SECONDS = 10
# Consecutive failures (other than "gone") before a subscription is dropped.
MAX_CONSECUTIVE_FAILURES = 10
GONE_STATUSES = (404, 410)


def _default_send(**kwargs):
    from pywebpush import webpush  # imported lazily so the module loads without the dependency

    return webpush(**kwargs)


def _status_of(error: Exception) -> int | None:
    response = getattr(error, "response", None)
    return getattr(response, "status_code", None)


def build_payload(alert: RenderedAlert) -> bytes:
    """What the service worker's push handler reads. Well under the ~4 KB push payload limit."""
    return orjson.dumps(
        {
            "type": alert.type,
            "title": alert.title[:200],
            "body": alert.body[:500],
            "url": alert.url,
            "tag": alert.tag,
            # Lets the service worker say how old a push is if it arrives late.
            "created_at": alert.created_at,
        }
    )


class WebPushChannel(AlertChannel):
    name = "web_push"

    def __init__(
        self,
        vapid_private_key: str,
        vapid_subject: str,
        *,
        load_subscriptions: Callable[[int], list[dict]] | None = None,
        delete_subscription: Callable[[int], None] | None = None,
        mark_success: Callable[[int], None] | None = None,
        record_failure: Callable[[int], int] | None = None,
        send: Callable[..., Any] = _default_send,
    ) -> None:
        if load_subscriptions is None:
            import services.push_db as push_db

            load_subscriptions = push_db.get_subscriptions_for_user
            delete_subscription = push_db.delete_subscription
            mark_success = push_db.mark_subscription_success
            record_failure = push_db.record_subscription_failure
        self._vapid_private_key = vapid_private_key
        self._vapid_subject = vapid_subject
        self._load = load_subscriptions
        self._delete = delete_subscription
        self._mark_success = mark_success
        self._record_failure = record_failure
        self._send = send

    def deliver(self, client, user_id: int, alert: RenderedAlert) -> bool:
        subscriptions = self._load(user_id)
        if not subscriptions:
            return False
        payload = build_payload(alert)
        sent = False
        for sub in subscriptions:
            if self._send_one(sub, payload):
                sent = True
        return sent

    def _send_one(self, sub: dict, payload: bytes) -> bool:
        sub_id = sub["id"]
        if not is_allowed_push_endpoint(sub["endpoint"]):
            # Validated on the way in; re-checked so a bad row can never be used.
            logger.warning(f"Deleting push subscription {sub_id} with a disallowed endpoint")
            self._delete(sub_id)
            return False
        try:
            self._send(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                data=payload,
                vapid_private_key=self._vapid_private_key,
                # pywebpush adds "aud" and "exp" to the claims dict, so give it a fresh one each time.
                vapid_claims={"sub": self._vapid_subject},
                ttl=PUSH_TTL_SECONDS,
                headers=dict(PUSH_HEADERS),
                timeout=PUSH_TIMEOUT_SECONDS,
            )
        except Exception as e:
            status = _status_of(e)
            if status in GONE_STATUSES:
                self._delete(sub_id)
                logger.info(f"Push subscription {sub_id} is gone ({status}); deleted")
            else:
                failures = self._record_failure(sub_id)
                logger.warning(
                    f"Push to subscription {sub_id} failed ({status or type(e).__name__}); "
                    f"{failures} consecutive failure(s)"
                )
                if failures >= MAX_CONSECUTIVE_FAILURES:
                    self._delete(sub_id)
            return False
        self._mark_success(sub_id)
        return True
