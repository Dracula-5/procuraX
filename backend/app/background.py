"""Background work shared by `python -m app.worker` and the API's optional in-process loop.

One cycle = notification fan-out and delivery, invoice-file virus scans, then the contract expiry and
reminder sweep. Every stage claims rows with FOR UPDATE SKIP LOCKED, so any number of processes can run
cycles at the same time.
"""

import asyncio
import logging

from app.modules.contracts.service import sweep_once as contract_sweep
from app.modules.documents.scanner import scan_once
from app.modules.notifications.worker import process_once

logger = logging.getLogger("procurax.worker")


async def run_cycle() -> dict[str, int]:
    return {**await process_once(), **await scan_once(), **await contract_sweep()}


async def background_loop(interval_seconds: float, *, stop: asyncio.Event | None = None) -> None:
    """Run until cancelled (or `stop` is set). Busy cycles run back to back; idle ones wait."""
    while stop is None or not stop.is_set():
        try:
            counts = await run_cycle()
            if any(v for k, v in counts.items() if k != "planned"):
                logger.info("worker_cycle", extra=counts)
                continue
        except asyncio.CancelledError:
            raise
        except Exception:  # A failed cycle must not stop the worker.
            logger.exception("worker_cycle_failed")
        await asyncio.sleep(interval_seconds)
