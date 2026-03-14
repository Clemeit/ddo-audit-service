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


async def rate_limit_middleware(request: Request):
    """
    Rate limiting middleware to prevent abuse.

    Implements per-IP rate limiting for auth endpoints (/auth/*)
    Implements per-user rate limiting for authenticated user endpoints (/user/settings/persistent, /user/profile/password)
    """

    path = request.path

    # Check if this is an auth endpoint (should be rate limited by IP)
    if re.match(r"^/v?\d*/auth/", path):
        ip = get_client_ip(request)

        # Create a rate limit key for this IP and endpoint
        rate_limit_key = f"rate_limit:auth:{ip}"

        try:
            current_count = redis_client.get_rate_limit(rate_limit_key)

            if current_count is None:
                # First request in this window
                redis_client.set_rate_limit(
                    rate_limit_key, 1, AUTH_RATE_LIMIT["window"]
                )
            elif current_count >= AUTH_RATE_LIMIT["requests"]:
                # Rate limit exceeded
                return json(
                    {
                        "error": "Rate limit exceeded. Try again in 15 minutes.",
                        "retry_after": AUTH_RATE_LIMIT["window"],
                    },
                    status=429,
                    headers={"Retry-After": str(AUTH_RATE_LIMIT["window"])},
                )
            else:
                # Increment counter
                redis_client.increment_rate_limit(rate_limit_key)
        except Exception as e:
            # Fail open - allow request if Redis is down
            pass

    # Check if this is a protected user endpoint (should be rate limited by user)
    elif re.match(r"^/v?\d*/user/(settings/persistent|profile/password)", path):
        user_id = getattr(request.ctx, "user_id", None)

        if user_id:
            # Create a rate limit key for this user and endpoint
            rate_limit_key = f"rate_limit:user:{user_id}:{path}"

            try:
                current_count = redis_client.get_rate_limit(rate_limit_key)

                if current_count is None:
                    # First request in this window
                    redis_client.set_rate_limit(
                        rate_limit_key, 1, USER_RATE_LIMIT["window"]
                    )
                elif current_count >= USER_RATE_LIMIT["requests"]:
                    # Rate limit exceeded
                    return json(
                        {
                            "error": "Rate limit exceeded. Try again later.",
                            "retry_after": USER_RATE_LIMIT["window"],
                        },
                        status=429,
                        headers={"Retry-After": str(USER_RATE_LIMIT["window"])},
                    )
                else:
                    # Increment counter
                    redis_client.increment_rate_limit(rate_limit_key)
            except Exception as e:
                # Fail open - allow request if Redis is down
                pass
