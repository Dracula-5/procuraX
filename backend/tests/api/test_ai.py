"""AI features end to end: cited knowledge answers, guarded SQL under RLS, advisory flags, recorded outputs."""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.ai import advisors, gateway
from app.ai import router as ai_router
from app.ai.sql_guard import GuardedQuery
from app.core.config import get_settings
from tests.helpers import API, build_org, create_pr, create_vendor, submit

GUIDE = (
    "Purchases of software above one million yen need two written quotes. 適格請求書には登録番号を記載する。"
    " The procurement officer checks the quotes before the purchase order is issued."
)


class FakeLLM:
    """Stands in for a model: returns a fixed text and records the prompts it saw."""

    name = "fake"

    def __init__(self, text: str) -> None:
        self.text = text
        self.prompts: list[str] = []

    async def complete(self, *, system: str, prompt: str, max_tokens: int | None = None) -> gateway.LLMResult:
        self.prompts.append(prompt)
        return gateway.LLMResult(self.text, "fake-model", "fake-1", 120, 30, 12.5, "end_turn")


async def _predictions(admin_engine, org_id: str, kind: str) -> list:  # noqa: ANN001
    async with admin_engine.connect() as conn:
        return (
            await conn.execute(
                text("SELECT model, model_version, input_ref, input_sha256, output FROM ai_predictions "
                     "WHERE org_id = :o AND kind = :k ORDER BY created_at"),
                {"o": org_id, "k": kind},
            )
        ).all()  # fmt: skip


