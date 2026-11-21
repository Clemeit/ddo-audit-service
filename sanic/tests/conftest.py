import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


def _amock(fn):
    """Wrap a sync function to return a coroutine for use in monkeypatched async calls."""

    async def wrapper(*a, **kw):
        return fn(*a, **kw)

    return wrapper


# Ensure auth service can import in test context.
# Use a 32+ byte key to satisfy HS256 recommended minimum length.
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-key-32-bytes-minimum")
os.environ.setdefault("API_KEY", "test-api-key")

# Make `sanic/` importable as a package root for modules like `services.auth`.
SANIC_ROOT = Path(__file__).resolve().parents[1]
if str(SANIC_ROOT) not in sys.path:
    sys.path.insert(0, str(SANIC_ROOT))


@pytest.fixture
def run_async():
    def _run(coro):
        return asyncio.run(coro)

    return _run


@pytest.fixture
def response_json():
    def _parse(response):
        return json.loads(response.body)

    return _parse


from sanic.request import Request as _SanicRequest


class FakeRequest(_SanicRequest):
    """Attribute bag that still passes ``isinstance(x, sanic.Request)``.

    sanic-ext's ``@openapi.body`` wrapper locates the request by type, so a plain
    SimpleNamespace is rejected. Instance attributes take precedence over the
    parent's read-only properties/slots so tests can assign anything (``args``...).
    """

    def __init__(self, **attrs):
        self.__dict__.update(attrs)

    def __getattribute__(self, name):
        instance_attrs = object.__getattribute__(self, "__dict__")
        if name in instance_attrs:
            return instance_attrs[name]
        return object.__getattribute__(self, name)

    def __setattr__(self, name, value):
        object.__getattribute__(self, "__dict__")[name] = value


@pytest.fixture
def make_request():
    def _make_request(
        *,
        method="GET",
        path="/",
        json_body=None,
        headers=None,
        body=None,
        ctx=None,
        ip="127.0.0.1",
        cookies=None,
    ):
        if headers is None:
            headers = {}
        if body is None:
            body = b"" if json_body is None else json.dumps(json_body).encode("utf-8")
        if ctx is None:
            ctx = SimpleNamespace()
        if cookies is None:
            cookies = {}

        return FakeRequest(
            method=method,
            path=path,
            json=json_body,
            headers=headers,
            body=body,
            ctx=ctx,
            ip=ip,
            cookies=cookies,
        )

    return _make_request
