#!/usr/bin/env python3
"""
Multi-scenario performance test suite for DDO Audit API.

Runs configurable scenarios against the production or staging API
and optionally compares results with a saved baseline.

Usage:
    python perf_test_suite.py --tag before --output baseline.json
    python perf_test_suite.py --tag after --compare-with baseline.json

    # Only run specific scenarios:
    python perf_test_suite.py --scenarios server-info,characters-full

    # With auth scenarios:
    python perf_test_suite.py --access-token <jwt> --refresh-token <tok> --character-name Clemeit
"""

import sys
import os
import argparse
import time
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

# Allow importing from sibling script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from perf_test_endpoint import RequestResult, analyze_results

import requests as req_lib

DEFAULT_BASE_URL = "https://api.ddoaudit.com"
DEFAULT_CONCURRENCY = 20
DEFAULT_REQUESTS = 200
DEFAULT_TIMEOUT = 30

SCENARIOS = [
    {
        "name": "server-info",
        "description": "GET /v1/game/server-info — pure Redis read baseline",
        "method": "GET",
        "path": "/v1/game/server-info",
        "requires": [],
    },
    {
        "name": "characters-summary",
        "description": "GET /v1/characters/summary — pipeline Redis read",
        "method": "GET",
        "path": "/v1/characters/summary",
        "requires": [],
    },
    {
        "name": "characters-full",
        "description": "GET /v1/characters — full dataset Redis read",
        "method": "GET",
        "path": "/v1/characters",
        "requires": [],
    },
    {
        "name": "user-profile",
        "description": "GET /v1/user/profile — JWT middleware + Postgres",
        "method": "GET",
        "path": "/v1/user/profile",
        "requires": ["access_token"],
    },
    {
        "name": "auth-refresh",
        "description": "POST /v1/auth/refresh — Postgres session rotation (sequential)",
        "method": "POST",
        "path": "/v1/auth/refresh",
        "requires": ["refresh_token"],
        "serial": True,
    },
    {
        "name": "character-by-name",
        "description": "GET /v1/characters/by-name/<name> — full-dataset scan",
        "method": "GET",
        "path": "/v1/characters/by-name/{character_name}",
        "requires": ["character_name"],
    },
]


