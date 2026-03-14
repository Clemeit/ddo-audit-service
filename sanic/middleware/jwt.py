"""
JWT authentication middleware for protecting endpoints.

This middleware validates JWT tokens from the Authorization header
and attaches user information to the request context.
"""

from sanic import Request
from sanic.response import json
import services.auth as auth_service
import re


async def jwt_middleware(request: Request):
    """
    JWT middleware to validate tokens on protected endpoints.

    Attaches user_id and username to request.ctx if token is valid.
    Allows request to proceed if endpoint doesn't require authentication.
    Returns 401 if token is missing or invalid on protected endpoints.
    """

    # Check if this is a protected endpoint
    # Protected endpoints include authenticated user routes
    protected_patterns = [
        r"^/v?\d*/user/profile",
        r"^/v?\d*/user/settings/persistent",
    ]

    is_protected = False
    for pattern in protected_patterns:
        if re.match(pattern, request.path):
            is_protected = True
            break

    if not is_protected:
        # Not a protected endpoint, skip JWT validation
        return

    # Extract token from Authorization header
    auth_header = request.headers.get("Authorization", "")

    if not auth_header:
        return json({"error": "Authorization header required"}, status=401)

    # Check for Bearer token format
    if not auth_header.startswith("Bearer "):
        return json({"error": "Invalid authorization header format"}, status=401)

    token = auth_header[7:]  # Remove "Bearer " prefix

    # Verify token
    payload = auth_service.verify_jwt_token(token)

    if not payload:
        return json({"error": "Invalid or expired token"}, status=401)

    # Attach user info to request context
    request.ctx.user_id = payload.get("user_id")
    request.ctx.username = payload.get("username")

    if not request.ctx.user_id:
        return json({"error": "Invalid token claims"}, status=401)
