"""Backup and restore drill: dump a database, restore it into a scratch database, prove the copy works.

    uv run python -m app.scripts.restore_drill --report ../reports/restore_drill.json
    # PostgreSQL tools in a container instead of on PATH:
    uv run python -m app.scripts.restore_drill --tool-prefix "docker exec -i procurax-gaps-postgres" \
        --tool-url postgresql://procurax:procurax_dev@localhost:5432/procurax_test

Steps, each timed: `pg_dump --format=custom` of the source (the backup; SHA-256 recorded) → CREATE DATABASE
<scratch> → `pg_restore --no-owner --single-transaction --exit-on-error` → checks on the restored copy:

1. same Alembic revision as the source;
2. the same number of rows in every table;
3. row-level security enabled and forced, with the tenant_isolation policy, on every tenant table;
4. as the runtime role: nothing visible without a tenant context, exactly one tenant's rows with one;
5. the audit log is still append-only for the runtime role (UPDATE refused).

The scratch database is dropped afterwards (unless --keep). Exit code 1 if any check fails. Nothing is
uploaded anywhere: the dump stays on local disk (--dump-file) and is deleted unless asked to keep it.
"""

import argparse
import asyncio
import hashlib
import json
import shlex
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.models import TENANT_TABLES


def _libpq(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def _with_db(url: str, database: str) -> str:
    return make_url(url).set(database=database).render_as_string(hide_password=False)


def _run(prefix: list[str], args: list[str], stdin: bytes | None = None) -> bytes:
    result = subprocess.run([*prefix, *args], input=stdin, capture_output=True, check=False)  # noqa: S603
    if result.returncode != 0:
        raise RuntimeError(
            f"{args[0]} failed ({result.returncode}): {result.stderr.decode(errors='replace')[:2000]}"
        )
    return result.stdout


async def _counts(url: str) -> tuple[str | None, dict[str, int]]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT set_config('app.rls_bypass', 'on', false)"))
            revision = await conn.scalar(text("SELECT version_num FROM alembic_version"))
            tables = (
                await conn.scalars(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
                )
            ).all()
            counts = {t: int(await conn.scalar(text(f'SELECT count(*) FROM "{t}"')) or 0) for t in tables}  # noqa: S608
        return revision, counts
    finally:
        await engine.dispose()


async def _rls_and_role_checks(admin_url: str, runtime_url: str) -> dict[str, object]:
    engine = create_async_engine(admin_url)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                        "EXISTS (SELECT 1 FROM pg_policies p WHERE p.tablename = c.relname "
                        "AND p.policyname = 'tenant_isolation') "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relkind = 'r'"
                    )
                )
            ).all()
            await conn.execute(text("SELECT set_config('app.rls_bypass', 'on', false)"))
            org = await conn.scalar(
                text("SELECT org_id FROM users GROUP BY org_id ORDER BY count(*) DESC LIMIT 1")
            )
            org_users = (
                int(await conn.scalar(text("SELECT count(*) FROM users WHERE org_id = :o"), {"o": org}) or 0)
                if org
                else 0
            )
    finally:
        await engine.dispose()
    state = {name: (rls, forced, policy) for name, rls, forced, policy in rows}
    unprotected = sorted(t for t in TENANT_TABLES if state.get(t) != (True, True, True))

    runtime = create_async_engine(runtime_url)
    try:
        async with runtime.connect() as conn:
            async with conn.begin():
                without_context = int(await conn.scalar(text("SELECT count(*) FROM users")) or 0)
            async with conn.begin():
                await conn.execute(
                    text("SELECT set_config('app.org_id', :o, true)"), {"o": str(org or uuid.uuid4())}
                )
                with_context = int(await conn.scalar(text("SELECT count(*) FROM users")) or 0)
            audit_update_refused = False
            async with conn.begin():
                try:
                    async with conn.begin_nested():
                        await conn.execute(text("UPDATE audit_logs SET summary = summary WHERE false"))
                except Exception as exc:  # noqa: BLE001
                    audit_update_refused = "permission denied" in str(exc)
    finally:
        await runtime.dispose()
    return {
        "tenant_tables": len(TENANT_TABLES),
        "tenant_tables_without_forced_rls_or_policy": unprotected,
        "runtime_role_rows_without_tenant_context": without_context,
        "runtime_role_rows_with_one_tenant": with_context,
        "expected_rows_for_that_tenant": org_users,
        "audit_log_update_refused_for_runtime_role": audit_update_refused,
    }


