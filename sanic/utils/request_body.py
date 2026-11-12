"""Reading a request body that may be gzipped. Sanic does not inflate request bodies by itself, and the
Shard Exchange scans are uploaded gzipped (a few MB of very repetitive JSON). Used by the endpoint that
needs it rather than as global middleware, so no other route changes behaviour."""

import zlib

import orjson
from constants.shard_exchange import MAX_INFLATED_BODY_BYTES


class RequestBodyError(Exception):
    """The body could not be read (bad encoding, corrupt gzip, not JSON). Answer 400."""


class RequestBodyTooLarge(RequestBodyError):
    """The body is bigger than allowed once inflated. Answer 413."""


def _header(request, name: str) -> str:
    wanted = name.lower()
    for key, value in request.headers.items():
        if key.lower() == wanted:
            return value
    return ""


def inflate_gzip(data: bytes, max_bytes: int) -> bytes:
    """Inflates a gzip stream, refusing to produce more than max_bytes."""
    inflater = zlib.decompressobj(wbits=31)  # 16 + 15: gzip header and trailer
    try:
        # Asking for one byte more than allowed tells "exactly the limit" from "over it" without
        # ever holding more than max_bytes + 1 in memory.
        out = inflater.decompress(data, max_bytes + 1)
    except zlib.error as e:
        raise RequestBodyError(f"The gzip body is corrupt: {e}")
    if len(out) > max_bytes or inflater.unconsumed_tail:
        raise RequestBodyTooLarge(f"The body is larger than {max_bytes} bytes once inflated")
    if not inflater.eof:
        raise RequestBodyError("The gzip body is truncated")
    return out


def read_json_body(request, max_bytes: int = MAX_INFLATED_BODY_BYTES):
    """The request body parsed as JSON, inflating it first if Content-Encoding is gzip."""
    raw = request.body or b""
    encoding = _header(request, "Content-Encoding").strip().lower()
    if encoding in ("", "identity"):
        data = raw
        if len(data) > max_bytes:
            raise RequestBodyTooLarge(f"The body is larger than {max_bytes} bytes")
    elif encoding in ("gzip", "x-gzip"):
        data = inflate_gzip(raw, max_bytes)
    else:
        raise RequestBodyError(f"Unsupported Content-Encoding: {encoding}")

    try:
        return orjson.loads(data)
    except orjson.JSONDecodeError:
        raise RequestBodyError("The body is not valid JSON")
