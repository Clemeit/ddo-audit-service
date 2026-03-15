import json
import os
import time
import uuid
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from utils.access_log import (
    _env_bool,
    _env_float,
    _env_int,
    _safe_int,
    build_access_event,
    dumps_event,
    get_client_ip,
    get_request_id,
    monotonic_duration_ms,
    monotonic_start_ns,
    response_size_bytes,
    should_log,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    *,
    headers=None,
    ip="127.0.0.1",
    method="GET",
    path="/test",
    query_string="",
    route_path=None
):
    """Lightweight request stub for access-log tests."""
    if headers is None:
        headers = {}
    req = SimpleNamespace(
        headers=headers,
        ip=ip,
        method=method,
        path=path,
        query_string=query_string,
    )
    if route_path is not None:
        req.route = SimpleNamespace(path=route_path)
    else:
        req.route = None
    return req


def _make_response(*, status=200, headers=None, body=None):
    if headers is None:
        headers = {}
    return SimpleNamespace(status=status, headers=headers, body=body)


# ===========================================================================
# _env_bool
# ===========================================================================


class TestEnvBool:
    def test_returns_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("MY_TEST_BOOL", raising=False)
        assert _env_bool("MY_TEST_BOOL", True) is True
        assert _env_bool("MY_TEST_BOOL", False) is False

    @pytest.mark.parametrize(
        "raw", ["1", "true", "True", "TRUE", "yes", "on", " yes ", " ON "]
    )
    def test_truthy_values(self, monkeypatch, raw):
        monkeypatch.setenv("MY_TEST_BOOL", raw)
        assert _env_bool("MY_TEST_BOOL", False) is True

    @pytest.mark.parametrize("raw", ["0", "false", "no", "off", "", "random"])
    def test_falsy_values(self, monkeypatch, raw):
        monkeypatch.setenv("MY_TEST_BOOL", raw)
        assert _env_bool("MY_TEST_BOOL", True) is False


# ===========================================================================
# _env_float
# ===========================================================================


class TestEnvFloat:
    def test_returns_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("MY_TEST_FLOAT", raising=False)
        assert _env_float("MY_TEST_FLOAT", 3.14) == 3.14

    def test_valid_float(self, monkeypatch):
        monkeypatch.setenv("MY_TEST_FLOAT", "0.75")
        assert _env_float("MY_TEST_FLOAT", 0.0) == 0.75

    def test_integer_string(self, monkeypatch):
        monkeypatch.setenv("MY_TEST_FLOAT", "5")
        assert _env_float("MY_TEST_FLOAT", 0.0) == 5.0

    def test_invalid_returns_default(self, monkeypatch):
        monkeypatch.setenv("MY_TEST_FLOAT", "not_a_number")
        assert _env_float("MY_TEST_FLOAT", 1.5) == 1.5


# ===========================================================================
# _env_int
# ===========================================================================


class TestEnvInt:
    def test_returns_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("MY_TEST_INT", raising=False)
        assert _env_int("MY_TEST_INT", 42) == 42

    def test_valid_int(self, monkeypatch):
        monkeypatch.setenv("MY_TEST_INT", "100")
        assert _env_int("MY_TEST_INT", 0) == 100

    def test_negative_int(self, monkeypatch):
        monkeypatch.setenv("MY_TEST_INT", "-7")
        assert _env_int("MY_TEST_INT", 0) == -7

    def test_float_string_returns_default(self, monkeypatch):
        monkeypatch.setenv("MY_TEST_INT", "3.14")
        assert _env_int("MY_TEST_INT", 99) == 99

    def test_invalid_returns_default(self, monkeypatch):
        monkeypatch.setenv("MY_TEST_INT", "abc")
        assert _env_int("MY_TEST_INT", 10) == 10


# ===========================================================================
# _safe_int
# ===========================================================================


class TestSafeInt:
    def test_none_returns_none(self):
        assert _safe_int(None) is None

    def test_int_passthrough(self):
        assert _safe_int(42) == 42

    def test_string_int(self):
        assert _safe_int("123") == 123

    def test_float_truncates(self):
        assert _safe_int(3.9) == 3

    def test_non_numeric_string_returns_none(self):
        assert _safe_int("abc") is None

    def test_empty_string_returns_none(self):
        assert _safe_int("") is None


# ===========================================================================
# get_request_id
# ===========================================================================


class TestGetRequestId:
    def test_uses_x_request_id_header(self):
        req = _make_request(headers={"x-request-id": "abc-123"})
        assert get_request_id(req) == "abc-123"

    def test_uses_x_correlation_id_header(self):
        req = _make_request(headers={"x-correlation-id": "corr-456"})
        assert get_request_id(req) == "corr-456"

    def test_prefers_x_request_id_over_correlation(self):
        req = _make_request(headers={"x-request-id": "req", "x-correlation-id": "corr"})
        assert get_request_id(req) == "req"

    def test_generates_uuid_when_no_header(self):
        req = _make_request(headers={})
        rid = get_request_id(req)
        # uuid4().hex is 32 hex characters
        assert len(rid) == 32
        int(rid, 16)  # should not raise

    def test_truncates_long_header(self):
        req = _make_request(headers={"x-request-id": "x" * 200})
        assert len(get_request_id(req)) == 128

    def test_strips_whitespace(self):
        req = _make_request(headers={"x-request-id": "  trimmed  "})
        assert get_request_id(req) == "trimmed"


