"""Database-level controls, tested by connecting as the runtime role directly — i.e. what
still holds if an application bug forgets a tenant filter."""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.domain.rbac import ROLE_PERMISSIONS, Role
from tests.helpers import build_org, create_pr


async def _set_tenant(conn, org_id: str | None) -> None:  # noqa: ANN001
    await conn.execute(text("SELECT set_config('app.org_id', :v, true)"), {"v": org_id or ""})


async def test_rls_hides_all_rows_without_tenant_context(client: AsyncClient, app_engine) -> None:  # noqa: ANN001
    org = await build_org(client, name="RLS no-context")
    await create_pr(client, org)
    async with app_engine.begin() as conn:
        # Unfiltered queries with no tenant set: fail closed.
        for table in ("purchase_requests", "users", "vendors", "budgets", "audit_logs", "organizations"):
            assert await conn.scalar(text(f"SELECT count(*) FROM {table}")) == 0, table  # noqa: S608


async def test_rls_scopes_unfiltered_queries_to_current_tenant(client: AsyncClient, app_engine) -> None:  # noqa: ANN001
    a = await build_org(client, name="RLS A")
    b = await build_org(client, name="RLS B")
    await create_pr(client, a)
    await create_pr(client, b)
    async with app_engine.begin() as conn:
        await _set_tenant(conn, a.org_id)
        # Deliberately no WHERE org_id = … : RLS alone must restrict the result.
        org_ids = set((await conn.scalars(text("SELECT DISTINCT org_id::text FROM purchase_requests"))).all())
        assert org_ids == {a.org_id}
        users = set((await conn.scalars(text("SELECT DISTINCT org_id::text FROM users"))).all())
        assert users == {a.org_id}


async def test_rls_blocks_writes_into_another_tenant(client: AsyncClient, app_engine) -> None:  # noqa: ANN001
    a = await build_org(client, name="RLS write A")
    b = await build_org(client, name="RLS write B")
    with pytest.raises(DBAPIError, match="row-level security"):
        async with app_engine.begin() as conn:
            await _set_tenant(conn, a.org_id)
            await conn.execute(
                text(
                    "INSERT INTO departments (id, org_id, name, code, created_at, updated_at) "
                    "VALUES (:id, :org, 'Injected', 'INJ', now(), now())"
                ),
                {"id": uuid.uuid4(), "org": b.org_id},
            )
    async with app_engine.begin() as conn:
        await _set_tenant(conn, a.org_id)
        updated = await conn.execute(
            text("UPDATE purchase_requests SET title = 'tampered' WHERE org_id = :b"), {"b": b.org_id}
        )
        assert updated.rowcount == 0


async def test_audit_log_is_append_only_for_runtime_role(client: AsyncClient, app_engine) -> None:  # noqa: ANN001
    org = await build_org(client, name="Audit immutability")
    for statement in ("UPDATE audit_logs SET summary = 'x'", "DELETE FROM audit_logs"):
        with pytest.raises(DBAPIError, match="permission denied"):
            async with app_engine.begin() as conn:
                await _set_tenant(conn, org.org_id)
                await conn.execute(text(statement))


async def test_rbac_catalogue_is_read_only_and_matches_code(app_engine) -> None:  # noqa: ANN001
    async with app_engine.begin() as conn:
        rows = (await conn.execute(text("SELECT role_key, permission_key FROM role_permissions"))).all()
    in_db = {(r, p) for r, p in rows}
    in_code = {(role.value, perm.value) for role, perms in ROLE_PERMISSIONS.items() for perm in perms}
    assert in_db == in_code, "RBAC tables drifted from app.domain.rbac — add a migration"
    assert {r for r, _ in in_db} == {r.value for r in Role}

    with pytest.raises(DBAPIError, match="permission denied"):
        async with app_engine.begin() as conn:
            await conn.execute(text("INSERT INTO role_permissions VALUES ('employee', 'platform:admin')"))


async def test_constraints_reject_invalid_business_data(client: AsyncClient, admin_engine) -> None:  # noqa: ANN001
    org = await build_org(client, name="Constraints")
    pr = await create_pr(client, org)
    async with admin_engine.begin() as conn:
        with pytest.raises(DBAPIError):
            async with conn.begin_nested():
                await conn.execute(
                    text("UPDATE purchase_requests SET status = 'teleported' WHERE id = :id"),
                    {"id": pr["id"]},
                )
        with pytest.raises(DBAPIError):
            async with conn.begin_nested():
                await conn.execute(
                    text("UPDATE purchase_request_items SET quantity = 0 WHERE purchase_request_id = :id"),
                    {"id": pr["id"]},
                )
