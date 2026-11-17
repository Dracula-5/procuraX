"""Safe, deterministic analytics assistant: intents map to fixed tenant-scoped ORM queries."""

import asyncio
import base64
import binascii
import re
import uuid
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.ai import gateway
from app.ai.retrieval import hybrid_search, index_document, rrf
from app.ai.text import Passage, bm25_rank, chunks, tokenize
from app.api.deps import DB, requires
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.principal import Principal
from app.domain.policy.rules import RULES
from app.domain.rbac import Permission
from app.domain.workflow.purchase_request import PRStatus
from app.ml.invoice_ocr import MAX_DOCUMENT_BYTES, OCRUnavailableError, extract_ocr_text
from app.ml.invoice_text import extract_invoice_text
from app.modules.audit import service as audit
from app.modules.identity.models import Organization
from app.modules.procurement.knowledge_models import ProcurementKnowledgeDocument
from app.modules.procurement.models import ApprovalPolicy, PurchaseRequest
from app.modules.procurement.records import Invoice, Payment
from app.modules.vendors.models import Vendor

router = APIRouter(prefix="/procurement-assistant", tags=["procurement assistant"])


class AssistantQueryIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class Evidence(BaseModel):
    id: uuid.UUID
    type: str
    label: str
    details: dict


class AssistantAnswer(BaseModel):
    intent: str | None
    answer: str
    strategy: str
    evidence: list[Evidence]


class KnowledgeQueryIn(BaseModel):
    question: str = Field(min_length=3, max_length=500)


class KnowledgeDocumentIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    source_reference: str | None = Field(default=None, max_length=500)
    content: str = Field(min_length=20, max_length=100_000)


class KnowledgeDocumentSummary(BaseModel):
    id: uuid.UUID
    title: str
    source_reference: str | None
    version: int
    is_active: bool
    created_at: datetime


class InvoiceOCRIn(BaseModel):
    filename: str = Field(min_length=5, max_length=255)
    content_base64: str = Field(min_length=1, max_length=25_200_000)


class KnowledgeCitation(BaseModel):
    id: str
    title: str
    excerpt: str
    score: float


class KnowledgeAnswer(BaseModel):
    answer: str
    strategy: str
    citations: list[KnowledgeCitation]
    # A model-written summary of the citations (only when one passed the citation check).
    generated: bool = False
    model: str | None = None
    prediction_id: uuid.UUID | None = None


ANSWER_SYSTEM = (
    "You answer questions about a company's procurement policy using only the numbered sources given. "
    "Write two to four sentences in the language of the question. End every sentence with the number of "
    "the source it comes from, like [1]. If the sources do not answer the question, reply exactly: "
    "NOT IN SOURCES. You never approve, reject or decide anything; say who decides when relevant."
)
_CITATION = re.compile(r"\[(\d+)\]")


def _checked_answer(text: str, sources: int) -> str | None:
    """The model's answer if every sentence-level citation points at a given source; otherwise None."""
    answer = text.strip()
    cited = [int(n) for n in _CITATION.findall(answer)]
    if not answer or answer.upper().startswith("NOT IN SOURCES") or not cited:
        return None
    return answer if all(1 <= n <= sources for n in cited) else None


def _rank_texts(
    question: str,
    documents: list[tuple[str, str, str]],
    limit: int = 5,
    tokenizer: Callable[[str], list[str]] = tokenize,
) -> list[KnowledgeCitation]:
    passages = [Passage(id=i, title=title, text=text, score=0.0) for i, title, text in documents]
    return [
        KnowledgeCitation(id=p.id, title=p.title, excerpt=p.text, score=p.score)
        for p in bm25_rank(question, passages, limit, tokenizer)
    ]


def _rank_policy_rules(question: str, limit: int = 5) -> list[KnowledgeCitation]:
    """Small BM25 lexical baseline over the versioned policy-rule catalogue."""
    documents = [(rule.id, rule.title, rule.description) for rule in RULES.values()]
    return _rank_texts(question, documents, limit)


def _chunks(content: str, *, size: int = 900, overlap: int = 120) -> list[str]:
    return chunks(content, size=size, overlap=overlap)


