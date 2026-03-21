"""Shared cookie helpers for auth-related endpoints."""

import logging
import os

import services.auth as auth_service


logger = logging.getLogger(__name__)

# Cookie configuration — override via environment for each deployment tier.
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"

_SAME_SITE_ALLOWED = {"Lax", "Strict", "None"}
_raw_same_site = os.getenv("COOKIE_SAME_SITE", "Lax")
COOKIE_SAME_SITE = _raw_same_site if _raw_same_site in _SAME_SITE_ALLOWED else "Lax"
if COOKIE_SAME_SITE != _raw_same_site:
    logger.warning(
        "Invalid COOKIE_SAME_SITE value %r; falling back to 'Lax'", _raw_same_site
    )

# SameSite=None requires Secure=True per the cookies spec.
if COOKIE_SAME_SITE == "None":
    COOKIE_SECURE = True

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/v1/auth"


def set_refresh_cookie(response, token: str) -> None:
    """Attach an HttpOnly refresh-token cookie to the response."""
    response.add_cookie(
        REFRESH_COOKIE_NAME,
        token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite=COOKIE_SAME_SITE,
        path=REFRESH_COOKIE_PATH,
        max_age=auth_service.REFRESH_TOKEN_EXPIRATION_SECONDS,
    )
    logger.debug(
        "Refresh cookie set (path=%s, secure=%s)", REFRESH_COOKIE_PATH, COOKIE_SECURE
    )


def clear_refresh_cookie(response) -> None:
    """Expire and clear the refresh-token cookie from the client."""
    response.delete_cookie(REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)
    logger.debug("Refresh cookie cleared")
