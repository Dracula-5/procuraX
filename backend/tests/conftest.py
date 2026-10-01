"""Test harness.

Tests run against a real PostgreSQL (docker-compose service, database `procurax_test`)
because the behaviours under test — Row-Level Security, row locks, constraints, grants —
only exist in PostgreSQL. The API connects as the least-privileged runtime role, exactly
as in production; migrations and cleanup use the owner role.
"""

import os
import subprocess
import sys
from pathlib import Path

os.environ["PROCURAX_ENV"] = "test"
os.environ.setdefault(
    "PROCURAX_DATABASE_URL",
    "postgresql+asyncpg://procurax_app:procurax_app_dev@127.0.0.1:55433/procurax_test",
)
os.environ.setdefault(
    "PROCURAX_DATABASE_ADMIN_URL", "postgresql+asyncpg://procurax:procurax_dev@127.0.0.1:55433/procurax_test"
)
os.environ["PROCURAX_DEMO_MODE"] = "true"
# Many tests share one client address; the throttle itself is tested with a tightened limit.
os.environ.setdefault("PROCURAX_LOGIN_MAX_FAILURES", "1000")

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import event, text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.core.config import get_settings  # noqa: E402
from app.main import app  # noqa: E402
from app.models import TENANT_TABLES  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session", autouse=True)
def migrated_database() -> None:
    subprocess.run(  # noqa: S603
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND_DIR,
        check=True,
        env={**os.environ},
    )


@pytest.fixture(scope="session", autouse=True)
async def clean_database(migrated_database: None) -> None:
    engine = create_async_engine(get_settings().database_admin_url)
    async with engine.begin() as conn:
        tables = ", ".join(["organizations", *TENANT_TABLES])
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    await engine.dispose()


@pytest.fixture
async def admin_engine():  # noqa: ANN201
    """Owner connection for assertions that must bypass the application (e.g. RLS tests).

    On managed PostgreSQL the owner is not a superuser, so FORCE RLS applies to it as well; the
    session-level bypass setting keeps owner-side setup and assertions independent of that.
    """
    engine = create_async_engine(get_settings().database_admin_url)

    @event.listens_for(engine.sync_engine, "connect")
    def _bypass_rls(dbapi_connection, _record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("SELECT set_config('app.rls_bypass', 'on', false)")
        cursor.close()

    yield engine
    await engine.dispose()


@pytest.fixture
async def app_engine():  # noqa: ANN201
    """Raw connection as the runtime role, to test database-level controls directly."""
    engine = create_async_engine(get_settings().database_url)
    yield engine
    await engine.dispose()


@pytest.fixture
async def client() -> AsyncClient:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
