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

# Lua script to atomically increment a rate-limit counter and set its TTL on
# the first touch.  Returns [current_count, ttl].  This replaces 2-3
# sequential INCR / TTL / EXPIRE round-trips with a single evalsha call.
_RATE_LIMIT_LUA = """
local key = KEYS[1]
local limit_window = tonumber(ARGV[1])
local count = redis.call('INCR', key)
if count == 1 then
    redis.call('EXPIRE', key, limit_window)
end
local ttl = redis.call('TTL', key)
if ttl < 0 then
    redis.call('EXPIRE', key, limit_window)
    ttl = limit_window
end
return {count, ttl}
"""


async def _async_increment_and_check_limit(
    rate_limit_key: str, limit: int, window: int
):
    """
    Async version: atomically increment the rate limit counter via Lua script.
    Uses EVALSHA with automatic EVAL fallback for efficiency.
    Returns a tuple (allowed: bool, retry_after: int | None).
    """
    try:
        client = await redis_client.get_async_redis_client()
        script = client.register_script(_RATE_LIMIT_LUA)
        result = await script(keys=[rate_limit_key], args=[window])
        current_count = int(result[0])
        ttl = int(result[1])
        if current_count > limit:
            return False, ttl
        return True, ttl
    except RuntimeError:
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
            allowed, retry_after = await _async_increment_and_check_limit(
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
                allowed, retry_after = await _async_increment_and_check_limit(
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
