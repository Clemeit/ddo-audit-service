import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from time import monotonic_ns

from constants.server import SSE_SERVER_NAMES_LOWERCASE

logger = logging.getLogger(__name__)

# ── Stream identity ───────────────────────────────────────────────────────────
# Initialized once at process start; changes on deploy/restart so clients can
# detect epoch mismatch after a reconnect.
STREAM_EPOCH: str = str(uuid.uuid4())

# ── Per-stream monotonic sequence counters ────────────────────────────────────
# key: (stream_type, server_name)  value: current seq (first broadcast = 1)
_seq_counters: dict[tuple[str, str], int] = {}

# ── Metrics ───────────────────────────────────────────────────────────────────
# sse_clients_connected is computed dynamically in get_metrics().
_metrics: dict[str, int] = {
    "sse_snapshot_sent_total": 0,
    "sse_delta_sent_total": 0,
    "sse_delta_suppressed_noop_total": 0,
    "sse_queue_evictions_total": 0,
    "sse_reconnects_total": 0,
    # Disconnect tracking (per-stream)
    "sse_characters_disconnect_total": 0,
    "sse_characters_disconnect_error_total": 0,
    "sse_lfms_disconnect_total": 0,
    "sse_lfms_disconnect_error_total": 0,
    # Send latency (aggregate across both streams)
    "sse_send_latency_ms_total": 0,
    "sse_send_latency_samples": 0,
}

# ── Client registries ─────────────────────────────────────────────────────────
character_queues: dict[str, set[asyncio.Queue]] = {
    s: set() for s in SSE_SERVER_NAMES_LOWERCASE
}
lfm_queues: dict[str, set[asyncio.Queue]] = {
    s: set() for s in SSE_SERVER_NAMES_LOWERCASE
}


def get_metrics() -> dict:
    """Return current SSE metrics.

    Aggregate and per-stream values are computed dynamically from the live
    queue sets and in-process counters. ``sse_send_latency_ms_avg`` is
    zero-safe (returns 0 when no broadcasts have occurred yet).
    """
    char_connected = sum(len(qs) for qs in character_queues.values())
    lfm_connected = sum(len(qs) for qs in lfm_queues.values())
    samples = _metrics["sse_send_latency_samples"]
    avg_latency = _metrics["sse_send_latency_ms_total"] // samples if samples > 0 else 0
    return {
        **_metrics,
        # Aggregate computed fields
        "sse_clients_connected": char_connected + lfm_connected,
        "sse_disconnect_total": (
            _metrics["sse_characters_disconnect_total"]
            + _metrics["sse_lfms_disconnect_total"]
        ),
        "sse_disconnect_error_total": (
            _metrics["sse_characters_disconnect_error_total"]
            + _metrics["sse_lfms_disconnect_error_total"]
        ),
        "sse_send_latency_ms_avg": avg_latency,
        # Per-stream breakdown
        "streams": {
            "characters": {
                "clients_connected": char_connected,
                "disconnect_total": _metrics["sse_characters_disconnect_total"],
                "disconnect_error_total": _metrics["sse_characters_disconnect_error_total"],
            },
            "lfms": {
                "clients_connected": lfm_connected,
                "disconnect_total": _metrics["sse_lfms_disconnect_total"],
                "disconnect_error_total": _metrics["sse_lfms_disconnect_error_total"],
            },
        },
    }


def record_reconnect() -> None:
    """Increment the reconnect counter (call when Last-Event-ID header is present)."""
    _metrics["sse_reconnects_total"] += 1


def record_disconnect(stream_type: str, *, error: bool = False) -> None:
    """Increment disconnect counters for the given stream type.

    Call with ``error=False`` on a clean stream exit (deadline or close),
    and ``error=True`` when an exception interrupts the stream.
    """
    if stream_type == "characters":
        _metrics["sse_characters_disconnect_total"] += 1
        if error:
            _metrics["sse_characters_disconnect_error_total"] += 1
    elif stream_type == "lfms":
        _metrics["sse_lfms_disconnect_total"] += 1
        if error:
            _metrics["sse_lfms_disconnect_error_total"] += 1


# ── Internal helpers ──────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_current_seq(stream_type: str, server_name: str) -> int:
    """Return the current seq without incrementing."""
    return _seq_counters.get((stream_type, server_name), 0)


