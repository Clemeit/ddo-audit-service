import json
import os
import random
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sanic.request import Request
from sanic.response import HTTPResponse


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except Exception:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except Exception:
        return default


ACCESS_LOG_ENABLED = _env_bool("ACCESS_LOG_ENABLED", True)
ACCESS_LOG_SAMPLE_RATE = max(0.0, min(1.0, _env_float("ACCESS_LOG_SAMPLE_RATE", 1.0)))
ACCESS_LOG_SLOW_MS = _env_int("ACCESS_LOG_SLOW_MS", 750)
ACCESS_LOG_INCLUDE_QUERY = _env_bool("ACCESS_LOG_INCLUDE_QUERY", False)


def get_request_id(request: Request) -> str:
    existing = request.headers.get("x-request-id") or request.headers.get(
        "x-correlation-id"
    )
    if existing:
        # avoid logging unbounded header values
        return existing.strip()[:128]
    return uuid.uuid4().hex


def get_client_ip(request: Request) -> Optional[str]:
    # Prefer proxy-provided IPs if present (nginx typically sets one of these).
    x_real_ip = request.headers.get("x-real-ip")
    if x_real_ip:
        return x_real_ip.strip()[:64]

    x_forwarded_for = request.headers.get("x-forwarded-for")
    if x_forwarded_for:
        # left-most is the original client IP
        ip = x_forwarded_for.split(",")[0].strip()
        return ip[:64] if ip else None

    # Sanic-populated peer IP (often the reverse proxy)
    try:
        return request.ip
    except Exception:
        return None


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def response_size_bytes(response: HTTPResponse) -> Optional[int]:
    # Prefer explicit Content-Length when set.
    cl = _safe_int(getattr(response, "headers", {}).get("content-length"))
    if cl is not None:
        return cl

    # Fall back to body length where possible.
    body = getattr(response, "body", None)
    if body is None:
        return None
    try:
        return len(body)
    except Exception:
        return None


def should_log(status: int, duration_ms: int) -> bool:
    if not ACCESS_LOG_ENABLED:
        return False

    # Always log errors + slow requests.
    if status >= 400:
        return True
    if duration_ms >= ACCESS_LOG_SLOW_MS:
        return True

    # Otherwise sample.
    if ACCESS_LOG_SAMPLE_RATE >= 1.0:
        return True
    if ACCESS_LOG_SAMPLE_RATE <= 0.0:
        return False
    return random.random() < ACCESS_LOG_SAMPLE_RATE


def build_access_event(
    request: Request,
    response: HTTPResponse,
    *,
    request_id: str,
    duration_ms: int,
) -> dict:
    method = getattr(request, "method", None)
    path = getattr(request, "path", None)
    qs = getattr(request, "query_string", "") or ""

    # Sanic's request.route may be None for 404s
    route_path = None
    try:
        if getattr(request, "route", None):
            route_path = getattr(request.route, "path", None)
    except Exception:
        route_path = None

    event: dict[str, Any] = {
        "type": "access",
        "ts": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "ip": get_client_ip(request),
        "remote_ip": getattr(request, "ip", None),
        "method": method,
        "path": path,
        "route": route_path,
        "status": getattr(response, "status", None),
        "duration_ms": duration_ms,
        "bytes_in": _safe_int(request.headers.get("content-length")),
        "bytes_out": response_size_bytes(response),
        "user_agent": request.headers.get("user-agent"),
        "referrer": request.headers.get("referer"),
        "host": request.headers.get("host"),
    }

    if ACCESS_LOG_INCLUDE_QUERY and qs:
        # Can include PII; keep this opt-in.
        event["query_string"] = qs[:2048]

    return event


def monotonic_start_ns() -> int:
    return time.monotonic_ns()


def monotonic_duration_ms(start_ns: int) -> int:
    try:
        return int((time.monotonic_ns() - start_ns) / 1_000_000)
    except Exception:
        return 0


def dumps_event(event: dict) -> str:
    # Compact JSON (good for log shipping).
    return json.dumps(event, separators=(",", ":"), ensure_ascii=False)
