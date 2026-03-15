import asyncio
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


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
    ):
        if headers is None:
            headers = {}
        if body is None:
            body = b"" if json_body is None else json.dumps(json_body).encode("utf-8")
        if ctx is None:
            ctx = SimpleNamespace()

        return SimpleNamespace(
            method=method,
            path=path,
            json=json_body,
            headers=headers,
            body=body,
            ctx=ctx,
            ip=ip,
        )

    return _make_request