def _increment_seq(stream_type: str, server_name: str) -> int:
    """Increment and return the next seq for this stream."""
    key = (stream_type, server_name)
    _seq_counters[key] = _seq_counters.get(key, 0) + 1
    return _seq_counters[key]


# ── Public API ────────────────────────────────────────────────────────────────


def format_sse(event: str, data: str, event_id: str | None = None) -> str:
    """Format an SSE frame.

    When *event_id* is provided an ``id:`` field is prepended so that
    browser ``EventSource`` clients automatically send ``Last-Event-ID``
    on reconnect, enabling seq-based gap detection.
    """
    id_line = f"id: {event_id}\n" if event_id is not None else ""
    return f"{id_line}event: {event}\ndata: {data}\n\n"


def make_snapshot_envelope(stream_type: str, server_name: str, data: dict) -> str:
    """Build an SSE snapshot event for a newly-connected client.

    Uses the *current* seq (without incrementing) so the client knows which
    broadcast position the snapshot corresponds to.
    """
    envelope = {
        "type": "snapshot",
        "seq": _get_current_seq(stream_type, server_name),
        "epoch": STREAM_EPOCH,
        "server": server_name,
        "sent_at": _now_iso(),
        "data": data,
    }
    return format_sse("snapshot", json.dumps(envelope), event_id=str(envelope["seq"]))


def register(
    registry: dict[str, set[asyncio.Queue]], server_name: str
) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=20)
    registry[server_name].add(queue)
    return queue


def unregister(
    registry: dict[str, set[asyncio.Queue]],
    server_name: str,
    queue: asyncio.Queue,
) -> None:
    registry[server_name].discard(queue)


def broadcast(
    registry: dict[str, set[asyncio.Queue]],
    server_name: str,
    sse_message: str,
) -> int:
    start_ns = monotonic_ns()
    notified = 0
    for q in set(registry[server_name]):
        try:
            q.put_nowait(sse_message)
            notified += 1
        except asyncio.QueueFull:
            unregister(registry, server_name, q)
            _metrics["sse_queue_evictions_total"] += 1
    _metrics["sse_send_latency_ms_total"] += (monotonic_ns() - start_ns) // 1_000_000
    _metrics["sse_send_latency_samples"] += 1
    return notified


def broadcast_snapshot(
    stream_type: str,
    queues: dict[str, set[asyncio.Queue]],
    server_name: str,
    data: dict,
) -> int:
    """Build a snapshot envelope, broadcast it, and update metrics."""
    seq = _increment_seq(stream_type, server_name)
    envelope = {
        "type": "snapshot",
        "seq": seq,
        "epoch": STREAM_EPOCH,
        "server": server_name,
        "sent_at": _now_iso(),
        "data": data,
    }
    msg = format_sse("snapshot", json.dumps(envelope), event_id=str(seq))
    count = broadcast(queues, server_name, msg)
    _metrics["sse_snapshot_sent_total"] += count
    logger.debug(
        "sse snapshot broadcast",
        extra={
            "stream_type": stream_type,
            "server": server_name,
            "seq": seq,
            "epoch": STREAM_EPOCH,
            "client_count": count,
        },
    )
    return count


def broadcast_delta(
    stream_type: str,
    queues: dict[str, set[asyncio.Queue]],
    server_name: str,
    updates: list,
    removals: list,
) -> int:
    """Build a delta envelope, broadcast it, and update metrics.

    Returns 0 without broadcasting when both *updates* and *removals* are empty
    (no-op suppression).
    """
    if not updates and not removals:
        _metrics["sse_delta_suppressed_noop_total"] += 1
        return 0
    seq = _increment_seq(stream_type, server_name)
    envelope = {
        "type": "delta",
        "seq": seq,
        "epoch": STREAM_EPOCH,
        "server": server_name,
        "sent_at": _now_iso(),
        "updates": updates,
        "removals": removals,
    }
    msg = format_sse("delta", json.dumps(envelope), event_id=str(seq))
    count = broadcast(queues, server_name, msg)
    _metrics["sse_delta_sent_total"] += count
    logger.debug(
        "sse delta broadcast",
        extra={
            "stream_type": stream_type,
            "server": server_name,
            "seq": seq,
            "epoch": STREAM_EPOCH,
            "client_count": count,
        },
    )
    return count