# ===========================================================================
# get_client_ip
# ===========================================================================


class TestGetClientIp:
    def test_x_real_ip(self):
        req = _make_request(headers={"x-real-ip": "1.2.3.4"})
        assert get_client_ip(req) == "1.2.3.4"

    def test_x_forwarded_for_single(self):
        req = _make_request(headers={"x-forwarded-for": "5.6.7.8"})
        assert get_client_ip(req) == "5.6.7.8"

    def test_x_forwarded_for_multiple_returns_first(self):
        req = _make_request(headers={"x-forwarded-for": "10.0.0.1, 10.0.0.2, 10.0.0.3"})
        assert get_client_ip(req) == "10.0.0.1"

    def test_x_real_ip_preferred_over_forwarded_for(self):
        req = _make_request(
            headers={
                "x-real-ip": "1.1.1.1",
                "x-forwarded-for": "2.2.2.2",
            }
        )
        assert get_client_ip(req) == "1.1.1.1"

    def test_falls_back_to_request_ip(self):
        req = _make_request(headers={}, ip="192.168.1.1")
        assert get_client_ip(req) == "192.168.1.1"

    def test_truncates_long_x_real_ip(self):
        req = _make_request(headers={"x-real-ip": "a" * 100})
        assert len(get_client_ip(req)) == 64

    def test_truncates_long_x_forwarded_for(self):
        req = _make_request(headers={"x-forwarded-for": "b" * 100})
        assert len(get_client_ip(req)) == 64

    def test_returns_none_when_ip_raises(self):
        req = _make_request(headers={})
        # Make .ip raise
        del req.ip
        req_obj = SimpleNamespace(headers={})
        # Accessing .ip raises AttributeError
        assert get_client_ip(req_obj) is None

    def test_x_forwarded_for_empty_first_entry(self):
        req = _make_request(headers={"x-forwarded-for": ", 10.0.0.2"})
        assert get_client_ip(req) is None


# ===========================================================================
# response_size_bytes
# ===========================================================================


class TestResponseSizeBytes:
    def test_from_content_length_header(self):
        resp = _make_response(headers={"content-length": "512"})
        assert response_size_bytes(resp) == 512

    def test_from_body_length(self):
        resp = _make_response(body=b"hello world")
        assert response_size_bytes(resp) == 11

    def test_content_length_preferred_over_body(self):
        resp = _make_response(headers={"content-length": "999"}, body=b"short")
        assert response_size_bytes(resp) == 999

    def test_none_body_returns_none(self):
        resp = _make_response(body=None)
        assert response_size_bytes(resp) is None

    def test_zero_content_length(self):
        resp = _make_response(headers={"content-length": "0"})
        assert response_size_bytes(resp) == 0

    def test_no_headers_no_body(self):
        resp = SimpleNamespace(body=None)
        assert response_size_bytes(resp) is None

    def test_invalid_content_length_falls_back_to_body(self):
        resp = _make_response(headers={"content-length": "not_a_number"}, body=b"data")
        assert response_size_bytes(resp) == 4

    def test_empty_body(self):
        resp = _make_response(body=b"")
        assert response_size_bytes(resp) == 0


# ===========================================================================
# should_log
# ===========================================================================


class TestShouldLog:
    def test_disabled_returns_false(self, monkeypatch):
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_ENABLED", False)
        assert should_log(200, 0) is False

    def test_error_status_always_logged(self, monkeypatch):
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_ENABLED", True)
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_SAMPLE_RATE", 0.0)
        assert should_log(400, 0) is True
        assert should_log(500, 0) is True

    def test_slow_request_always_logged(self, monkeypatch):
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_ENABLED", True)
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_SLOW_MS", 500)
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_SAMPLE_RATE", 0.0)
        assert should_log(200, 500) is True
        assert should_log(200, 1000) is True

    def test_below_slow_threshold_not_always_logged(self, monkeypatch):
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_ENABLED", True)
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_SLOW_MS", 500)
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_SAMPLE_RATE", 0.0)
        assert should_log(200, 499) is False

    def test_full_sample_rate_always_logs(self, monkeypatch):
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_ENABLED", True)
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_SLOW_MS", 10000)
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_SAMPLE_RATE", 1.0)
        assert should_log(200, 0) is True

    def test_zero_sample_rate_skips(self, monkeypatch):
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_ENABLED", True)
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_SLOW_MS", 10000)
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_SAMPLE_RATE", 0.0)
        assert should_log(200, 0) is False

    def test_partial_sample_rate(self, monkeypatch):
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_ENABLED", True)
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_SLOW_MS", 10000)
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_SAMPLE_RATE", 0.5)
        # With a fixed random seed we can verify sampling behavior
        with patch("utils.access_log.random.random", return_value=0.3):
            assert should_log(200, 0) is True
        with patch("utils.access_log.random.random", return_value=0.7):
            assert should_log(200, 0) is False

    def test_status_399_not_error(self, monkeypatch):
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_ENABLED", True)
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_SLOW_MS", 10000)
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_SAMPLE_RATE", 0.0)
        assert should_log(399, 0) is False