async def drill(args: argparse.Namespace) -> dict[str, object]:
    settings = get_settings()
    admin_url = args.source_url or settings.database_admin_url
    source_db = make_url(admin_url).database or "procurax"
    scratch = args.scratch_db or f"{source_db}_restore_drill"
    tool_url = args.tool_url or _libpq(admin_url)
    prefix = shlex.split(args.tool_prefix) if args.tool_prefix else []
    timings: dict[str, float] = {}
    report: dict[str, object] = {
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "source_database": source_db,
        "scratch_database": scratch,
    }

    t = time.perf_counter()
    dump = _run(prefix, ["pg_dump", "--format=custom", "--no-owner", f"--dbname={tool_url}"])
    timings["pg_dump_seconds"] = round(time.perf_counter() - t, 2)
    digest = hashlib.sha256(dump).hexdigest()
    report["dump_bytes"], report["dump_sha256"] = len(dump), digest
    dump_file = Path(args.dump_file) if args.dump_file else None
    if dump_file:
        dump_file.write_bytes(dump)
        if hashlib.sha256(dump_file.read_bytes()).hexdigest() != digest:
            raise RuntimeError("The dump written to disk does not match its checksum")

    admin_engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin_engine.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
            await conn.execute(text(f'CREATE DATABASE "{scratch}"'))
        t = time.perf_counter()
        _run(
            prefix,
            [
                "pg_restore",
                "--no-owner",
                "--single-transaction",
                "--exit-on-error",
                f"--dbname={_with_db(tool_url, scratch)}",
            ],
            stdin=dump,
        )
        timings["pg_restore_seconds"] = round(time.perf_counter() - t, 2)

        t = time.perf_counter()
        source_revision, source_counts = await _counts(admin_url)
        copy_revision, copy_counts = await _counts(_with_db(admin_url, scratch))
        safety = await _rls_and_role_checks(
            _with_db(admin_url, scratch), _with_db(settings.database_url, scratch)
        )
        timings["verification_seconds"] = round(time.perf_counter() - t, 2)
    finally:
        if not args.keep:
            async with admin_engine.connect() as conn:
                await conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        await admin_engine.dispose()
    if dump_file and not args.keep_dump:
        dump_file.unlink(missing_ok=True)

    mismatched = sorted(t for t in source_counts if source_counts[t] != copy_counts.get(t))
    checks = {
        "alembic_revision_matches": source_revision == copy_revision,
        "row_counts_match": not mismatched and set(source_counts) == set(copy_counts),
        "rls_on_every_tenant_table": not safety["tenant_tables_without_forced_rls_or_policy"],
        "runtime_role_fails_closed": safety["runtime_role_rows_without_tenant_context"] == 0,
        "runtime_role_sees_one_tenant": safety["runtime_role_rows_with_one_tenant"]
        == safety["expected_rows_for_that_tenant"],
        "audit_log_append_only": safety["audit_log_update_refused_for_runtime_role"],
    }
    report.update(
        {
            "alembic_revision": copy_revision,
            "tables": len(copy_counts),
            "rows": sum(copy_counts.values()),
            "tables_with_different_counts": mismatched,
            **safety,
            "timings": timings,
            "checks": checks,
            "passed": all(checks.values()),
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="ProcuraX backup and restore drill")
    parser.add_argument(
        "--source-url", help="owner connection (SQLAlchemy URL); default PROCURAX_DATABASE_ADMIN_URL"
    )
    parser.add_argument(
        "--tool-url", help="libpq URL as seen by pg_dump/pg_restore (default: the source URL)"
    )
    parser.add_argument("--tool-prefix", help='run the tools through e.g. "docker exec -i <container>"')
    parser.add_argument("--scratch-db", help="database to restore into (dropped and recreated)")
    parser.add_argument("--dump-file", help="also write the backup here (checked against its SHA-256)")
    parser.add_argument("--keep-dump", action="store_true", help="keep --dump-file after the drill")
    parser.add_argument("--keep", action="store_true", help="keep the scratch database")
    parser.add_argument("--report", help="write the JSON report here")
    args = parser.parse_args()
    report = asyncio.run(drill(args))
    body = json.dumps(report, indent=2)
    if args.report:
        Path(args.report).write_text(body + "\n", encoding="utf-8")
    print(body)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
