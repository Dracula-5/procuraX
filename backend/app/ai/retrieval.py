"""Retrieval over a tenant's knowledge documents: BM25, or BM25 + pgvector (embedding) fused by RRF.

PROCURAX_RETRIEVAL_MODE selects it. BM25 is the default: with the local hashing embedding the hybrid ranking
measured lower on the official NTA Q&A (reports/rag_nta_invoice_qa.json); hybrid is for a semantic
embedding model.

Chunks and their embeddings live in `knowledge_chunks` (RLS: a tenant only ever searches its own rows).
A document is indexed when it is added; documents from before the index existed are indexed on first
search. The two rankings are combined with reciprocal rank fusion (k = 60), so neither score scale has to
be calibrated against the other.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import EMBEDDING_MODEL, embed
from app.ai.models import KnowledgeChunk
from app.ai.text import Passage, bm25_rank, chunks
from app.core.config import get_settings
from app.modules.procurement.knowledge_models import ProcurementKnowledgeDocument

RRF_K = 60
CANDIDATES = 20


async def index_document(session: AsyncSession, document: ProcurementKnowledgeDocument) -> int:
    pieces = chunks(document.content)
    for index, text in enumerate(pieces):
        session.add(
            KnowledgeChunk(
                org_id=document.org_id,
                document_id=document.id,
                chunk_index=index,
                content=text,
                embedding=embed(f"{document.title} {text}"),
                embedding_model=EMBEDDING_MODEL,
            )
        )
    await session.flush()
    return len(pieces)


async def _ensure_indexed(session: AsyncSession, org_id: uuid.UUID) -> None:
    indexed = select(KnowledgeChunk.document_id).where(KnowledgeChunk.org_id == org_id).distinct()
    missing = await session.scalars(
        select(ProcurementKnowledgeDocument).where(
            ProcurementKnowledgeDocument.org_id == org_id,
            ProcurementKnowledgeDocument.is_active.is_(True),
            ProcurementKnowledgeDocument.id.not_in(indexed),
        )
    )
    for document in missing:
        await index_document(session, document)


def rrf(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    """Reciprocal rank fusion: sum of 1 / (k + rank) over the rankings an item appears in. Pure."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return scores


async def hybrid_search(
    session: AsyncSession, org_id: uuid.UUID, question: str, limit: int = 5
) -> list[Passage]:
    await _ensure_indexed(session, org_id)
    rows = (
        await session.execute(
            select(KnowledgeChunk, ProcurementKnowledgeDocument)
            .join(ProcurementKnowledgeDocument, ProcurementKnowledgeDocument.id == KnowledgeChunk.document_id)
            .where(KnowledgeChunk.org_id == org_id, ProcurementKnowledgeDocument.is_active.is_(True))
        )
    ).all()
    if not rows:
        return []
    passages = {}
    for chunk, document in rows:
        key = f"doc:{document.id}:v{document.version}:chunk:{chunk.chunk_index + 1}"
        title = f"{document.title} (v{document.version})"
        if document.source_reference:
            title = f"{title} · {document.source_reference}"
        passages[str(chunk.id)] = Passage(id=key, title=title, text=chunk.content, score=0.0)
    lexical_hits = bm25_rank(question, list(passages.values()), limit=CANDIDATES)
    if get_settings().retrieval_mode == "bm25":
        return lexical_hits[:limit]
    lexical = [p.id for p in lexical_hits]
    by_key = {p.id: chunk_id for chunk_id, p in passages.items()}
    distance = KnowledgeChunk.embedding.cosine_distance(embed(question))
    vector_ids = (
        await session.scalars(
            select(KnowledgeChunk.id)
            .where(KnowledgeChunk.org_id == org_id, KnowledgeChunk.id.in_([uuid.UUID(c) for c in passages]))
            .order_by(distance)
            .limit(CANDIDATES)
        )
    ).all()
    semantic = [passages[str(i)].id for i in vector_ids]
    fused = rrf([lexical, semantic])
    ranked = sorted(fused.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return [
        Passage(
            id=key, title=passages[by_key[key]].title, text=passages[by_key[key]].text, score=round(score, 5)
        )
        for key, score in ranked
    ]
