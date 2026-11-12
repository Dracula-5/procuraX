"""Notification worker process.

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

from app.core.db import dispose_engine
from app.core.logging import configure_logging
from app.modules.notifications.worker import process_once, worker_loop


async def _run(once: bool, interval: float) -> int:
    try:
        if once:
            print(json.dumps(await process_once()))
            return 0
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            # Windows has no add_signal_handler; Ctrl+C raises KeyboardInterrupt there instead.
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, stop.set)
        logging.getLogger("procurax.notifications").info("worker_started", extra={"interval": interval})
        await worker_loop(interval, stop=stop)
        return 0
    finally:
        await dispose_engine()


def main() -> int:
    parser = argparse.ArgumentParser(description="ProcuraX notification worker")
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