HELP = (
    "I can answer approved software spend above a threshold, approved vendors by category, "
    "unmatched invoices, pending payment approvals, and why a purchase request is policy-blocked."
)


def _threshold(question: str) -> Decimal | None:
    match = re.search(r"(?:JPY\s*|\u00a5\s*|\uffe5\s*)?([0-9][0-9,]*(?:\.\d{1,2})?)", question, re.IGNORECASE)
    return Decimal(match.group(1).replace(",", "")) if match else None


def _category(question: str) -> str | None:
    synonyms = {
        "software": "software",
        "hardware": "hardware",
        "networking": "hardware",
        "office supplies": "office_supplies",
        "office-supplies": "office_supplies",
        "travel": "travel",
        "facilities": "facilities",
        "marketing": "marketing",
        "logistics": "logistics",
        "manufacturing": "manufacturing",
        "professional services": "professional_services",
        "it services": "it_services",
    }
    lowered = question.casefold()
    return next((value for term, value in synonyms.items() if term in lowered), None)


@router.post("/query", response_model=AssistantAnswer)
async def answer_question(
    data: AssistantQueryIn,
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.ANALYTICS_READ))],
) -> AssistantAnswer:
    question = data.question.strip()
    lowered = question.casefold()
    evidence: list[Evidence] = []
    if "invoice" in lowered and any(word in lowered for word in ("unmatched", "mismatch", "exception")):
        invoices = await session.scalars(
            select(Invoice)
            .where(Invoice.org_id == principal.org_id, Invoice.match_status != "matched")
            .order_by(Invoice.created_at.desc())
            .limit(50)
        )
        for invoice_row in invoices:
            evidence.append(
                Evidence(
                    id=invoice_row.id,
                    type="invoice",
                    label=invoice_row.invoice_number,
                    details={
                        "match_status": invoice_row.match_status,
                        "amount": str(invoice_row.total),
                        "currency": invoice_row.currency,
                        "purchase_order_id": str(invoice_row.purchase_order_id),
                    },
                )
            )
        return AssistantAnswer(
            intent="unmatched_invoices",
            answer=f"Found {len(evidence)} invoices requiring review.",
            strategy="Fixed tenant-scoped ORM query; no generated SQL.",
            evidence=evidence,
        )

    if "payment" in lowered and any(word in lowered for word in ("pending", "approval", "awaiting")):
        payments = await session.scalars(
            select(Payment)
            .where(Payment.org_id == principal.org_id, Payment.status == "pending_approval")
            .order_by(Payment.created_at.asc())
            .limit(50)
        )
        for payment_row in payments:
            evidence.append(
                Evidence(
                    id=payment_row.id,
                    type="payment",
                    label=f"Invoice {payment_row.invoice_id}",
                    details={
                        "amount": str(payment_row.amount),
                        "currency": payment_row.currency,
                        "status": payment_row.status,
                    },
                )
            )
        return AssistantAnswer(
            intent="pending_payments",
            answer=f"Found {len(evidence)} payment requests awaiting approval.",
            strategy="Fixed tenant-scoped ORM query; no generated SQL.",
            evidence=evidence,
        )

    if "software" in lowered and any(word in lowered for word in ("above", "over", "greater than", ">")):
        threshold = _threshold(question)
        if threshold is None:
            return AssistantAnswer(
                intent="approved_software_spend",
                answer="Please include a numeric amount threshold.",
                strategy="Deterministic intent and parameter extraction; no generated SQL.",
                evidence=[],
            )
        requests = await session.scalars(
            select(PurchaseRequest)
            .where(
                PurchaseRequest.org_id == principal.org_id,
                PurchaseRequest.status == PRStatus.APPROVED.value,
                PurchaseRequest.category == "software",
                PurchaseRequest.base_amount > threshold,
            )
            .order_by(PurchaseRequest.base_amount.desc())
            .limit(50)
        )
        for request_row in requests:
            evidence.append(
                Evidence(
                    id=request_row.id,
                    type="purchase_request",
                    label=f"{request_row.number}: {request_row.title}",
                    details={
                        "amount": str(request_row.estimated_total),
                        "currency": request_row.currency,
                        "category": request_row.category,
                        "status": request_row.status,
                    },
                )
            )
        currency = evidence[0].details["currency"] if evidence else ""
        return AssistantAnswer(
            intent="approved_software_spend",
            answer=f"Found {len(evidence)} approved software requests above {threshold} {currency}.".strip(),
            strategy="Threshold is a bound parameter in a fixed tenant-scoped ORM query; no generated SQL.",
            evidence=evidence,
        )

    if any(phrase in lowered for phrase in ("approved vendors", "approved vendor", "which vendors")):
        category = _category(lowered)
        if category is None:
            return AssistantAnswer(
                intent="approved_vendors",
                answer="Name a spend category so I can filter approved vendors.",
                strategy="Deterministic category mapping; no generated SQL.",
                evidence=[],
            )
        vendors = await session.scalars(
            select(Vendor)
            .where(
                Vendor.org_id == principal.org_id,
                Vendor.status == "approved",
                Vendor.deleted_at.is_(None),
                Vendor.categories.contains([category]),
            )
            .order_by(Vendor.name)
            .limit(50)
        )
        for vendor_row in vendors:
            evidence.append(
                Evidence(
                    id=vendor_row.id,
                    type="vendor",
                    label=vendor_row.name,
                    details={
                        "categories": vendor_row.categories,
                        "contract_status": vendor_row.contract_status,
                        "risk_level": vendor_row.risk_level,
                    },
                )
            )
        return AssistantAnswer(
            intent="approved_vendors",
            answer=f"Found {len(evidence)} approved vendors in {category.replace('_', ' ')}.",
            strategy="Fixed tenant-scoped ORM query using PostgreSQL array containment; no generated SQL.",
            evidence=evidence,
        )

    if "request" in lowered and any(word in lowered for word in ("blocked", "why")):
        number_match = re.search(r"PR-[0-9]{4}-[0-9]{6}", question, re.IGNORECASE)
        if not number_match:
            return AssistantAnswer(
                intent="policy_block_reason",
                answer="Include the request number, such as PR-2026-000042.",
                strategy="Deterministic identifier lookup; no generated SQL.",
                evidence=[],
            )
        row = await session.scalar(
            select(PurchaseRequest).where(
                PurchaseRequest.org_id == principal.org_id,
                func.lower(PurchaseRequest.number) == number_match.group(0).lower(),
            )
        )
        if row is None:
            raise NotFoundError("Purchase request not found")
        reasons = row.policy_evaluation or {}
        evidence.append(
            Evidence(
                id=row.id,
                type="purchase_request",
                label=f"{row.number}: {row.title}",
                details={
                    "status": row.status,
                    "policy_version": row.policy_version,
                    "policy_evaluation": reasons,
                },
            )
        )
        return AssistantAnswer(
            intent="policy_block_reason",
            answer=(
                "Request is policy blocked; see the cited rule evaluation."
                if row.status == "policy_blocked"
                else f"Request status is {row.status}; it is not currently policy blocked."
            ),
            strategy="Direct tenant-scoped record lookup; the saved policy snapshot is the source.",
            evidence=evidence,
        )

    return AssistantAnswer(
        intent=None, answer=HELP, strategy="Unsupported question; no model or SQL was called.", evidence=[]
    )


