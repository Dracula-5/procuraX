"""AI endpoints: guarded SQL analytics, advisory flags on requests, recorded predictions.

Nothing here changes a decision. The SQL path answers read-only questions; advisory flags are shown
beside a request; every model output is recorded in ai_predictions (model, version, input reference).
"""

import re
import time
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.ai import gateway
from app.ai.models import AIPrediction
from app.ai.sql_guard import ALLOWED, MAX_ROWS, UnsafeQuery, guard
from app.api.deps import DB, CurrentUser, requires
from app.core.principal import Principal
from app.domain.rbac import Permission
from app.modules.procurement import service as procurement

router = APIRouter(tags=["ai"])
Analyst = Annotated[Principal, Depends(requires(Permission.ANALYTICS_READ))]


def _sql_system() -> str:
    """The SQL-drafting instructions: the allowed schema and the output contract."""
    tables = [f"- {table}({', '.join(sorted(columns))})" for table, columns in sorted(ALLOWED.items())]
    rules = [
        "You write one PostgreSQL SELECT statement that answers a procurement analytics question.",
        "Use only these tables and columns:",
        *tables,
        "Amounts in purchase_requests.base_amount are in the organisation's base currency.",
        "purchase_requests.status is one of draft, pending_approval, policy_blocked, approved, rejected, cancelled.",
        "invoices.match_status is one of matched, partially_matched, mismatch, duplicate, requires_review, "
        "exception_approved, exception_rejected.",
        f"Name every column (no *), write no comments, return at most {MAX_ROWS} rows.",
        "Reply with the SQL only. If the question cannot be answered from these tables, reply UNANSWERABLE.",
    ]
    return "\n".join(rules)


SQL_SYSTEM = _sql_system()
_FENCE = re.compile(r"^```(?:sql)?\s*|\s*```$", re.IGNORECASE)


class SqlQuestionIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class SqlAnswer(BaseModel):
    status: Literal["answered", "refused", "unanswerable", "no_model_answer"]
    question: str
    sql: str | None
    columns: list[str]
    rows: list[list[str | None]]
    reason: str | None
    model: str | None
    model_version: str | None
    prediction_id: uuid.UUID | None


async def _run_read_only(session: DB, sql: str) -> tuple[list[str], list[list[str | None]]]:
    """Execute in a read-only, time-boxed savepoint under the caller's tenant (RLS stays on)."""
    async with session.begin_nested() as savepoint:
        await session.execute(text("SET LOCAL transaction_read_only = on"))
        await session.execute(text("SET LOCAL statement_timeout = '3s'"))
        result = await session.execute(text(sql))
        columns = list(result.keys())
        rows = [[None if v is None else str(v) for v in row] for row in result.fetchmany(MAX_ROWS)]
        await savepoint.rollback()
    return columns, rows


@router.post("/procurement-assistant/sql-query", response_model=SqlAnswer)
async def sql_query(data: SqlQuestionIn, session: DB, principal: Analyst) -> SqlAnswer:
    """A language model drafts SQL; the guard checks it; it runs read-only under row-level security."""
    question = data.question.strip()
    llm = gateway.get_llm()
    try:
        draft = await llm.complete(system=SQL_SYSTEM, prompt=f"Question: {question}", max_tokens=2000)
    except gateway.LLMUnavailable as exc:
        return SqlAnswer(
            status="no_model_answer", question=question, sql=None, columns=[], rows=[], reason=exc.reason,
            model=llm.name, model_version=None, prediction_id=None,
        )  # fmt: skip
    sql = _FENCE.sub("", draft.text.strip()).strip()
    start = time.perf_counter()
    status: Literal["answered", "refused", "unanswerable"]
    columns: list[str] = []
    rows: list[list[str | None]] = []
    reason: str | None = None
    executed: str | None = None
    if sql.upper().rstrip(".") == "UNANSWERABLE":
        status = "unanswerable"
    else:
        try:
            executed = guard(sql).sql
            columns, rows = await _run_read_only(session, executed)
            status = "answered"
        except UnsafeQuery as exc:
            status, reason = "refused", exc.reason
        except DBAPIError as exc:
            status, reason = "refused", f"sql_database_refused: {type(exc.orig).__name__}"
    prediction = await gateway.record(
        session,
        org_id=principal.org_id,
        kind="sql_draft",
        model=draft.model,
        model_version=draft.model_version,
        input_ref=f"question:{gateway.sha256_of(question)[:16]}",
        input_data={"system": SQL_SYSTEM, "question": question},
        output={"status": status, "draft": sql, "executed": executed, "reason": reason, "rows": len(rows)},
        latency_ms=draft.latency_ms + (time.perf_counter() - start) * 1000,
        actor_user_id=principal.user_id,
        input_tokens=draft.input_tokens,
        output_tokens=draft.output_tokens,
    )
    await session.commit()
    return SqlAnswer(
        status=status, question=question, sql=executed or sql, columns=columns, rows=rows, reason=reason,
        model=draft.model, model_version=draft.model_version, prediction_id=prediction.id,
    )  # fmt: skip


class AdvisoryOut(BaseModel):
    prediction_id: uuid.UUID
    model: str
    model_version: str
    created_at: datetime
    submission: int | None
    flags: list[dict]


@router.get("/purchase-requests/{pr_id}/advisories", response_model=list[AdvisoryOut])
async def request_advisories(pr_id: uuid.UUID, session: DB, principal: CurrentUser) -> list[AdvisoryOut]:
    """ML flags recorded when the request was submitted. For review only; they do not affect routing."""
    pr = await procurement._get(session, principal, pr_id)  # noqa: SLF001 - same visibility as the request
    rows = await session.scalars(
        select(AIPrediction)
        .where(
            AIPrediction.org_id == principal.org_id,
            AIPrediction.input_ref == f"purchase_request:{pr.id}",
            AIPrediction.kind == "advisory_flags",
        )
        .order_by(AIPrediction.created_at.desc())
        .limit(10)
    )
    return [
        AdvisoryOut(
            prediction_id=r.id,
            model=r.model,
            model_version=r.model_version,
            created_at=r.created_at,
            submission=r.output.get("submission"),
            flags=r.output.get("flags", []),
        )
        for r in rows
    ]
