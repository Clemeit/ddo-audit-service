"""
Rate limiting middleware for protecting endpoints from abuse.

Implements per-IP rate limiting for authentication endpoints
and per-user rate limiting for authenticated endpoints.
"""

from sanic import Request
from sanic.response import json
import services.redis as redis_client
from utils.access_log import get_client_ip
import re


# Rate limit configurations (in seconds and requests)
AUTH_RATE_LIMIT = {
    "requests": 5,
    "window": 900,  # 15 minutes
}

USER_RATE_LIMIT = {
    "requests": 10,
    "window": 3600,  # 1 hour
}

AUTH_ENDPOINT_PATTERN = re.compile(r"^/v?\d*/auth/(register|login|refresh)$")
USER_ENDPOINT_PATTERN = re.compile(
    r"^/v?\d*/(user/(settings/persistent|profile/password)|auth/logout)$"
)


def _increment_and_check_limit(rate_limit_key: str, limit: int, window: int):
    """
    Atomically increment the rate limit counter and determine if the request is allowed.
    Returns a tuple (allowed: bool, retry_after: int | None), where retry_after is
    the remaining TTL in seconds for this rate limit window.
    """
    try:
        with redis_client.get_redis_client() as client:
            # INCR is atomic in Redis and will create the key with value 1 if it does not exist.
            current_count = client.incr(rate_limit_key)
            # Ensure the key has a TTL corresponding to the rate limit window.
            ttl = client.ttl(rate_limit_key)
            if current_count == 1 or ttl is None or ttl < 0:
                client.expire(rate_limit_key, window)
                ttl = window
            # If the incremented count exceeds the allowed limit, the request is not allowed.
            if current_count > limit:
                return False, ttl
            return True, ttl
    except RuntimeError:
        # Redis not initialized — fail open
        return True, None


async def rate_limit_middleware(request: Request):
    """
    Rate limiting middleware to prevent abuse.

    Implements per-IP rate limiting for auth endpoints (/auth/*)
    Implements per-user rate limiting for authenticated user endpoints (/user/settings/persistent, /user/profile/password)
    """

    path = request.path

    # Check if this is an auth endpoint (should be rate limited by IP)
    if AUTH_ENDPOINT_PATTERN.match(path):
        ip = get_client_ip(request)

        # Create a rate limit key for this IP and specific auth endpoint path
        rate_limit_key = f"rate_limit:auth:{ip}:{path}"

        try:
            allowed, retry_after = _increment_and_check_limit(
                rate_limit_key,
                AUTH_RATE_LIMIT["requests"],
                AUTH_RATE_LIMIT["window"],
            )

            if not allowed:
                # Rate limit exceeded
                return json(
                    {
                        "error": "Rate limit exceeded. Try again in 15 minutes.",
                        "retry_after": retry_after,
                    },
                    status=429,
                    headers={
                        "Retry-After": (
                            str(retry_after)
                            if retry_after is not None
                            else str(AUTH_RATE_LIMIT["window"])
                        )
                    },
                )
        except Exception as e:
            # Fail open - allow request if Redis is down
            pass

    # Check if this is a protected user endpoint (should be rate limited by user)
    elif USER_ENDPOINT_PATTERN.match(path):
        user_id = getattr(request.ctx, "user_id", None)

        if user_id:
            # Normalize endpoint path to be version-independent for rate limiting
            endpoint_match = USER_ENDPOINT_PATTERN.match(path)
            endpoint_identifier = (
                endpoint_match.group(1).replace("/", ":")
                if endpoint_match
                else "unknown-endpoint"
            )
            # Create a rate limit key for this user and logical endpoint
            rate_limit_key = f"rate_limit:user:{user_id}:{endpoint_identifier}"

            try:
                allowed, retry_after = _increment_and_check_limit(
                    rate_limit_key,
                    USER_RATE_LIMIT["requests"],
                    USER_RATE_LIMIT["window"],
                )

                if not allowed:
                    # Rate limit exceeded
                    return json(
                        {
                            "error": "Rate limit exceeded. Try again later.",
                            "retry_after": retry_after,
                        },
                        status=429,
                        headers={
                            "Retry-After": (
                                str(retry_after)
                                if retry_after is not None
                                else str(USER_RATE_LIMIT["window"])
                            )
                        },
                    )
            except Exception as e:
                # Fail open - allow request if Redis is down
                pass
