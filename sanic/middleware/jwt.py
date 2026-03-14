"""
JWT authentication middleware for protecting endpoints.

This middleware validates JWT tokens from the Authorization header
and attaches user information to the request context.
"""

from sanic import Request
from sanic.response import json
import services.auth as auth_service
from utils.route import is_jwt_protected


def _unauthorized_response():
    """Return a generic unauthorized response for all JWT auth failures."""
    return json({"error": "Unauthorized"}, status=401)


async def jwt_middleware(request: Request):
    """
    JWT middleware to validate tokens on protected endpoints.

    Attaches user_id and username to request.ctx if token is valid.
    Allows request to proceed if endpoint doesn't require authentication.
    Returns 401 if token is missing or invalid on protected endpoints.
    """

    if not is_jwt_protected(request):
        # Not a protected endpoint, skip JWT validation
        return

    # Extract token from Authorization header
    auth_header = request.headers.get("Authorization", "")

    if not auth_header:
        return _unauthorized_response()

    # Check for Bearer token format
    if not auth_header.startswith("Bearer "):
        return _unauthorized_response()

    token = auth_header[7:]  # Remove "Bearer " prefix

    # Verify token
    payload = auth_service.verify_jwt_token(token)

    if not payload:
        return _unauthorized_response()

    # Attach user info to request context
    request.ctx.user_id = payload.get("user_id")
    request.ctx.username = payload.get("username")

    if not request.ctx.user_id:
        return _unauthorized_response()
