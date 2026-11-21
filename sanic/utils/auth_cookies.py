"""Shared cookie and origin helpers for the cookie-authenticated auth endpoints."""

import logging
import os
from typing import Optional

import services.auth as auth_service


logger = logging.getLogger(__name__)

# Cookie configuration — override via environment for each deployment tier.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"

_SAME_SITE_CANONICAL = {
    "lax": "Lax",
    "strict": "Strict",
    "none": "None",
}
# Strict is safe here: the frontend and API are same-site (www./api.), and the cookie is
# only ever sent by fetch/XHR to /v1/auth, never on a top-level navigation.
_raw_same_site = os.getenv("COOKIE_SAME_SITE", "Strict")
_normalized_same_site = _raw_same_site.strip().lower()
COOKIE_SAME_SITE = _SAME_SITE_CANONICAL.get(_normalized_same_site, "Strict")
if _normalized_same_site not in _SAME_SITE_CANONICAL:
    logger.warning(
        "Invalid COOKIE_SAME_SITE value %r; falling back to 'Strict'", _raw_same_site
    )

# SameSite=None requires Secure=True per the cookies spec.
if COOKIE_SAME_SITE == "None":
    COOKIE_SECURE = True

# The browser only accepts a "__Secure-" cookie over HTTPS with the Secure flag, which
# also stops an insecure origin from planting or overwriting it.
_BASE_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_NAME = (
    f"__Secure-{_BASE_COOKIE_NAME}" if COOKIE_SECURE else _BASE_COOKIE_NAME
)
# Cookie name used before the __Secure- prefix. Still read (and cleared) so existing
# sessions survive the rename; remove once every legacy cookie has expired.
LEGACY_REFRESH_COOKIE_NAME = _BASE_COOKIE_NAME
REFRESH_COOKIE_PATH = "/v1/auth"

# Frontend origins allowed to call the cookie-authenticated endpoints. Mirrors the
# credentialed origins in nginx/nginx.conf ($cors_credentials).
AUTH_ALLOWED_ORIGINS = frozenset(
    origin.strip().rstrip("/")
    for origin in os.getenv(
        "AUTH_ALLOWED_ORIGINS", "https://www.ddoaudit.com,http://www.localtest.me"
    ).split(",")
    if origin.strip()
)


def _write_cookie(response, name: str, value: str, max_age: int) -> None:
    response.add_cookie(
        name.removeprefix("__Secure-"),
        value,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAME_SITE,
        path=REFRESH_COOKIE_PATH,
        max_age=max_age,
        secure_prefix=name.startswith("__Secure-"),
    )


def get_refresh_cookie(request) -> Optional[str]:
    """Return the refresh token from the request cookies (current name, then legacy)."""
    cookies = request.cookies or {}
    for name in (REFRESH_COOKIE_NAME, LEGACY_REFRESH_COOKIE_NAME):
        value = cookies.get(name)
        if value:
            return value
    return None


def set_refresh_cookie(response, token: str, max_age: Optional[int] = None) -> None:
    """Attach an HttpOnly refresh-token cookie to the response."""
    if max_age is None:
        max_age = auth_service.REFRESH_TOKEN_EXPIRATION_SECONDS
    _write_cookie(response, REFRESH_COOKIE_NAME, token, max(1, int(max_age)))
    if LEGACY_REFRESH_COOKIE_NAME != REFRESH_COOKIE_NAME:
        _write_cookie(response, LEGACY_REFRESH_COOKIE_NAME, "", 0)
    logger.debug(
        "Refresh cookie set (path=%s, secure=%s)", REFRESH_COOKIE_PATH, COOKIE_SECURE
    )


def clear_refresh_cookie(response) -> None:
    """Expire the refresh-token cookie (current and legacy names) on the client.

    Written with the same attributes it was set with: Sanic's delete_cookie always marks
    the expiry Secure, which a plain-HTTP dev origin would silently ignore.
    """
    _write_cookie(response, REFRESH_COOKIE_NAME, "", 0)
    if LEGACY_REFRESH_COOKIE_NAME != REFRESH_COOKIE_NAME:
        _write_cookie(response, LEGACY_REFRESH_COOKIE_NAME, "", 0)
    logger.debug("Refresh cookie cleared")


def is_trusted_origin(request) -> bool:
    """CSRF defence for cookie-authenticated endpoints.

    Browsers send Origin on every cross-origin POST, so a request from another site
    (including a sibling subdomain) carries a foreign Origin and is refused. Requests
    without Origin come from non-browser clients, which can't ride a victim's cookie.
    """
    origin = (request.headers.get("Origin") or "").strip().rstrip("/")
    if not origin:
        return True
    return origin in AUTH_ALLOWED_ORIGINS
