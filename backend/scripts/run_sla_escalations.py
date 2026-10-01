"""Run one idempotent approval SLA escalation sweep; schedule externally (Task Scheduler/cron).

Alternatively set PROCURAX_SLA_ESCALATION_INTERVAL_SECONDS to run the sweep inside the API.
Both paths take the same PostgreSQL advisory lock, so they never sweep concurrently.
"""

import asyncio
import json
import sys
from pathlib import Path

# Run as a file (`python scripts/run_sla_escalations.py`), so make the backend package importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.modules.procurement.escalations import run_sla_sweep_once  # noqa: E402


async def main() -> None:
    result = await run_sla_sweep_once()
    print(json.dumps(result if result is not None else {"skipped": "another sweep holds the lock"}))


if __name__ == "__main__":
    asyncio.run(main())
