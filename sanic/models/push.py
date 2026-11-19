"""Request bodies for /v1/user/push-subscriptions.

The dispatch worker POSTs to the subscription's endpoint, so endpoints are restricted to HTTPS URLs on
the browsers' push services. Without that, anyone could make the worker send requests to arbitrary
(including internal) addresses.
"""

import re
from typing import Optional
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Hosts of the push services used by current browsers (Chrome/Edge/Opera/Brave/Samsung via FCM,
# Firefox via Mozilla autopush, Safari via Apple, legacy Edge via WNS).
ALLOWED_PUSH_HOST_SUFFIXES: tuple[str, ...] = (
    ".googleapis.com",
    ".push.services.mozilla.com",
    ".push.apple.com",
    ".notify.windows.com",
)
MAX_ENDPOINT_LENGTH = 2048
_BASE64URL = re.compile(r"^[A-Za-z0-9_\-]+={0,2}$")


def is_allowed_push_endpoint(endpoint: str) -> bool:
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not host or parts.username or parts.password:
        return False
    if parts.port not in (None, 443):
        return False
    return any(host.endswith(suffix) for suffix in ALLOWED_PUSH_HOST_SUFFIXES)


class PushSubscriptionKeys(BaseModel):
    model_config = ConfigDict(extra="ignore")

    p256dh: str = Field(min_length=16, max_length=256)
    auth: str = Field(min_length=8, max_length=64)

    @field_validator("p256dh", "auth")
    @classmethod
    def _base64url(cls, value: str) -> str:
        if not _BASE64URL.match(value):
            raise ValueError("must be base64url")
        return value


class PushSubscriptionInput(BaseModel):
    """The shape of the browser's ``PushSubscription.toJSON()``."""

    model_config = ConfigDict(extra="ignore")

    endpoint: str = Field(min_length=1, max_length=MAX_ENDPOINT_LENGTH)
    keys: PushSubscriptionKeys
    expirationTime: Optional[float] = None

    @field_validator("endpoint")
    @classmethod
    def _known_push_service(cls, value: str) -> str:
        if not is_allowed_push_endpoint(value):
            raise ValueError("not a supported push service")
        return value


class PushSubscriptionDelete(BaseModel):
    model_config = ConfigDict(extra="ignore")

    endpoint: str = Field(min_length=1, max_length=MAX_ENDPOINT_LENGTH)
