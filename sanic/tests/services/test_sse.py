import asyncio
import json

import pytest

import services.sse as sse_service


def test_format_sse_returns_correct_format():
    result = sse_service.format_sse("snapshot", '{"key": "value"}')
    assert result == 'event: snapshot\ndata: {"key": "value"}\n\n'


def test_format_sse_works_for_delta_event():
    result = sse_service.format_sse("delta", "{}")
    assert result == "event: delta\ndata: {}\n\n"


def test_format_sse_with_event_id_prepends_id_field():
    result = sse_service.format_sse("snapshot", "{}", event_id="42")
    assert result == "id: 42\nevent: snapshot\ndata: {}\n\n"


def test_register_adds_queue_to_registry():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}
    queue = sse_service.register(registry, "cormyr")
    assert queue in registry["cormyr"]
    assert isinstance(queue, asyncio.Queue)
    assert queue.maxsize == 20


def test_register_returns_independent_queues():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}
    q1 = sse_service.register(registry, "cormyr")
    q2 = sse_service.register(registry, "cormyr")
    assert q1 is not q2
    assert len(registry["cormyr"]) == 2


def test_unregister_removes_queue_from_registry():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}
    queue = sse_service.register(registry, "cormyr")
    sse_service.unregister(registry, "cormyr", queue)
    assert queue not in registry["cormyr"]


def test_unregister_is_noop_for_unknown_queue():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}
    queue = asyncio.Queue()
    sse_service.unregister(registry, "cormyr", queue)  # must not raise


def test_broadcast_sends_message_to_all_queues():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}
    q1 = sse_service.register(registry, "cormyr")
    q2 = sse_service.register(registry, "cormyr")

    count = sse_service.broadcast(registry, "cormyr", "event: test\ndata: {}\n\n")

    assert count == 2
    assert q1.get_nowait() == "event: test\ndata: {}\n\n"
    assert q2.get_nowait() == "event: test\ndata: {}\n\n"


def test_broadcast_evicts_full_queues_and_retains_healthy_ones():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}

    q_full = asyncio.Queue(maxsize=1)
    q_full.put_nowait("already full")
    registry["cormyr"].add(q_full)

    q_ok = sse_service.register(registry, "cormyr")

    count = sse_service.broadcast(registry, "cormyr", "event: test\ndata: {}\n\n")

    assert count == 1
    assert q_full not in registry["cormyr"]
    assert q_ok in registry["cormyr"]
    assert q_ok.get_nowait() == "event: test\ndata: {}\n\n"


def test_broadcast_returns_zero_for_empty_registry():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}
    count = sse_service.broadcast(registry, "cormyr", "event: test\ndata: {}\n\n")
    assert count == 0


def test_broadcast_does_not_affect_other_servers():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set(), "thrane": set()}
    q_cormyr = sse_service.register(registry, "cormyr")
    q_thrane = sse_service.register(registry, "thrane")

    sse_service.broadcast(registry, "cormyr", "event: test\ndata: {}\n\n")

    assert q_cormyr.qsize() == 1
    assert q_thrane.qsize() == 0


# ── Stream epoch tests ────────────────────────────────────────────────────────


def test_stream_epoch_is_a_non_empty_string():
    assert isinstance(sse_service.STREAM_EPOCH, str)
    assert len(sse_service.STREAM_EPOCH) > 0


def test_stream_epoch_is_stable_within_process():
    epoch_a = sse_service.STREAM_EPOCH
    epoch_b = sse_service.STREAM_EPOCH
    assert epoch_a == epoch_b


# ── Sequence counter tests ────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_seq_counters(monkeypatch):
    """Isolate seq counter state between tests."""
    monkeypatch.setattr(sse_service, "_seq_counters", {})


def test_get_current_seq_returns_zero_before_any_broadcast():
    seq = sse_service._get_current_seq("characters", "cormyr")
    assert seq == 0


def test_increment_seq_returns_one_on_first_call():
    seq = sse_service._increment_seq("characters", "cormyr")
    assert seq == 1


def test_increment_seq_is_strictly_monotonic():
    seq1 = sse_service._increment_seq("characters", "cormyr")
    seq2 = sse_service._increment_seq("characters", "cormyr")
    seq3 = sse_service._increment_seq("characters", "cormyr")
    assert seq1 < seq2 < seq3


def test_seq_counters_are_isolated_per_server():
    sse_service._increment_seq("characters", "cormyr")
    sse_service._increment_seq("characters", "cormyr")
    seq_thrane = sse_service._increment_seq("characters", "thrane")
    assert seq_thrane == 1


