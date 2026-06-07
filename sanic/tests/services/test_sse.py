import asyncio

import services.sse as sse_service


def test_format_sse_returns_correct_format():
    result = sse_service.format_sse("snapshot", '{"key": "value"}')
    assert result == 'event: snapshot\ndata: {"key": "value"}\n\n'


def test_format_sse_works_for_delta_event():
    result = sse_service.format_sse("delta", "{}")
    assert result == "event: delta\ndata: {}\n\n"


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
