"""
SSE drift detector — connects to the v2 SSE stream for a given server, maintains
a local replica of the state by applying snapshot / delta events, and on every
event polls the v1 REST endpoint and reports any discrepancies.

Usage:
    python scripts/sse_drift_check.py
    python scripts/sse_drift_check.py --server shadowdale --type lfms
    python scripts/sse_drift_check.py --base-url https://api.ddoaudit.com --server cormyr
    python scripts/sse_drift_check.py --deep        # also compare field values

Press Ctrl+C to stop at any time.
"""

import argparse
import json
import sys
import time
from typing import Any

import requests

DEFAULT_BASE_URL = "http://localhost:8000"
ID_FIELD = "id"
POLL_TIMEOUT = 10  # seconds for v1 REST poll
SSE_CONNECT_TIMEOUT = 10  # seconds to establish initial SSE connection
RECONNECT_DELAY = 5  # seconds to wait before reconnecting after an error


# ---------------------------------------------------------------------------
# SSE parsing
# ---------------------------------------------------------------------------


def iter_sse_events(response: requests.Response):
    """
    Yield (event_name, data) tuples from a streaming SSE response.
    Emits keepalive comments as '__keepalive__' events so callers can
    account for transferred bytes.
    Buffers multi-line data fields.
    """
    event_name = "message"
    data_lines: list[str] = []

    for raw in response.iter_lines(decode_unicode=True):
        # Keepalive comments
        if raw.startswith(":"):
            yield "__keepalive__", raw[1:]
            continue

        # Empty line = dispatch the buffered event
        if raw == "":
            if data_lines:
                yield event_name, "\n".join(data_lines)
                event_name = "message"
                data_lines = []
            continue

        if raw.startswith("event:"):
            event_name = raw[len("event:") :].strip()
        elif raw.startswith("data:"):
            data_lines.append(raw[len("data:") :].strip())
        # id: and retry: fields are ignored


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------


def apply_snapshot(state: dict[str, Any], raw_data: str) -> None:
    """Replace the entire local state with the snapshot payload.

    Handles both the raw ID→entity mapping (v1-style) and the v2 envelope
    format ``{type, seq, epoch, server, sent_at, data}`` by unwrapping the
    inner ``data`` field when the envelope is detected.
    """
    state.clear()
    parsed = json.loads(raw_data)
    # v2 envelope: top-level keys include "type" and "data"
    if isinstance(parsed, dict) and "type" in parsed and "data" in parsed:
        parsed = parsed["data"]
    for k, v in parsed.items():
        state[str(k)] = v


def apply_delta(state: dict[str, Any], raw_data: str) -> None:
    """Apply updates and removals from a delta event to the local state."""
    payload = json.loads(raw_data)
    for item in payload.get("updates", []):
        state[str(item[ID_FIELD])] = item
    for id_ in payload.get("removals", []):
        state.pop(str(id_), None)