def test_seq_counters_are_isolated_per_stream_type():
    sse_service._increment_seq("characters", "cormyr")
    seq_lfm = sse_service._increment_seq("lfms", "cormyr")
    assert seq_lfm == 1


# ── make_snapshot_envelope tests ─────────────────────────────────────────────


def test_make_snapshot_envelope_returns_sse_snapshot_event():
    msg = sse_service.make_snapshot_envelope(
        "characters", "cormyr", {"1": {"name": "Hero"}}
    )
    assert msg.startswith("id: ")
    assert "event: snapshot" in msg
    assert "data: " in msg
    assert msg.endswith("\n\n")


def test_make_snapshot_envelope_payload_has_required_fields():
    msg = sse_service.make_snapshot_envelope("characters", "cormyr", {"42": {}})
    payload = json.loads(msg.split("data: ", 1)[1].strip())
    assert payload["type"] == "snapshot"
    assert "seq" in payload
    assert payload["epoch"] == sse_service.STREAM_EPOCH
    assert payload["server"] == "cormyr"
    assert "sent_at" in payload
    assert "data" in payload


def test_make_snapshot_envelope_seq_does_not_increment():
    sse_service._increment_seq("characters", "cormyr")  # seq is now 1
    msg = sse_service.make_snapshot_envelope("characters", "cormyr", {})
    payload = json.loads(msg.split("data: ", 1)[1].strip())
    assert payload["seq"] == 1  # still 1 — not incremented by connect snapshot
    assert sse_service._get_current_seq("characters", "cormyr") == 1


def test_make_snapshot_envelope_includes_data_payload():
    data = {"10": {"name": "Aragorn"}, "20": {"name": "Legolas"}}
    msg = sse_service.make_snapshot_envelope("characters", "cormyr", data)
    payload = json.loads(msg.split("data: ", 1)[1].strip())
    assert payload["data"] == data


# ── broadcast_snapshot tests ──────────────────────────────────────────────────


def test_broadcast_snapshot_sends_snapshot_event_to_queue():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}
    q = sse_service.register(registry, "cormyr")

    sse_service.broadcast_snapshot("characters", registry, "cormyr", {"1": {}})

    msg = q.get_nowait()
    assert msg.startswith("id: ")
    assert "event: snapshot" in msg
    payload = json.loads(msg.split("data: ", 1)[1].strip())
    assert payload["type"] == "snapshot"
    assert payload["seq"] == 1
    assert payload["epoch"] == sse_service.STREAM_EPOCH
    assert payload["server"] == "cormyr"
    assert "sent_at" in payload
    assert "data" in payload


def test_broadcast_snapshot_increments_seq():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}
    sse_service.register(registry, "cormyr")

    sse_service.broadcast_snapshot("characters", registry, "cormyr", {})
    sse_service.broadcast_snapshot("characters", registry, "cormyr", {})

    assert sse_service._get_current_seq("characters", "cormyr") == 2


def test_broadcast_snapshot_returns_notified_count():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}
    sse_service.register(registry, "cormyr")
    sse_service.register(registry, "cormyr")

    count = sse_service.broadcast_snapshot("characters", registry, "cormyr", {})
    assert count == 2


# ── broadcast_delta tests ─────────────────────────────────────────────────────


def test_broadcast_delta_sends_delta_event_to_queue():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}
    q = sse_service.register(registry, "cormyr")

    sse_service.broadcast_delta("characters", registry, "cormyr", [{"id": 1}], [2])

    msg = q.get_nowait()
    assert msg.startswith("id: ")
    assert "event: delta" in msg
    payload = json.loads(msg.split("data: ", 1)[1].strip())
    assert payload["type"] == "delta"
    assert payload["seq"] == 1
    assert payload["epoch"] == sse_service.STREAM_EPOCH
    assert payload["server"] == "cormyr"
    assert "sent_at" in payload
    assert payload["updates"] == [{"id": 1}]
    assert payload["removals"] == [2]


def test_broadcast_delta_increments_seq():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}
    sse_service.register(registry, "cormyr")

    sse_service.broadcast_delta("characters", registry, "cormyr", [{"id": 1}], [])
    sse_service.broadcast_delta("characters", registry, "cormyr", [{"id": 2}], [])

    assert sse_service._get_current_seq("characters", "cormyr") == 2


def test_broadcast_delta_suppresses_empty_delta_and_does_not_increment_seq(monkeypatch):
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}
    q = sse_service.register(registry, "cormyr")

    initial_suppressed = sse_service._metrics["sse_delta_suppressed_noop_total"]
    count = sse_service.broadcast_delta("characters", registry, "cormyr", [], [])

    assert count == 0
    assert q.qsize() == 0
    assert sse_service._get_current_seq("characters", "cormyr") == 0
    assert (
        sse_service._metrics["sse_delta_suppressed_noop_total"]
        == initial_suppressed + 1
    )