async def test_knowledge_uses_hybrid_retrieval_and_only_shows_correctly_cited_answers(
    client: AsyncClient,
    admin_engine,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    org = await build_org(client)
    added = await client.post(
        f"{API}/procurement-assistant/knowledge/documents",
        headers=org.admin.headers,
        json={"title": "Software buying guide", "source_reference": "Internal guide v3", "content": GUIDE},
    )
    assert added.status_code == 201, added.text
    async with admin_engine.connect() as conn:
        chunk = (
            await conn.execute(
                text(
                    "SELECT embedding_model, vector_dims(embedding) FROM knowledge_chunks WHERE org_id = :o"
                ),
                {"o": org.org_id},
            )
        ).one()
    assert chunk == ("hashing-ngram-256@1", 256)

    question = {"question": "How many quotes are needed for software above one million yen?"}
    plain = (
        await client.post(
            f"{API}/procurement-assistant/knowledge", headers=org["employee"].headers, json=question
        )
    ).json()
    assert plain["generated"] is False  # default replay provider has no recording: extractive only
    assert plain["citations"][0]["title"].startswith("Software buying guide (v1)")
    assert "BM25 retrieval" in plain["strategy"]  # the measured default
    monkeypatch.setattr(get_settings(), "retrieval_mode", "hybrid")
    hybrid = (
        await client.post(
            f"{API}/procurement-assistant/knowledge", headers=org["employee"].headers, json=question
        )
    ).json()
    assert "pgvector" in hybrid["strategy"]
    assert hybrid["citations"][0]["title"].startswith("Software buying guide (v1)")

    good = FakeLLM("Two written quotes are needed above one million yen [1].")
    monkeypatch.setattr(gateway, "get_llm", lambda settings=None: good)
    answer = (
        await client.post(
            f"{API}/procurement-assistant/knowledge", headers=org["employee"].headers, json=question
        )
    ).json()
    assert answer["generated"] is True and answer["answer"].endswith("[1].")
    assert answer["model"] == "fake-model" and answer["prediction_id"]
    assert "[1] Software buying guide" in good.prompts[0]

    bad = FakeLLM("Three quotes are always needed [7].")  # cites a source it was not given
    monkeypatch.setattr(gateway, "get_llm", lambda settings=None: bad)
    refused = (
        await client.post(
            f"{API}/procurement-assistant/knowledge", headers=org["employee"].headers, json=question
        )
    ).json()
    assert refused["generated"] is False and "Three quotes" not in refused["answer"]
    recorded = await _predictions(admin_engine, org.org_id, "knowledge_answer")
    assert [r.output["accepted"] for r in recorded] == [True, False]
    assert all(
        r.model == "fake-model" and r.model_version == "fake-1" and len(r.input_sha256) == 64
        for r in recorded
    )


async def test_sql_answers_run_read_only_under_the_callers_tenant(
    client: AsyncClient,
    admin_engine,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    a = await build_org(client, name="SQL A K.K.")
    b = await build_org(client, name="SQL B K.K.")
    await create_pr(client, a)
    for _ in range(3):
        await create_pr(client, b)
    ask = {"question": "How many purchase requests do we have?"}

    none = (
        await client.post(f"{API}/procurement-assistant/sql-query", headers=a["finance"].headers, json=ask)
    ).json()
    assert none["status"] == "no_model_answer" and none["reason"] == "llm_replay_miss"

    monkeypatch.setattr(
        gateway,
        "get_llm",
        lambda settings=None: FakeLLM("```sql\nSELECT count(id) AS requests FROM purchase_requests\n```"),
    )
    answered = (
        await client.post(f"{API}/procurement-assistant/sql-query", headers=a["finance"].headers, json=ask)
    ).json()
    assert answered["status"] == "answered", answered
    assert answered["columns"] == ["requests"] and answered["rows"] == [["1"]]  # only org A's row (RLS)
    assert "LIMIT 200" in answered["sql"]

    monkeypatch.setattr(
        gateway, "get_llm", lambda settings=None: FakeLLM("SELECT set_config('app.rls_bypass', 'on', true)")
    )
    bypass = (
        await client.post(f"{API}/procurement-assistant/sql-query", headers=a["finance"].headers, json=ask)
    ).json()
    assert bypass["status"] == "refused" and bypass["reason"] == "sql_function"

    # Defence in depth: even if the guard let a write through, the read-only transaction refuses it.
    monkeypatch.setattr(
        ai_router, "guard", lambda sql: GuardedQuery(sql="DELETE FROM purchase_requests", tables=())
    )
    monkeypatch.setattr(gateway, "get_llm", lambda settings=None: FakeLLM("SELECT 1"))
    write = (
        await client.post(f"{API}/procurement-assistant/sql-query", headers=a["finance"].headers, json=ask)
    ).json()
    assert write["status"] == "refused" and write["reason"].startswith("sql_database_refused")
    async with admin_engine.connect() as conn:
        assert (
            await conn.scalar(
                text("SELECT count(*) FROM purchase_requests WHERE org_id = :o"), {"o": a.org_id}
            )
        ) == 1

    monkeypatch.setattr(gateway, "get_llm", lambda settings=None: FakeLLM("UNANSWERABLE"))
    unanswerable = (
        await client.post(f"{API}/procurement-assistant/sql-query", headers=a["finance"].headers, json=ask)
    ).json()
    assert unanswerable["status"] == "unanswerable"
    employee = await client.post(
        f"{API}/procurement-assistant/sql-query", headers=a["employee"].headers, json=ask
    )
    assert employee.status_code == 403
    drafts = await _predictions(admin_engine, a.org_id, "sql_draft")
    assert [d.output["status"] for d in drafts] == ["answered", "refused", "refused", "unanswerable"]


async def test_advisory_flags_are_recorded_but_never_change_the_decision(
    client: AsyncClient,
    admin_engine,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    org = await build_org(client, budget="100000000")
    vendor = await create_vendor(client, org)
    for amount in ("20000", "22000", "18000", "21000", "19000", "23000", "20500"):
        await submit(client, org, await create_pr(client, org, amount=amount, vendor_id=vendor["id"]))
    outlier = await submit(client, org, await create_pr(client, org, amount="400000", vendor_id=vendor["id"]))
    flags = (
        await client.get(
            f"{API}/purchase-requests/{outlier['id']}/advisories", headers=org["employee"].headers
        )
    ).json()
    codes = {f["code"] for f in flags[0]["flags"]}
    assert "amount_outlier" in codes and "possible_duplicate" in codes  # same title, requester and vendor
    assert flags[0]["model"] == "procurax-advisors"

    # The same request with the advisors switched off gets exactly the same policy result.
    monkeypatch.setattr(advisors, "_flags", _raise)
    again = await submit(client, org, await create_pr(client, org, amount="400000", vendor_id=vendor["id"]))
    assert again["status"] == outlier["status"]
    assert [s["approver_role"] for s in again["approvals"]] == [
        s["approver_role"] for s in outlier["approvals"]
    ]
    assert {h["rule_id"] for h in again["policy_evaluation"]["hits"]} == {
        h["rule_id"] for h in outlier["policy_evaluation"]["hits"]
    }
    assert (
        await client.get(f"{API}/purchase-requests/{again['id']}/advisories", headers=org["employee"].headers)
    ).json() == []
    other = await build_org(client, name="Advisory B K.K.")
    hidden = await client.get(
        f"{API}/purchase-requests/{outlier['id']}/advisories", headers=other["employee"].headers
    )
    assert hidden.status_code == 404


async def _raise(*_: object) -> list[dict]:
    raise RuntimeError("advisor down")


async def test_ai_tables_are_tenant_isolated_and_predictions_are_append_only(
    client: AsyncClient, app_engine
) -> None:  # noqa: ANN001
    a = await build_org(client, name="AI RLS A")
    b = await build_org(client, name="AI RLS B")
    for org in (a, b):
        await client.post(
            f"{API}/procurement-assistant/knowledge/documents",
            headers=org.admin.headers,
            json={"title": "Guide", "content": GUIDE},
        )
        await submit(client, org, await create_pr(client, org))
    for table in ("knowledge_chunks", "ai_predictions"):
        async with app_engine.connect() as conn:
            async with conn.begin():
                await conn.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": a.org_id})
                seen = {str(r[0]) for r in await conn.execute(text(f"SELECT DISTINCT org_id FROM {table}"))}  # noqa: S608
                assert seen == {a.org_id}, table
            async with conn.begin():
                assert (await conn.scalar(text(f"SELECT count(*) FROM {table}"))) == 0  # noqa: S608
    async with app_engine.begin() as conn:
        await conn.execute(text("SELECT set_config('app.org_id', :o, true)"), {"o": a.org_id})
        for statement in ("UPDATE ai_predictions SET model = 'edited'", "DELETE FROM ai_predictions"):
            with pytest.raises(DBAPIError, match="permission denied"):
                async with conn.begin_nested():
                    await conn.execute(text(statement))