@router.post("/knowledge", response_model=KnowledgeAnswer)
async def answer_policy_question(
    data: KnowledgeQueryIn,
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.POLICY_READ))],
) -> KnowledgeAnswer:
    """Retrieve tenant knowledge documents, exact policy rules and the active policy."""
    question = data.question.strip()
    tenant_documents = await session.scalars(
        select(ProcurementKnowledgeDocument)
        .where(
            ProcurementKnowledgeDocument.org_id == principal.org_id,
            ProcurementKnowledgeDocument.is_active.is_(True),
        )
        .order_by(ProcurementKnowledgeDocument.created_at.desc())
        .limit(100)
    )
    del tenant_documents  # documents are searched through their indexed chunks (BM25 + pgvector, RRF)
    document_citations = [
        KnowledgeCitation(id=p.id, title=p.title, excerpt=p.text, score=p.score)
        for p in await hybrid_search(session, principal.org_id, question, limit=5)
    ]
    # Document passages (hybrid) and policy rules (BM25) have different score scales: fuse by rank.
    rule_citations = _rank_policy_rules(question)
    by_id = {c.id: c for c in [*document_citations, *rule_citations]}
    fused = rrf([[c.id for c in document_citations], [c.id for c in rule_citations]])
    citations = [
        by_id[key].model_copy(update={"score": round(score, 5)})
        # On equal rank the tenant's own document goes first (more specific than a generic rule).
        for key, score in sorted(
            fused.items(), key=lambda item: (-item[1], not item[0].startswith("doc:"), item[0])
        )[:5]
    ]
    if any(term in question.casefold() for term in ("threshold", "amount", "limit", "approval")):
        policy = await session.scalar(
            select(ApprovalPolicy).where(
                ApprovalPolicy.org_id == principal.org_id,
                ApprovalPolicy.is_active.is_(True),
            )
        )
        if policy is None:
            raise NotFoundError("No active approval policy for this organisation")
        config = policy.config
        currency = config["currency"]
        summary = (
            f"Active approval policy v{policy.version} ({currency}): auto-approval through "
            f"{config['auto_approval_limit']}; manager limit {config['manager_approval_limit']}; "
            f"procurement review threshold {config['procurement_review_threshold']}. "
            "Amounts and routing are tenant-configured; the policy engine enforces the rules."
        )
        citations.insert(
            0,
            KnowledgeCitation(
                id=f"approval-policy-v{policy.version}",
                title=f"Active tenant approval policy v{policy.version}",
                excerpt=summary,
                score=1.0,
            ),
        )
    elif citations:
        summary = "Retrieved source text is quoted below; consult its cited policy or tenant source."
    else:
        summary = "No matching policy text was found. Rephrase the question or consult an administrator."
    documents_by = (
        "hybrid retrieval (BM25 + pgvector embeddings, reciprocal rank fusion)"
        if get_settings().retrieval_mode == "hybrid"
        else "BM25 retrieval (CJK bigrams)"
    )
    strategy = (
        f"Tenant-scoped {documents_by} over document chunks, BM25 over the policy rules, and the active "
        "policy, merged by rank; extractive citations."
    )
    if not citations:
        return KnowledgeAnswer(answer=summary, strategy=strategy, citations=citations)
    sources = "\n\n".join(f"[{i}] {c.title}\n{c.excerpt}" for i, c in enumerate(citations, start=1))
    prompt = f"Question: {question}\n\nSources:\n{sources}"
    llm = gateway.get_llm()
    try:
        result = await llm.complete(system=ANSWER_SYSTEM, prompt=prompt, max_tokens=4000)
    except gateway.LLMUnavailable:
        return KnowledgeAnswer(answer=summary, strategy=strategy, citations=citations)
    checked = _checked_answer(result.text, len(citations))
    prediction = await gateway.record(
        session,
        org_id=principal.org_id,
        kind="knowledge_answer",
        model=result.model,
        model_version=result.model_version,
        input_ref=f"question:{gateway.sha256_of(question)[:16]}",
        input_data={"system": ANSWER_SYSTEM, "prompt": prompt},
        output={"text": result.text, "accepted": checked is not None, "citations": [c.id for c in citations]},
        latency_ms=result.latency_ms,
        actor_user_id=principal.user_id,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    await session.commit()
    if checked is None:  # uncited or mis-cited text is never shown; the quoted sources still are
        return KnowledgeAnswer(
            answer=summary, strategy=strategy, citations=citations, prediction_id=prediction.id
        )
    return KnowledgeAnswer(
        answer=checked,
        strategy=f"{strategy} Summary written by {result.model} from the numbered sources only; check them.",
        citations=citations,
        generated=True,
        model=result.model,
        prediction_id=prediction.id,
    )


@router.post("/invoice-ocr")
async def extract_invoice_document(
    data: InvoiceOCRIn,
    principal: Annotated[Principal, Depends(requires(Permission.VENDOR_MANAGE))],
) -> dict:
    """Extract visible invoice text locally; values are suggestions for human review."""
    del principal  # Permission check is the audit boundary; document bytes are never logged or persisted.
    try:
        content = base64.b64decode(data.content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=422, detail="content_base64 must contain valid base64 data") from exc
    if len(content) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="Invoice document must be no larger than 18 MiB")
    try:
        settings = get_settings()
        raw_text = await asyncio.to_thread(
            extract_ocr_text,
            content,
            data.filename,
            tesseract_path=settings.tesseract_path,
            pdftoppm_path=settings.pdftoppm_path,
            tessdata_path=settings.tessdata_path,
            languages=settings.ocr_languages,
        )
    except OCRUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    extracted = extract_invoice_text(raw_text, ocr_tolerant=True)
    return {
        "status": "human_review_required",
        "filename": data.filename,
        "extracted_fields": extracted,
        "raw_text": raw_text,
        "source": "local_tesseract_ocr_plus_labeled_line_parser",
    }