def drop_nulls(value: Any) -> Any:
    """Recursively remove null values from dict/list payloads."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for k, v in value.items():
            if v is None:
                continue
            cleaned[k] = drop_nulls(v)
        return cleaned

    if isinstance(value, list):
        return [drop_nulls(item) for item in value]

    return value


def estimate_sse_event_bytes(event_name: str, raw_data: str) -> int:
    """Approximate bytes transferred for an SSE frame on the wire.

    Handles multi-line data: iter_sse_events joins multiple ``data:`` lines
    with ``\n``, so we must emit one ``data:`` prefix per line when computing
    the byte count.
    """
    if event_name == "__keepalive__":
        # Preserve any leading space in comment payload.
        return len(f":{raw_data}\n\n".encode("utf-8"))
    data_lines = raw_data.split("\n")
    frame = (
        f"event: {event_name}\n"
        + "".join(f"data: {line}\n" for line in data_lines)
        + "\n"
    )
    return len(frame.encode("utf-8"))


def format_bytes(num_bytes: int) -> str:
    """Format bytes in a human-readable form."""
    units = ["B", "KB", "MB", "GB"]
    size = float(num_bytes)
    unit_idx = 0
    while size >= 1024 and unit_idx < len(units) - 1:
        size /= 1024
        unit_idx += 1
    return f"{size:.2f} {units[unit_idx]}"


def format_transfer_summary(
    latest_v2_bytes: int,
    latest_v1_bytes: int,
    total_v2_bytes: int,
    total_v1_bytes: int,
    drift_count: int,
) -> str:
    """Build a compact transfer summary with latest and cumulative sizes."""
    reduction_pct = get_reduction_percent(total_v1_bytes, total_v2_bytes)
    return (
        f"last v2={format_bytes(latest_v2_bytes)} "
        f"v1={format_bytes(latest_v1_bytes)} | "
        f"total v2={format_bytes(total_v2_bytes)} "
        f"v1={format_bytes(total_v1_bytes)} "
        f"reduction={reduction_pct:.2f}% drift={drift_count}"
    )


def get_reduction_percent(v1_bytes: int, v2_bytes: int) -> float:
    """
    Return percent reduction from v1 to v2.
    Positive means v2 transferred less; negative means v2 transferred more.
    """
    if v1_bytes <= 0:
        return 0.0
    return ((v1_bytes - v2_bytes) / v1_bytes) * 100.0


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


def compare(
    v2_state: dict[str, Any],
    v1_data: dict[str, Any],
    deep: bool,
) -> list[str]:
    """
    Compare the v2 local state against the v1 REST snapshot.
    Returns a list of human-readable discrepancy strings (empty = no drift).
    """
    diffs: list[str] = []

    v2_ids = set(v2_state.keys())
    v1_ids = set(str(k) for k in v1_data.keys())

    only_v2 = v2_ids - v1_ids
    only_v1 = v1_ids - v2_ids

    if only_v2:
        sample = sorted(only_v2, key=lambda x: int(x) if x.isdigit() else x)[:20]
        diffs.append(
            f"  {len(only_v2)} ID(s) in v2 but NOT in v1"
            f"{' (sample)' if len(only_v2) > 20 else ''}: {sample}"
        )
    if only_v1:
        sample = sorted(only_v1, key=lambda x: int(x) if x.isdigit() else x)[:20]
        diffs.append(
            f"  {len(only_v1)} ID(s) in v1 but NOT in v2"
            f"{' (sample)' if len(only_v1) > 20 else ''}: {sample}"
        )

    if deep:
        common = v2_ids & v1_ids
        field_drift_count = 0
        sorted_common = sorted(common, key=lambda x: int(x) if x.isdigit() else x)
        for loop_idx, id_ in enumerate(sorted_common):
            v2_entity = drop_nulls(v2_state[id_])
            v1_entity = drop_nulls(v1_data[str(id_)])
            field_diffs: list[str] = []
            for key in sorted(set(v2_entity) | set(v1_entity)):
                v2_val = v2_entity.get(key)
                v1_val = v1_entity.get(key)
                if v2_val != v1_val:
                    field_diffs.append(f"{key}: v2={v2_val!r} | v1={v1_val!r}")
            if field_diffs:
                field_drift_count += 1
                # Show first 5 differing fields per entity to keep output readable
                diffs.append(f"  ID {id_}: {'; '.join(field_diffs[:5])}")
                if field_drift_count >= 10:
                    remaining = len(sorted_common) - loop_idx - 1
                    if remaining > 0:
                        diffs.append(
                            f"  … (field diffs truncated, {remaining} more IDs not shown)"
                        )
                    break

    return diffs


# ---------------------------------------------------------------------------
# v1 poll
# ---------------------------------------------------------------------------


def poll_v1(session: requests.Session, url: str) -> tuple[dict[str, Any] | None, int]:
    """Fetch v1 data and return (str-keyed dict or None, response byte size)."""
    try:
        resp = session.get(url, timeout=POLL_TIMEOUT)
        resp.raise_for_status()
        response_size = len(resp.content)
        data = resp.json().get("data", {})
        return {str(k): v for k, v in data.items()}, response_size
    except Exception as exc:
        print(f"[WARN] v1 poll error: {exc}", file=sys.stderr)
        return None, 0


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def run(args: argparse.Namespace) -> None:
    server = args.server.lower()
    entity = args.type  # "characters" or "lfms"
    base = args.base_url.rstrip("/")

    sse_url = f"{base}/v2/{entity}/stream/{server}"
    v1_url = f"{base}/v1/{entity}/{server}"

    print(f"SSE stream : {sse_url}")
    print(f"v1 REST    : {v1_url}")
    print(f"Deep check : {'yes (field values)' if args.deep else 'no (ID sets only)'}")
    print("Press Ctrl+C to stop.\n")

    sse_session = requests.Session()
    sse_session.headers["Accept"] = "text/event-stream"
    sse_session.headers["Cache-Control"] = "no-cache"

    poll_session = requests.Session()

    v2_state: dict[str, Any] = {}
    event_count = 0
    drift_count = 0
    total_v2_bytes = 0
    total_v1_bytes = 0

    while True:
        try:
            print(f"[INFO] Connecting …")
            with sse_session.get(
                sse_url, stream=True, timeout=(SSE_CONNECT_TIMEOUT, None)
            ) as resp:
                resp.raise_for_status()
                print(f"[INFO] Connected (HTTP {resp.status_code})")

                for event_name, raw_data in iter_sse_events(resp):
                    event_count += 1
                    ts = time.strftime("%H:%M:%S")
                    latest_v2_bytes = estimate_sse_event_bytes(event_name, raw_data)
                    total_v2_bytes += latest_v2_bytes

                    if event_name == "__keepalive__":
                        transfer_summary = format_transfer_summary(
                            latest_v2_bytes=latest_v2_bytes,
                            latest_v1_bytes=0,
                            total_v2_bytes=total_v2_bytes,
                            total_v1_bytes=total_v1_bytes,
                            drift_count=drift_count,
                        )
                        print(
                            f"[{ts}] keepalive              | transfer {transfer_summary}"
                        )
                        continue

                    if event_name == "snapshot":
                        apply_snapshot(v2_state, raw_data)
                        label = f"snapshot       v2={len(v2_state):>5}"

                    elif event_name == "delta":
                        apply_delta(v2_state, raw_data)
                        payload = json.loads(raw_data)
                        u = len(payload.get("updates", []))
                        r = len(payload.get("removals", []))
                        label = f"delta +{u:>3}/-{r:<3}  v2={len(v2_state):>5}"

                    elif event_name == "close":
                        print(f"[{ts}] Server sent close event — reconnecting …")
                        break

                    else:
                        print(f"[{ts}] Unknown event type '{event_name}', skipping.")
                        continue

                    # Poll v1 immediately after updating local state
                    v1_data, v1_response_size = poll_v1(poll_session, v1_url)
                    total_v1_bytes += v1_response_size
                    transfer_summary = format_transfer_summary(
                        latest_v2_bytes=latest_v2_bytes,
                        latest_v1_bytes=v1_response_size,
                        total_v2_bytes=total_v2_bytes,
                        total_v1_bytes=total_v1_bytes,
                        drift_count=drift_count,
                    )
                    if v1_data is None:
                        print(f"[{ts}] {label}  | poll failed | {transfer_summary}")
                        continue

                    diffs = compare(v2_state, v1_data, args.deep)
                    if diffs:
                        drift_count += 1
                        transfer_summary = format_transfer_summary(
                            latest_v2_bytes=latest_v2_bytes,
                            latest_v1_bytes=v1_response_size,
                            total_v2_bytes=total_v2_bytes,
                            total_v1_bytes=total_v1_bytes,
                            drift_count=drift_count,
                        )
                        print(
                            f"[{ts}] {label}  | DRIFT (v1={len(v1_data)}) "
                            f"| transfer {transfer_summary}"
                        )
                        for d in diffs:
                            print(d)
                    else:
                        print(
                            f"[{ts}] {label}  | OK (v1={len(v1_data):>5}) | transfer {transfer_summary}"
                        )

        except KeyboardInterrupt:
            final_reduction = get_reduction_percent(total_v1_bytes, total_v2_bytes)
            print(
                "\n[INFO] Stopped. "
                f"Events seen: {event_count}, drift events: {drift_count}, "
                f"v2 transferred: {format_bytes(total_v2_bytes)}, "
                f"v1 transferred: {format_bytes(total_v1_bytes)}, "
                f"reduction: {final_reduction:.2f}%."
            )
            break
        except requests.exceptions.RequestException as exc:
            print(f"[WARN] Connection error: {exc}. Retrying in {RECONNECT_DELAY}s …")
            try:
                time.sleep(RECONNECT_DELAY)
            except KeyboardInterrupt:
                print("[INFO] Stopped.")
                break


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Connect to a v2 SSE stream and verify it stays consistent with the v1 REST endpoint."
        )
    )
    parser.add_argument(
        "--server",
        default="cormyr",
        metavar="SERVER",
        help="Server name to monitor (default: cormyr)",
    )
    parser.add_argument(
        "--type",
        choices=["characters", "lfms"],
        default="characters",
        metavar="TYPE",
        help="Entity type: characters or lfms (default: characters)",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        dest="base_url",
        metavar="URL",
        help=f"API base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Also compare field values for matching IDs, not just ID set membership",
    )
    run(parser.parse_args())


if __name__ == "__main__":
    main()