# ===========================================================================
# build_access_event
# ===========================================================================


class TestBuildAccessEvent:
    def test_basic_fields(self):
        req = _make_request(
            method="POST",
            path="/api/v1/test",
            headers={"user-agent": "TestAgent/1.0", "host": "example.com"},
            ip="10.0.0.1",
            route_path="/api/v1/test",
        )
        resp = _make_response(status=200, body=b"OK")
        event = build_access_event(req, resp, request_id="req-1", duration_ms=42)

        assert event["type"] == "access"
        assert event["request_id"] == "req-1"
        assert event["method"] == "POST"
        assert event["path"] == "/api/v1/test"
        assert event["route"] == "/api/v1/test"
        assert event["status"] == 200
        assert event["duration_ms"] == 42
        assert event["user_agent"] == "TestAgent/1.0"
        assert event["host"] == "example.com"
        assert event["ip"] == "10.0.0.1"
        assert "ts" in event

    def test_route_none_for_404(self):
        req = _make_request(path="/unknown")
        resp = _make_response(status=404)
        event = build_access_event(req, resp, request_id="r", duration_ms=1)
        assert event["route"] is None

    def test_query_string_excluded_by_default(self, monkeypatch):
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_INCLUDE_QUERY", False)
        req = _make_request(query_string="key=value")
        resp = _make_response()
        event = build_access_event(req, resp, request_id="r", duration_ms=0)
        assert "query_string" not in event

    def test_query_string_included_when_enabled(self, monkeypatch):
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_INCLUDE_QUERY", True)
        req = _make_request(query_string="key=value")
        resp = _make_response()
        event = build_access_event(req, resp, request_id="r", duration_ms=0)
        assert event["query_string"] == "key=value"

    def test_query_string_truncated(self, monkeypatch):
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_INCLUDE_QUERY", True)
        req = _make_request(query_string="x" * 5000)
        resp = _make_response()
        event = build_access_event(req, resp, request_id="r", duration_ms=0)
        assert len(event["query_string"]) == 2048

    def test_empty_query_string_not_included(self, monkeypatch):
        monkeypatch.setattr("utils.access_log.ACCESS_LOG_INCLUDE_QUERY", True)
        req = _make_request(query_string="")
        resp = _make_response()
        event = build_access_event(req, resp, request_id="r", duration_ms=0)
        assert "query_string" not in event

    def test_bytes_in_from_content_length(self):
        req = _make_request(headers={"content-length": "256"})
        resp = _make_response()
        event = build_access_event(req, resp, request_id="r", duration_ms=0)
        assert event["bytes_in"] == 256

    def test_bytes_out_from_response(self):
        req = _make_request()
        resp = _make_response(body=b"response body")
        event = build_access_event(req, resp, request_id="r", duration_ms=0)
        assert event["bytes_out"] == 13

    def test_referrer_header(self):
        req = _make_request(headers={"referer": "https://example.com"})
        resp = _make_response()
        event = build_access_event(req, resp, request_id="r", duration_ms=0)
        assert event["referrer"] == "https://example.com"

    def test_remote_ip(self):
        req = _make_request(ip="172.16.0.1")
        resp = _make_response()
        event = build_access_event(req, resp, request_id="r", duration_ms=0)
        assert event["remote_ip"] == "172.16.0.1"


# ===========================================================================
# monotonic_start_ns / monotonic_duration_ms
# ===========================================================================


class TestMonotonic:
    def test_start_ns_returns_int(self):
        val = monotonic_start_ns()
        assert isinstance(val, int)
        assert val > 0

    def test_duration_ms_reasonable(self):
        start = monotonic_start_ns()
        # Duration should be near-zero since no sleep
        dur = monotonic_duration_ms(start)
        assert isinstance(dur, int)
        assert dur >= 0
        assert dur < 1000  # should be well under 1 second

    def test_duration_ms_with_invalid_input(self):
        # Very large start_ns could theoretically cause issues, but the function
        # catches exceptions and returns 0
        assert monotonic_duration_ms(0) >= 0


# ===========================================================================
# dumps_event
# ===========================================================================


class TestDumpsEvent:
    def test_compact_json(self):
        event = {"type": "access", "status": 200}
        result = dumps_event(event)
        assert result == '{"type":"access","status":200}'

    def test_no_spaces(self):
        event = {"a": 1, "b": "hello"}
        result = dumps_event(event)
        assert " " not in result.replace("hello", "")

    def test_unicode_preserved(self):
        event = {"name": "café"}
        result = dumps_event(event)
        assert "café" in result

    def test_round_trip(self):
        event = {"type": "access", "status": 200, "path": "/test"}
        result = dumps_event(event)
        parsed = json.loads(result)
        assert parsed == event
