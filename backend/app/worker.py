"""Background worker process: notification outbox delivery and invoice-file virus scans.

    python -m app.worker              # run until stopped (Ctrl+C / SIGTERM)
    python -m app.worker --once       # one batch of each stage, then exit (cron / CI)
    python -m app.worker --interval 5 # idle wait between empty cycles, in seconds

Uses the runtime database role with the explicit RLS bypass (like the SLA sweep), because it claims
due rows across tenants; every query it runs filters by the event's org_id. Any number of workers can
run concurrently: rows are claimed with FOR UPDATE SKIP LOCKED.
"""

import argparse
import asyncio
import contextlib
import json
import logging
import signal
import sys

from prometheus_client import start_http_server

from app.background import background_loop, run_cycle
from app.core import metrics
from app.core.config import get_settings
from app.core.db import dispose_engine
from app.core.logging import configure_logging


async def _run(once: bool, interval: float) -> int:
    try:
        if once:
            print(json.dumps(await run_cycle()))
            return 0
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            # Windows has no add_signal_handler; Ctrl+C raises KeyboardInterrupt there instead.
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, stop.set)
        settings = get_settings()
        if settings.otel_enabled:
            from app.core.tracing import setup_worker_tracing  # noqa: PLC0415 - optional, off by default

            setup_worker_tracing(settings)
        port = settings.worker_metrics_port
        if port:
            # Prometheus scrapes the worker's own counters (cycles, deliveries, scans) here.
            start_http_server(port, registry=metrics.REGISTRY)
        logging.getLogger("procurax.worker").info(
            "worker_started", extra={"interval": interval, "metrics_port": port}
        )
        await background_loop(interval, stop=stop)
        return 0
    finally:
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description="ProcuraX background worker")
    parser.add_argument("--once", action="store_true", help="process one batch per stage and exit")
    parser.add_argument("--interval", type=float, default=5.0, help="seconds to wait after an idle cycle")
    args = parser.parse_args()
    configure_logging()
    try:
        return asyncio.run(_run(args.once, args.interval))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
