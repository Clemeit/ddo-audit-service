"""In-tab channel: a short per-user Redis stream the open site polls (GET /v1/user/alerts/inbox)."""

import orjson
from business.alerts.base import AlertChannel, RenderedAlert
from models.redis import RedisKeys

INBOX_MAX_LEN = 100
INBOX_TTL_SECONDS = 24 * 60 * 60
INBOX_FIELD = "alert"


def inbox_key(user_id: int) -> str:
    return RedisKeys.ALERT_INBOX.value.format(user_id=user_id)


class InboxChannel(AlertChannel):
    name = "inbox"

    def deliver(self, client, user_id: int, alert: RenderedAlert) -> bool:
        key = inbox_key(user_id)
        pipe = client.pipeline(transaction=False)
        pipe.xadd(
            key,
            {INBOX_FIELD: orjson.dumps(alert.to_dict())},
            maxlen=INBOX_MAX_LEN,
            approximate=True,
        )
        pipe.expire(key, INBOX_TTL_SECONDS)
        pipe.execute()
        return True
