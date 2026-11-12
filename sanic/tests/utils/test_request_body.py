import gzip
import json
from types import SimpleNamespace

import pytest
from utils.request_body import (
    RequestBodyError,
    RequestBodyTooLarge,
    inflate_gzip,
    read_json_body,
)


def _request(body: bytes, encoding: str | None = None):
    headers = {} if encoding is None else {"Content-Encoding": encoding}
    return SimpleNamespace(body=body, headers=headers)


def test_reads_plain_json():
    assert read_json_body(_request(b'{"a": 1}')) == {"a": 1}


def test_reads_gzipped_json():
    body = gzip.compress(json.dumps({"a": [1, 2, 3]}).encode("utf-8"))
    assert read_json_body(_request(body, "gzip")) == {"a": [1, 2, 3]}


def test_content_encoding_is_case_insensitive():
    request = SimpleNamespace(
        body=gzip.compress(b'{"a": 1}'), headers={"content-encoding": "GZIP"}
    )
    assert read_json_body(request) == {"a": 1}


def test_rejects_unknown_encoding():
    with pytest.raises(RequestBodyError):
        read_json_body(_request(b"{}", "br"))


def test_rejects_corrupt_gzip():
    with pytest.raises(RequestBodyError):
        read_json_body(_request(b"definitely not gzip", "gzip"))


def test_rejects_truncated_gzip():
    body = gzip.compress(b'{"a": "' + b"x" * 5000 + b'"}')
    with pytest.raises(RequestBodyError):
        read_json_body(_request(body[: len(body) // 2], "gzip"))


def test_rejects_invalid_json():
    with pytest.raises(RequestBodyError):
        read_json_body(_request(b"{not json"))


def test_rejects_an_empty_body():
    with pytest.raises(RequestBodyError):
        read_json_body(_request(b""))


def test_gzip_bomb_is_refused_without_inflating_it_all():
    bomb = gzip.compress(b"0" * (10 * 1024 * 1024))  # tiny on the wire, 10 MB inflated
    with pytest.raises(RequestBodyTooLarge):
        read_json_body(_request(bomb, "gzip"), max_bytes=1024 * 1024)


def test_exactly_the_limit_is_allowed():
    data = b"[" + b"1," * 99 + b"1]"
    assert len(inflate_gzip(gzip.compress(data), max_bytes=len(data))) == len(data)


def test_plain_body_over_the_limit_is_refused():
    with pytest.raises(RequestBodyTooLarge):
        read_json_body(_request(b"[" + b"1," * 1000 + b"1]"), max_bytes=100)
