"""
Alert endpoints: a user's alert rules, their in-tab inbox, and a test alert.

All routes are JWT protected (see constants/route.py JWT_PROTECTED_ROUTES).
"""

import re
import secrets
from datetime import datetime

import business.alerts  # noqa: F401  (registers the alert types)
import services.alerts_db as alerts_db
import services.redis as redis_client
from business.alerts import registry
from business.alerts.channels.inbox import INBOX_FIELD, inbox_key
from business.alerts.dispatcher import async_bump_rules_version
from business.alerts.events import async_emit_alert_event
from models.alerts import MAX_ALERT_RULE_BODY_BYTES, AlertRuleInput

import orjson
from pydantic import ValidationError
from sanic import Blueprint
from sanic.request import Request
from sanic.response import json
from sanic_ext import openapi

alerts_blueprint = Blueprint("alerts", url_prefix="/user/alerts", version=1)

STREAM_ID_PATTERN = re.compile(r"^\d{1,20}-\d{1,20}$")
INBOX_PAGE_SIZE = 50


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def _rule_to_api(row: dict) -> dict:
    return {
        "id": row["id"],
        "type": row["type"],
        "criteria": row.get("criteria") or {},
        "enabled": row["enabled"],
        "expires_at": _iso(row.get("expires_at")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def _validation_message(e: ValidationError) -> str:
    parts = []
    for err in e.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        parts.append(f"{loc}: {err['msg']}" if loc else err["msg"])
    return ", ".join(parts)


async def _parse_rule_body(request: Request, user_id: int):
    """Returns (AlertRuleInput, validated criteria dict) or raises ValueError with a user-facing message."""
    if len(request.body or b"") > MAX_ALERT_RULE_BODY_BYTES:
        raise ValueError("Request body too large")
    try:
        body = AlertRuleInput(**(request.json or {}))
    except ValidationError as e:
        raise ValueError(_validation_message(e))
    alert_type = registry.get_type(body.type)
    if alert_type is None or not alert_type.user_creatable:
        raise ValueError(f"Unknown alert type: {body.type}")
    try:
        criteria = alert_type.Criteria.model_validate(body.criteria)
    except ValidationError as e:
        raise ValueError(_validation_message(e))
    criteria = await alert_type.validate_for_user(criteria, user_id)
    return body, criteria.model_dump(mode="json")


def _user_id(request: Request):
    return getattr(request.ctx, "user_id", None)


@alerts_blueprint.get("/types")
@openapi.summary("List the alert types users can create rules for")
@openapi.secured("BearerAuth")
async def get_alert_types(request: Request):
    if not _user_id(request):
        return json({"error": "Unauthorized"}, status=401)
    return json(
        {
            "data": [
                {"type": t.name, "label": t.label}
                for t in registry.user_creatable_types()
            ]
        }
    )


@alerts_blueprint.get("/rules")
@openapi.summary("List the authenticated user's alert rules")
@openapi.secured("BearerAuth")
async def get_alert_rules(request: Request):
    user_id = _user_id(request)
    if not user_id:
        return json({"error": "Unauthorized"}, status=401)
    try:
        rows = await alerts_db.async_list_rules(user_id)
    except Exception:
        return json({"error": "Internal server error"}, status=500)
    return json({"data": [_rule_to_api(r) for r in rows]})


@alerts_blueprint.post("/rules")
@openapi.summary("Create an alert rule")
@openapi.secured("BearerAuth")
@openapi.body({"application/json": AlertRuleInput})
@openapi.response(400, description="Validation error or rule limit reached")
async def create_alert_rule(request: Request):
    user_id = _user_id(request)
    if not user_id:
        return json({"error": "Unauthorized"}, status=401)
    try:
        body, criteria = await _parse_rule_body(request, user_id)
    except ValueError as e:
        return json({"error": str(e)}, status=400)
    try:
        row = await alerts_db.async_create_rule(
            user_id, body.type, criteria, body.enabled, body.expires_at
        )
    except alerts_db.RuleLimitReached:
        return json(
            {
                "error": f"You can have at most {alerts_db.MAX_RULES_PER_USER} alerts."
            },
            status=400,
        )
    except Exception:
        return json({"error": "Internal server error"}, status=500)
    await async_bump_rules_version()
    return json({"data": _rule_to_api(row)}, status=201)


@alerts_blueprint.put("/rules/<rule_id:int>")
@openapi.summary("Replace an alert rule's settings (its type cannot change)")
@openapi.secured("BearerAuth")
@openapi.body({"application/json": AlertRuleInput})
@openapi.response(404, description="Rule not found")
async def update_alert_rule(request: Request, rule_id: int):
    user_id = _user_id(request)
    if not user_id:
        return json({"error": "Unauthorized"}, status=401)
    try:
        body, criteria = await _parse_rule_body(request, user_id)
    except ValueError as e:
        return json({"error": str(e)}, status=400)
    try:
        existing = await alerts_db.async_get_rule(user_id, rule_id)
        if not existing:
            return json({"error": "Rule not found"}, status=404)
        if existing["type"] != body.type:
            return json({"error": "An alert's type cannot be changed"}, status=400)
        row = await alerts_db.async_update_rule(
            user_id, rule_id, criteria, body.enabled, body.expires_at
        )
    except Exception:
        return json({"error": "Internal server error"}, status=500)
    if not row:
        return json({"error": "Rule not found"}, status=404)
    await async_bump_rules_version()
    return json({"data": _rule_to_api(row)})


@alerts_blueprint.delete("/rules/<rule_id:int>")
@openapi.summary("Delete an alert rule")
@openapi.secured("BearerAuth")
@openapi.response(404, description="Rule not found")
async def delete_alert_rule(request: Request, rule_id: int):
    user_id = _user_id(request)
    if not user_id:
        return json({"error": "Unauthorized"}, status=401)
    try:
        deleted = await alerts_db.async_delete_rule(user_id, rule_id)
    except Exception:
        return json({"error": "Internal server error"}, status=500)
    if not deleted:
        return json({"error": "Rule not found"}, status=404)
    await async_bump_rules_version()
    return json({"data": {"deleted": True}})


def _decode(value):
    return value.decode() if isinstance(value, bytes) else value


@alerts_blueprint.get("/inbox")
@openapi.summary("Alerts delivered to the user after a given stream ID")
@openapi.description(
    "Without `after`, returns no alerts and the latest ID, which the client uses as its starting point."
)
@openapi.secured("BearerAuth")
async def get_alert_inbox(request: Request):
    user_id = _user_id(request)
    if not user_id:
        return json({"error": "Unauthorized"}, status=401)
    after = request.args.get("after")
    if after is not None and not STREAM_ID_PATTERN.match(after):
        return json({"error": "Invalid 'after' value"}, status=400)

    try:
        client = await redis_client.get_async_redis_client()
        key = inbox_key(user_id)
        if after is None:
            latest = await client.xrevrange(key, count=1)
            latest_id = _decode(latest[0][0]) if latest else "0-0"
            return json({"data": {"alerts": [], "latest_id": latest_id}})

        entries = await client.xrange(key, min=f"({after}", count=INBOX_PAGE_SIZE)
    except Exception:
        return json({"error": "Internal server error"}, status=500)

    alerts = []
    latest_id = after
    for entry_id, fields in entries:
        latest_id = _decode(entry_id)
        raw = fields.get(INBOX_FIELD.encode()) or fields.get(INBOX_FIELD)
        if not raw:
            continue
        try:
            alerts.append(orjson.loads(raw))
        except orjson.JSONDecodeError:
            continue
    return json({"data": {"alerts": alerts, "latest_id": latest_id}})


@alerts_blueprint.post("/test")
@openapi.summary("Send a test alert to the authenticated user through every channel")
@openapi.secured("BearerAuth")
async def send_test_alert(request: Request):
    user_id = _user_id(request)
    if not user_id:
        return json({"error": "Unauthorized"}, status=401)
    queued = await async_emit_alert_event(
        "test", {"user_id": user_id, "nonce": secrets.token_hex(8)}
    )
    if not queued:
        return json({"error": "Could not queue the test alert"}, status=500)
    return json({"data": {"queued": True}}, status=202)