class ScenarioTester:
    """Runs a single HTTP scenario repeatedly under concurrency."""

    def __init__(
        self,
        base_url: str,
        method: str,
        path: str,
        headers: dict | None = None,
        json_body: dict | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.url = f"{base_url.rstrip('/')}{path}"
        self.method = method.upper()
        self.json_body = json_body
        self.timeout = timeout
        # Keep one session per worker thread; requests.Session is not thread-safe.
        self._base_headers = {
            "User-Agent": "DDO-Audit-PerfSuite/1.0",
            **(headers or {}),
        }
        self._thread_local = threading.local()
        self._sessions: list[req_lib.Session] = []
        self._sessions_lock = threading.Lock()

    def _get_thread_session(self) -> req_lib.Session:
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = req_lib.Session()
            session.headers.update(self._base_headers)
            self._thread_local.session = session
            with self._sessions_lock:
                self._sessions.append(session)
        return session

    def _close_sessions(self) -> None:
        for session in self._sessions:
            session.close()
        self._sessions.clear()

    def _single_request(self) -> RequestResult:
        start = time.time()
        session = self._get_thread_session()
        try:
            if self.method == "POST":
                resp = session.post(self.url, json=self.json_body, timeout=self.timeout)
            else:
                resp = session.get(self.url, timeout=self.timeout)
            elapsed = time.time() - start
            return RequestResult(
                success=200 <= resp.status_code < 300,
                response_time=elapsed,
                status_code=resp.status_code,
                response_size=len(resp.content) if resp.content else 0,
            )
        except req_lib.RequestException as exc:
            return RequestResult(
                success=False,
                response_time=time.time() - start,
                error_message=str(exc),
            )

    def run(self, count: int, concurrency: int) -> list[RequestResult]:
        print(f"  {count} requests, concurrency {concurrency} -> {self.url}")
        results: list[RequestResult] = []
        try:
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                futures = [pool.submit(self._single_request) for _ in range(count)]
                for future in as_completed(futures):
                    results.append(future.result())
        finally:
            self._close_sessions()
        return results


def _run_refresh_scenario(args) -> dict | None:
    """Special handler: refresh rotates the token cookie, so run sequentially."""
    if not args.refresh_token:
        print("  SKIP (needs --refresh-token)")
        return None

    url = f"{args.base_url.rstrip('/')}/v1/auth/refresh"
    session = req_lib.Session()
    session.headers.update({"User-Agent": "DDO-Audit-PerfSuite/1.0"})

    current_token = args.refresh_token
    # Limit refresh count: each call rotates the token and must be serial
    count = min(args.requests, 50)
    print(f"  {count} sequential requests (token rotation) -> {url}")

    results: list[RequestResult] = []
    for _ in range(count):
        start = time.time()
        try:
            resp = session.post(
                url,
                cookies={"refresh_token": current_token},
                timeout=args.timeout,
            )
            elapsed = time.time() - start
            result = RequestResult(
                success=200 <= resp.status_code < 300,
                response_time=elapsed,
                status_code=resp.status_code,
                response_size=len(resp.content) if resp.content else 0,
            )
            results.append(result)

            if result.success:
                new_token = resp.cookies.get("refresh_token")
                if new_token:
                    current_token = new_token
                else:
                    break
            else:
                break
        except req_lib.RequestException as exc:
            results.append(
                RequestResult(
                    success=False,
                    response_time=time.time() - start,
                    error_message=str(exc),
                )
            )
            break

    return analyze_results(results) if results else None


def run_scenario(scenario: dict, args) -> dict | None:
    """Run a single named scenario. Returns analysis dict or None if skipped."""
    for req in scenario["requires"]:
        if not getattr(args, req, None):
            print(f"  SKIP (needs --{req.replace('_', '-')})")
            return None

    # Special serial handler for refresh
    if scenario["name"] == "auth-refresh":
        return _run_refresh_scenario(args)

    headers: dict[str, str] = {}
    path = scenario["path"]

    if "access_token" in scenario["requires"]:
        headers["Authorization"] = f"Bearer {args.access_token}"

    if "{character_name}" in path:
        path = path.replace("{character_name}", args.character_name)

    tester = ScenarioTester(
        base_url=args.base_url,
        method=scenario["method"],
        path=path,
        headers=headers,
        timeout=args.timeout,
    )

    results = tester.run(count=args.requests, concurrency=args.concurrency)
    return analyze_results(results)


def _fmt_change(cur: float, base: float) -> str:
    if base > 0:
        pct = ((cur - base) / base) * 100
        return f"{pct:+.1f}%"
    return "n/a"


def print_comparison(current: dict, baseline: dict, tag: str, baseline_tag: str):
    """Print side-by-side comparison table for two runs."""
    print("\n" + "=" * 80)
    print(f"COMPARISON: {baseline_tag} -> {tag}")
    print("=" * 80)
    print(
        f"{'Scenario':<25} {'Metric':<10} {'Baseline':>10} {'Current':>10} {'Change':>10}"
    )
    print("-" * 80)

    for name in current:
        cur = current[name]
        base = baseline.get(name)
        if not cur or not base:
            continue
        cur_s = cur.get("response_time_stats", {})
        base_s = base.get("response_time_stats", {})
        if not cur_s or not base_s:
            continue

        for metric in ("mean", "median", "p95"):
            c = cur_s.get(metric, 0)
            b = base_s.get(metric, 0)
            print(
                f"{name:<25} {metric:<10} {b:>9.3f}s {c:>9.3f}s {_fmt_change(c, b):>10}"
            )

    print("=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description="Multi-scenario performance test suite for DDO Audit API",
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base URL")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help="Concurrent threads per scenario (default: 20)",
    )
    parser.add_argument(
        "--requests",
        type=int,
        default=DEFAULT_REQUESTS,
        help="Requests per scenario (default: 200)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="Per-request timeout in seconds (default: 30)",
    )
    parser.add_argument("--access-token", help="JWT access token for auth scenarios")
    parser.add_argument(
        "--refresh-token", help="Refresh token for auth/refresh scenario"
    )
    parser.add_argument("--character-name", help="Character name for by-name scenario")
    parser.add_argument(
        "--tag", default="run", help="Label for this run (e.g. 'before', 'after')"
    )
    parser.add_argument("--output", help="Save results to this JSON file")
    parser.add_argument("--compare-with", help="Baseline JSON file to compare against")
    parser.add_argument(
        "--scenarios",
        help="Comma-separated scenario names to run (default: all)",
    )
    args = parser.parse_args()

    # Load baseline if comparing
    baseline_data = None
    baseline_tag = None
    if args.compare_with:
        with open(args.compare_with) as f:
            saved = json.load(f)
        baseline_data = saved.get("scenarios", {})
        baseline_tag = saved.get("tag", "baseline")

    # Filter scenarios
    selected = SCENARIOS
    if args.scenarios:
        names = {s.strip() for s in args.scenarios.split(",")}
        selected = [s for s in SCENARIOS if s["name"] in names]

    print(f"DDO Audit Performance Suite  tag={args.tag}")
    print(f"Base URL: {args.base_url}")
    print(f"Concurrency: {args.concurrency}  Requests/scenario: {args.requests}")
    print()

    all_results: dict[str, dict | None] = {}
    for scenario in selected:
        print(f"[{scenario['name']}] {scenario['description']}")
        analysis = run_scenario(scenario, args)
        all_results[scenario["name"]] = analysis

        if analysis:
            stats = analysis.get("response_time_stats", {})
            ok_rate = analysis.get("success_rate", 0)
            print(
                f"  -> mean={stats.get('mean', 0):.3f}s  "
                f"p95={stats.get('p95', 0):.3f}s  "
                f"ok={ok_rate:.0f}%"
            )
        print()

    # Save results
    output_data = {
        "tag": args.tag,
        "base_url": args.base_url,
        "concurrency": args.concurrency,
        "requests_per_scenario": args.requests,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "scenarios": all_results,
    }

    if args.output:
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        print(f"Results saved to {args.output}")

    if baseline_data:
        print_comparison(all_results, baseline_data, args.tag, baseline_tag)


if __name__ == "__main__":
    main()