@router.post("/knowledge/documents", response_model=KnowledgeDocumentSummary, status_code=201)
async def add_knowledge_document(
    data: KnowledgeDocumentIn,
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.POLICY_MANAGE))],
) -> KnowledgeDocumentSummary:
    """Version and index a tenant-owned plain-text policy or procurement guideline."""
    title = data.title.strip()
    await session.scalar(select(Organization.id).where(Organization.id == principal.org_id).with_for_update())
    existing = list(
        await session.scalars(
            select(ProcurementKnowledgeDocument)
            .where(
                ProcurementKnowledgeDocument.org_id == principal.org_id,
                ProcurementKnowledgeDocument.title == title,
            )
            .with_for_update()
        )
    )
    version = max((document.version for document in existing), default=0) + 1
    for document in existing:
        document.is_active = False
    row = ProcurementKnowledgeDocument(
        org_id=principal.org_id,
        title=title,
        source_reference=data.source_reference.strip() if data.source_reference else None,
        content=data.content.strip(),
        version=version,
        is_active=True,
        created_by_id=principal.user_id,
    )
    session.add(row)
    await session.flush()
    await index_document(session, row)  # chunks + embeddings for hybrid retrieval
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="procurement_knowledge.version_added",
        entity_type="procurement_knowledge_document",
        entity_id=row.id,
        summary=f"Added {title} version {version} to procurement knowledge",
        changes={"version": version, "source_reference": row.source_reference},
    )
    await session.commit()
    return KnowledgeDocumentSummary.model_validate(row, from_attributes=True)


