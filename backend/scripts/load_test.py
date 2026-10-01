"""Small standard-library HTTP load driver; results are measurements, never capacity promises."""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def _worker(
    worker_id: int,
    start: threading.Barrier,
    end_time: float,
    base_url: str,
    requests: list[dict[str, Any]],
    token: str | None,
    timeout: float,
) -> list[tuple[float, int]]:
    samples: list[tuple[float, int]] = []
    start.wait()
    sequence = worker_id
    while time.perf_counter() < end_time:
        scenario = requests[sequence % len(requests)]
        sequence += 1
        body = scenario.get("json")
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(  # noqa: S310 - run() restricts base URL to HTTP(S) origin and path
            base_url.rstrip("/") + scenario["path"],
            data=data,
            headers=headers,
            method=scenario.get("method", "GET").upper(),
        )
        began = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL validated by run()
                status = response.status
                response.read()
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.read()
        except Exception:
            status = 0
        samples.append(((time.perf_counter() - began) * 1000, status))
    return samples


def run(
    base_url: str,
    scenarios: list[dict[str, Any]],
    concurrency: int,
    duration_seconds: float,
    token: str | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    base = urlsplit(base_url)
    if base.scheme not in {"http", "https"} or not base.hostname or base.username or base.password:
        raise ValueError("base_url must be an HTTP(S) URL with a hostname and no embedded credentials")
    for scenario in scenarios:
        path = urlsplit(str(scenario.get("path", "")))
        if not str(scenario.get("path", "")).startswith("/") or path.scheme or path.netloc:
            raise ValueError("Each request plan path must be an absolute path on the configured base URL")
    barrier = threading.Barrier(concurrency)
    end_time = time.perf_counter() + duration_seconds
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [
            pool.submit(_worker, worker_id, barrier, end_time, base_url, scenarios, token, timeout)
            for worker_id in range(concurrency)
        ]
        samples = [sample for future in futures for sample in future.result()]
    elapsed = time.perf_counter() - started
    latencies = [latency for latency, _ in samples]
    statuses = Counter(str(status) for _, status in samples)
    successes = sum(status in (200, 201, 202, 204) for _, status in samples)
    return {
        "target": base_url,
        "concurrency": concurrency,
        "duration_seconds": round(elapsed, 3),
        "requests": len(samples),
        "requests_per_second": round(len(samples) / elapsed, 2) if elapsed else 0,
        "success_rate": round(successes / len(samples), 4) if samples else 0,
        "latency_ms": {
            "p50": round(percentile(latencies, 0.50), 2),
            "p95": round(percentile(latencies, 0.95), 2),
            "p99": round(percentile(latencies, 0.99), 2),
            "mean": round(statistics.fmean(latencies), 2) if latencies else 0,
        },
        "status_counts": dict(statuses),
        "non_success_count": len(samples) - successes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="API origin, e.g. http://127.0.0.1:8000")
    parser.add_argument("--plan", type=Path, required=True, help="JSON list of method/path/json scenarios")
    parser.add_argument("--concurrency", type=int, choices=(1, 5, 10, 50, 100, 250, 500), default=1)
    parser.add_argument("--duration", type=float, default=10, help="Measurement duration in seconds")
    parser.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args()
    if args.duration <= 0 or args.timeout <= 0:
        parser.error("duration and timeout must be positive")
    scenarios = json.loads(args.plan.read_text(encoding="utf-8"))
    if (
        not isinstance(scenarios, list)
        or not scenarios
        or any(not item.get("path", "").startswith("/") for item in scenarios)
    ):
        parser.error("plan must be a non-empty JSON list; each path must start with '/'")
    token = os.environ.get("PROCURAX_LOAD_TOKEN")
    print(
        json.dumps(run(args.url, scenarios, args.concurrency, args.duration, token, args.timeout), indent=2)
    )


if __name__ == "__main__":
    main()