def test_broadcast_delta_with_only_updates_is_not_suppressed():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}
    q = sse_service.register(registry, "cormyr")

    sse_service.broadcast_delta("characters", registry, "cormyr", [{"id": 1}], [])
    assert q.qsize() == 1


def test_broadcast_delta_with_only_removals_is_not_suppressed():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}
    q = sse_service.register(registry, "cormyr")

    sse_service.broadcast_delta("characters", registry, "cormyr", [], [99])
    assert q.qsize() == 1


def test_broadcast_delta_seq_is_strictly_after_snapshot_seq():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}
    sse_service.register(registry, "cormyr")

    sse_service.broadcast_snapshot("characters", registry, "cormyr", {})
    sse_service.broadcast_delta("characters", registry, "cormyr", [{"id": 1}], [])

    assert sse_service._get_current_seq("characters", "cormyr") == 2


# ── Metrics tests ─────────────────────────────────────────────────────────────


def test_get_metrics_returns_expected_keys():
    metrics = sse_service.get_metrics()
    expected_keys = {
        "sse_snapshot_sent_total",
        "sse_delta_sent_total",
        "sse_delta_suppressed_noop_total",
        "sse_clients_connected",
        "sse_queue_evictions_total",
        "sse_reconnects_total",
    }
    assert expected_keys.issubset(set(metrics.keys()))


def test_get_metrics_clients_connected_reflects_live_queue_sets():
    initial = sse_service.get_metrics()["sse_clients_connected"]

    q1 = sse_service.register(
        sse_service.character_queues, list(sse_service.character_queues)[0]
    )
    assert sse_service.get_metrics()["sse_clients_connected"] == initial + 1

    sse_service.unregister(
        sse_service.character_queues, list(sse_service.character_queues)[0], q1
    )
    assert sse_service.get_metrics()["sse_clients_connected"] == initial


def test_broadcast_eviction_increments_eviction_metric():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}
    q_full = asyncio.Queue(maxsize=1)
    q_full.put_nowait("full")
    registry["cormyr"].add(q_full)

    before = sse_service._metrics["sse_queue_evictions_total"]
    sse_service.broadcast(registry, "cormyr", "event: test\ndata: {}\n\n")
    assert sse_service._metrics["sse_queue_evictions_total"] == before + 1


def test_record_reconnect_increments_counter():
    before = sse_service._metrics["sse_reconnects_total"]
    sse_service.record_reconnect()
    assert sse_service._metrics["sse_reconnects_total"] == before + 1


def test_broadcast_snapshot_increments_snapshot_sent_metric():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}
    sse_service.register(registry, "cormyr")
    sse_service.register(registry, "cormyr")

    before = sse_service._metrics["sse_snapshot_sent_total"]
    sse_service.broadcast_snapshot("characters", registry, "cormyr", {})
    assert sse_service._metrics["sse_snapshot_sent_total"] == before + 2


def test_broadcast_delta_increments_delta_sent_metric():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}
    sse_service.register(registry, "cormyr")

    before = sse_service._metrics["sse_delta_sent_total"]
    sse_service.broadcast_delta("characters", registry, "cormyr", [{"id": 1}], [])
    assert sse_service._metrics["sse_delta_sent_total"] == before + 1


# ── epoch attached to all broadcast events ────────────────────────────────────


def test_epoch_present_in_broadcast_snapshot_payload():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}
    q = sse_service.register(registry, "cormyr")
    sse_service.broadcast_snapshot("characters", registry, "cormyr", {})
    payload = json.loads(q.get_nowait().split("data: ", 1)[1].strip())
    assert payload["epoch"] == sse_service.STREAM_EPOCH


def test_epoch_present_in_broadcast_delta_payload():
    registry: dict[str, set[asyncio.Queue]] = {"cormyr": set()}
    q = sse_service.register(registry, "cormyr")
    sse_service.broadcast_delta("characters", registry, "cormyr", [{"id": 1}], [])
    payload = json.loads(q.get_nowait().split("data: ", 1)[1].strip())
    assert payload["epoch"] == sse_service.STREAM_EPOCH


def test_epoch_present_in_make_snapshot_envelope():
    msg = sse_service.make_snapshot_envelope("characters", "cormyr", {})
    payload = json.loads(msg.split("data: ", 1)[1].strip())
    assert payload["epoch"] == sse_service.STREAM_EPOCH