@router.get("/knowledge/documents", response_model=list[KnowledgeDocumentSummary])
async def list_knowledge_documents(
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.POLICY_READ))],
) -> list[KnowledgeDocumentSummary]:
    rows = await session.scalars(
        select(ProcurementKnowledgeDocument)
        .where(ProcurementKnowledgeDocument.org_id == principal.org_id)
        .order_by(ProcurementKnowledgeDocument.title, ProcurementKnowledgeDocument.version.desc())
    )
    return [KnowledgeDocumentSummary.model_validate(row, from_attributes=True) for row in rows]


@router.post("/knowledge/documents/{document_id}/deactivate", response_model=KnowledgeDocumentSummary)
async def deactivate_knowledge_document(
    document_id: uuid.UUID,
    session: DB,
    principal: Annotated[Principal, Depends(requires(Permission.POLICY_MANAGE))],
) -> KnowledgeDocumentSummary:
    row = await session.scalar(
        select(ProcurementKnowledgeDocument)
        .where(
            ProcurementKnowledgeDocument.id == document_id,
            ProcurementKnowledgeDocument.org_id == principal.org_id,
        )
        .with_for_update()
    )
    if row is None:
        raise NotFoundError("Procurement knowledge document not found")
    if not row.is_active:
        raise ConflictError("Knowledge document is already inactive")
    row.is_active = False
    await audit.record(
        session,
        org_id=principal.org_id,
        actor=principal,
        action="procurement_knowledge.deactivated",
        entity_type="procurement_knowledge_document",
        entity_id=row.id,
        summary=f"Deactivated {row.title} version {row.version}",
    )
    await session.commit()
    return KnowledgeDocumentSummary.model_validate(row, from_attributes=True)
