"""
Web Push endpoints: the VAPID public key browsers subscribe with, and saving/removing a browser's
push subscription for the authenticated user.
"""

import os

import services.push_db as push_db
from models.push import PushSubscriptionDelete, PushSubscriptionInput

from pydantic import ValidationError
from sanic import Blueprint
from sanic.request import Request
from sanic.response import json
from sanic_ext import openapi

push_blueprint = Blueprint("push", url_prefix="/push", version=1)
push_subscriptions_blueprint = Blueprint(
    "push_subscriptions", url_prefix="/user/push-subscriptions", version=1
)

MAX_BODY_BYTES = 4 * 1024
MAX_USER_AGENT_LENGTH = 300


def vapid_public_key() -> str:
    return os.getenv("VAPID_PUBLIC_KEY", "").strip()


def _validation_message(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        parts.append(f"{loc}: {err['msg']}" if loc else err["msg"])
    return ", ".join(parts)


def _parse(request: Request, model):
    if len(request.body or b"") > MAX_BODY_BYTES:
        raise ValueError("Request body too large")
    try:
        return model(**(request.json or {}))
    except ValidationError as e:
        raise ValueError(_validation_message(e))


@push_blueprint.get("/vapid-public-key")
@openapi.summary("Public key browsers use to subscribe to DDO Audit push notifications")
@openapi.response(503, description="Push notifications are not configured")
async def get_vapid_public_key(request: Request):
    key = vapid_public_key()
    if not key:
        return json({"error": "Push notifications are not available"}, status=503)
    return json({"data": {"public_key": key}})


@push_subscriptions_blueprint.post("")
@openapi.summary("Save this browser's push subscription for the authenticated user")
@openapi.secured("BearerAuth")
@openapi.response(400, description="Invalid subscription")
async def save_push_subscription(request: Request):
    user_id = getattr(request.ctx, "user_id", None)
    if not user_id:
        return json({"error": "Unauthorized"}, status=401)
    if not vapid_public_key():
        return json({"error": "Push notifications are not available"}, status=503)
    try:
        body = _parse(request, PushSubscriptionInput)
    except ValueError as e:
        return json({"error": str(e)}, status=400)
    user_agent = (request.headers.get("User-Agent") or "")[:MAX_USER_AGENT_LENGTH]
    try:
        await push_db.async_upsert_subscription(
            user_id, body.endpoint, body.keys.p256dh, body.keys.auth, user_agent or None
        )
    except Exception:
        return json({"error": "Internal server error"}, status=500)
    return json({"data": {"saved": True}}, status=201)


# DELETE with a JSON body: Sanic ignores DELETE bodies unless told otherwise.
@push_subscriptions_blueprint.delete("", ignore_body=False)
@openapi.summary("Remove a push subscription of the authenticated user")
@openapi.secured("BearerAuth")
async def delete_push_subscription(request: Request):
    user_id = getattr(request.ctx, "user_id", None)
    if not user_id:
        return json({"error": "Unauthorized"}, status=401)
    try:
        body = _parse(request, PushSubscriptionDelete)
    except ValueError as e:
        return json({"error": str(e)}, status=400)
    try:
        deleted = await push_db.async_delete_subscription(user_id, body.endpoint)
    except Exception:
        return json({"error": "Internal server error"}, status=500)
    return json({"data": {"deleted": deleted}})
