"""Pure AI pieces: SQL guard (attack cases), embeddings, fusion, replay provider, Japanese invoice parsing."""

import json
import math

import pytest

from app.ai import gateway
from app.ai.embeddings import embed
from app.ai.retrieval import rrf
from app.ai.sql_guard import MAX_ROWS, UnsafeQuery, guard
from app.ai.text import Passage, bm25_rank
from app.ml.invoice_text import extract_invoice_text

# --- SQL guard ------------------------------------------------------------------------------------------


def test_guard_accepts_a_tenant_analytics_query_and_caps_rows() -> None:
    sql = (
        "SELECT pr.category, sum(pr.base_amount) AS spend FROM purchase_requests pr "
        "WHERE pr.status = 'approved' GROUP BY pr.category ORDER BY spend DESC"
    )
    out = guard(sql)
    assert out.tables == ("purchase_requests",)
    assert f"LIMIT {MAX_ROWS}" in out.sql
    assert "LIMIT 5" in guard(sql + " LIMIT 5").sql  # a smaller limit is kept
    assert f"LIMIT {MAX_ROWS}" in guard(sql + " LIMIT 100000").sql


def test_guard_accepts_ctes_joins_and_date_functions() -> None:
    sql = (
        "WITH monthly AS (SELECT date_trunc('month', i.invoice_date) AS month, sum(i.total) AS total "
        "FROM invoices i JOIN vendors v ON v.id = i.vendor_id WHERE v.status = 'approved' GROUP BY 1) "
        "SELECT month, total FROM monthly ORDER BY month"
    )
    assert guard(sql).tables == ("invoices", "vendors")


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("DELETE FROM purchase_requests", "sql_not_select"),
        ("UPDATE vendors SET status = 'approved'", "sql_not_select"),
        ("SELECT 1; DROP TABLE vendors", "sql_multiple_statements"),
        ("SELECT set_config('app.rls_bypass', 'on', true)", "sql_function"),
        ("SELECT current_setting('app.org_id')", "sql_function"),
        ("SELECT pg_sleep(30)", "sql_function"),
        ("SELECT name FROM vendors WHERE pg_catalog.pg_sleep(1) IS NULL", "sql_function"),
        ("SELECT bank_account FROM vendors", "sql_column"),
        ("SELECT v.bank_account FROM vendors v", "sql_column"),
        ("SELECT email, password_hash FROM users", "sql_table"),
        ("SELECT payment_settings FROM organizations", "sql_table"),
        ("SELECT usename FROM pg_catalog.pg_user", "sql_table"),
        ("SELECT * FROM vendors", "sql_star"),
        ("SELECT name INTO TEMP stolen FROM vendors", "sql_forbidden"),
        ("SELECT name FROM vendors FOR UPDATE", "sql_forbidden"),
        ("SELECT name FROM vendors -- hide", "sql_comment"),
        ("SELECT name /* x */ FROM vendors", "sql_comment"),
        ("WITH x AS (DELETE FROM vendors RETURNING id) SELECT id FROM x", "sql_forbidden"),
        ("SELECT mfa_secret_encrypted FROM users", "sql_table"),
        ("SELECT name FROM public.vendors", "sql_table"),
        ("COPY vendors TO '/tmp/x'", "sql_not_select"),
        ("", "sql_empty"),
        ("SELEC name FROM vendors", "sql_parse"),
    ],
    ids=lambda v: v[:40] if isinstance(v, str) else v,
)
def test_guard_refuses_unsafe_queries(sql: str, reason: str) -> None:
    with pytest.raises(UnsafeQuery) as exc:
        guard(sql)
    assert exc.value.reason == reason, (sql, exc.value)


# --- Embeddings and fusion ---------------------------------------------------------------------------------


def test_embeddings_are_deterministic_unit_vectors_with_overlap_signal() -> None:
    a, b = embed("適格請求書の登録番号"), embed("適格請求書の登録番号を確認")
    assert a == embed("適格請求書の登録番号") and len(a) == 256
    assert math.isclose(sum(v * v for v in a), 1.0, rel_tol=1e-9)
    unrelated = embed("office chairs and desks")
    cosine = lambda x, y: sum(p * q for p, q in zip(x, y, strict=True))  # noqa: E731
    assert cosine(a, b) > cosine(a, unrelated)
    assert embed("") == [0.0] * 256


def test_reciprocal_rank_fusion() -> None:
    fused = rrf([["a", "b", "c"], ["c", "a"]], k=60)
    assert sorted(fused, key=lambda key: -fused[key]) == ["a", "c", "b"]
    assert math.isclose(fused["a"], 1 / 61 + 1 / 62)


def test_bm25_ranks_japanese_by_bigrams() -> None:
    passages = [
        Passage("1", "インボイス制度", "適格請求書発行事業者の登録番号を記載します。", 0),
        Passage("2", "経費精算", "交通費の精算方法について。", 0),
    ]
    assert [p.id for p in bm25_rank("登録番号はどこに書く？", passages)] == ["1"]


# --- Replay provider ---------------------------------------------------------------------------------------


async def test_replay_provider_serves_recordings_and_misses_otherwise(tmp_path) -> None:  # noqa: ANN001
    provider = gateway.ReplayProvider(str(tmp_path))
    with pytest.raises(gateway.LLMUnavailable) as miss:
        await provider.complete(system="s", prompt="p")
    assert miss.value.reason == "llm_replay_miss"
    key = gateway.prompt_key("s", "p")
    (tmp_path / f"{key}.json").write_text(
        json.dumps({"text": "Answer [1].", "model": "claude-opus-5-5", "recorded_at": "2026-11-13T00:00:00"}),
        encoding="utf-8",
    )
    result = await provider.complete(system="s", prompt="p")
    assert result.text == "Answer [1]." and result.model == "replay"
    assert result.model_version == "claude-opus-5-5@2026-11-13T00:00:00"
    with pytest.raises(gateway.LLMUnavailable):
        await gateway.ReplayProvider(None).complete(system="s", prompt="p")  # no recordings configured


# --- Japanese invoices ----------------------------------------------------------------------------------------


def test_japanese_invoice_labels_full_width_and_dates() -> None:
    text = "\n".join(
        [
            "適格請求書",
            "取引先：株式会社みなと文具",
            "登録番号：Ｔ２０１０４０１０００００１",
            "請求書番号：INV-2026-00042",
            "請求日：2026年10月3日",
            "注文番号：PO-2026-000042",
            "通貨：円",
            "数量：12",
            "小計：120,000円",
            "消費税：12,000円",
            "合計：132,000円",
        ]
    )
    parsed = extract_invoice_text(text, ocr_tolerant=True)
    assert parsed == {
        "vendor": "株式会社みなと文具",
        "invoice_number": "INV-2026-00042",
        "invoice_date": "2026-10-03",
        "registration_number": "T2010401000001",
        "purchase_order_number": "PO-2026-000042",
        "currency": "JPY",
        "quantity": "12",
        "subtotal": "120000",
        "tax": "12000",
        "total": "132000",
    }
    assert extract_invoice_text("Total: 1,000\nSubtotal: 900")["total"] == "1000"  # English unchanged
