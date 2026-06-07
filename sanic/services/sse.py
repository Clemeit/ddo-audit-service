import asyncio

from constants.server import SSE_SERVER_NAMES_LOWERCASE

character_queues: dict[str, set[asyncio.Queue]] = {
    s: set() for s in SSE_SERVER_NAMES_LOWERCASE
}
lfm_queues: dict[str, set[asyncio.Queue]] = {
    s: set() for s in SSE_SERVER_NAMES_LOWERCASE
}


def format_sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


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
    notified = 0
    for q in set(registry[server_name]):
        try:
            q.put_nowait(sse_message)
            notified += 1
        except asyncio.QueueFull:
            unregister(registry, server_name, q)
    return notified
