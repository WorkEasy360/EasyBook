"""Tenant-safe, permission-filtered hybrid retrieval over DocumentChunk.

AUTHORIZATION IS PART OF THE QUERY, NEVER A POST-FILTER (phase section 15):
every candidate list is produced by ONE SQL statement that already carries
  * RLS (the tenant GUC) and the TenantManager organization filter,
  * an explicit organization_id predicate,
  * document state at query time: Document.upload_status = READY, OCR-sourced
    chunks only while Document.ocr_status = COMPLETED,
  * index currency: the chunk belongs to the document's CURRENT indexed
    content (DocumentIndex.status = INDEXED and content_hash matches), so a
    superseded chunk set is never retrievable even before its purge,
  * for vectors, the active embedding config key (vectors from different
    models are never compared).
Nothing cross-tenant or non-retrievable is ever loaded into Python.

HYBRID RANKING (phase section 17) — deterministic Reciprocal Rank Fusion:
    score(chunk) = sum over lists L containing it of  w_L / (k + rank_L)
with k = AI_RRF_K (default 60), rank starting at 1, and weights
vector 1.0, lexical 1.0, exact-identifier 2.0 (an exact INV-1024/GSTIN hit is
the strongest evidence a chunk is the one asked about). Ties break on
(document_id, chunk_index). A vector candidate counts only above
AI_VECTOR_MIN_SIMILARITY cosine similarity, so an irrelevant corpus returns
NOTHING rather than its least-bad chunk (phase section 68).

No reranker: not introduced until baseline hybrid retrieval has been
evaluated against a real embedding model (phase section 18, ai/CLAUDE.md).
"""

import re
import time
from dataclasses import dataclass, field

from django.contrib.postgres.search import SearchQuery, SearchRank
from django.db.models import F, Q

from ai.config import get_ai_config
from ai.embeddings import active_embedding_spec, embed_query
from ai.fields import CosineDistance
from ai.models.chunk import DocumentChunk, IndexStatus, TextSource
from ai.providers.errors import AIProviderError
from ai.sources import Source
from documents.models.document import OCRStatus, UploadStatus

_WORD = re.compile(r"[A-Za-z0-9]+")
# Identifier-like tokens: contain a digit AND a letter (INV-1024, a GSTIN,
# BILL/2026/07), or are long digit runs (account/reference numbers).
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9/_-]{2,39}")
MAX_QUERY_TOKENS = 32
MAX_IDENTIFIERS = 5
LIST_WEIGHTS = {"vector": 1.0, "lexical": 1.0, "exact": 2.0}


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    document_id: str
    document_title: str
    document_type: str
    chunk_index: int
    page_number: int | None
    section: str
    text: str
    score: float
    matched_by: tuple[str, ...]
    similarity: float | None = None

    @property
    def source(self) -> Source:
        return Source(
            source_id=f"chunk:{self.chunk_id}",
            type="document",
            id=self.document_id,
            label=self.document_title,
            route=f"/api/v1/documents/{self.document_id}/",
            document_id=self.document_id,
            page=self.page_number,
            section=self.section or None,
        )


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    mode: str  # "hybrid" | "lexical_only"
    embedding_tokens: int | None = None
    latency_ms: int = 0
    candidate_counts: dict = field(default_factory=dict)


def retrievable_chunks(*, organization):
    return (
        DocumentChunk.objects.filter(
            organization_id=organization.id,
            document__organization_id=organization.id,
            document__upload_status=UploadStatus.READY,
            document__ai_index__status=IndexStatus.INDEXED,
            document__ai_index__content_hash=F("index_content_hash"),
        )
        .filter(Q(text_source=TextSource.NATIVE_TEXT) | Q(text_source=TextSource.OCR, document__ocr_status=OCRStatus.COMPLETED))
    )


def query_tokens(query: str) -> list[str]:
    seen, tokens = set(), []
    for token in _WORD.findall(query.lower()):
        if len(token) >= 2 and token not in seen:
            seen.add(token)
            tokens.append(token)
    return tokens[:MAX_QUERY_TOKENS]


def query_identifiers(query: str) -> list[str]:
    found = []
    for token in _IDENTIFIER.findall(query):
        token = token.strip("/_-")
        has_digit = any(ch.isdigit() for ch in token)
        has_alpha = any(ch.isalpha() for ch in token)
        if (has_digit and has_alpha and len(token) >= 4) or (token.isdigit() and len(token) >= 6):
            if token.upper() not in {t.upper() for t in found}:
                found.append(token)
    return found[:MAX_IDENTIFIERS]


def _vector_candidates(base, query_vector, spec, limit, min_similarity):
    if not any(query_vector):
        return []
    rows = (
        base.filter(embedding_config_key=spec.config_key, embedding__isnull=False)
        .annotate(distance=CosineDistance("embedding", query_vector))
        .filter(distance__lte=1.0 - min_similarity)
        .order_by("distance", "document_id", "chunk_index")
        .values_list("id", "distance")[:limit]
    )
    return [(chunk_id, 1.0 - distance) for chunk_id, distance in rows]


def _lexical_candidates(base, query, limit):
    tokens = query_tokens(query)
    if not tokens:
        return []
    # Tokens are strictly [a-z0-9]+, so the raw tsquery can only ever be a
    # disjunction of plain lexemes — no tsquery operators from user or model
    # text reach PostgreSQL, and the whole string is a bound parameter.
    ts_query = SearchQuery(" | ".join(tokens), search_type="raw", config="english")
    rows = (
        base.filter(search_vector=ts_query)
        .annotate(rank=SearchRank(F("search_vector"), ts_query))
        .order_by("-rank", "document_id", "chunk_index")
        .values_list("id", flat=True)[:limit]
    )
    return list(rows)


def _exact_candidates(base, query, limit):
    identifiers = query_identifiers(query)
    if not identifiers:
        return []
    condition = Q()
    for identifier in identifiers:
        condition |= Q(text__icontains=identifier)  # Django escapes % and _
    return list(base.filter(condition).order_by("document_id", "chunk_index").values_list("id", flat=True)[:limit])


def hybrid_search(
    *, organization, query: str, limit: int | None = None, document_ids=None, document_type: str | None = None,
    deadline: float | None = None,
) -> RetrievalResult:
    """Callers MUST have authorized the user for documents first — use
    ai.tools (search_documents), which enforces permission, not this
    function directly from a view."""
    started = time.monotonic()
    config = get_ai_config()
    limit = min(limit or config.max_retrieved_chunks, config.max_retrieved_chunks)
    candidates = config.retrieval_candidates

    base = retrievable_chunks(organization=organization)
    if document_ids is not None:
        base = base.filter(document_id__in=list(document_ids))
    if document_type:
        base = base.filter(document__document_type=document_type)

    mode = "hybrid"
    embedding_tokens = None
    vector_hits: list[tuple] = []
    try:
        embedded = embed_query(query, deadline=deadline)
        embedding_tokens = embedded.usage_tokens
        vector_hits = _vector_candidates(
            base, embedded.vectors[0], active_embedding_spec(), candidates, config.vector_min_similarity
        )
    except AIProviderError:
        # Embedding outage degrades to lexical + exact retrieval rather than
        # failing the whole question; the response reports the mode.
        mode = "lexical_only"

    lists = {
        "vector": [chunk_id for chunk_id, _ in vector_hits],
        "lexical": _lexical_candidates(base, query, candidates),
        "exact": _exact_candidates(base, query, candidates),
    }
    similarity = dict(vector_hits)

    scores: dict = {}
    matched: dict = {}
    for name, ids in lists.items():
        for rank, chunk_id in enumerate(ids, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + LIST_WEIGHTS[name] / (config.rrf_k + rank)
            matched.setdefault(chunk_id, []).append(name)

    if not scores:
        return RetrievalResult(
            chunks=[], mode=mode, embedding_tokens=embedding_tokens,
            latency_ms=int((time.monotonic() - started) * 1000), candidate_counts={k: len(v) for k, v in lists.items()},
        )

    # Re-load the fused winners through the SAME authorized base queryset.
    rows = {
        row.id: row
        for row in base.filter(id__in=list(scores)).select_related("document").only(
            "id", "document_id", "document__title", "document_type", "chunk_index", "page_number", "section", "text",
        )
    }
    ordered = sorted(
        (chunk_id for chunk_id in scores if chunk_id in rows),
        key=lambda cid: (-round(scores[cid], 12), str(rows[cid].document_id), rows[cid].chunk_index),
    )[:limit]
    chunks = [
        RetrievedChunk(
            chunk_id=str(cid),
            document_id=str(rows[cid].document_id),
            document_title=rows[cid].document.title,
            document_type=rows[cid].document_type,
            chunk_index=rows[cid].chunk_index,
            page_number=rows[cid].page_number,
            section=rows[cid].section,
            text=rows[cid].text,
            score=round(scores[cid], 12),
            matched_by=tuple(matched[cid]),
            similarity=round(similarity[cid], 6) if cid in similarity else None,
        )
        for cid in ordered
    ]
    return RetrievalResult(
        chunks=chunks, mode=mode, embedding_tokens=embedding_tokens,
        latency_ms=int((time.monotonic() - started) * 1000), candidate_counts={k: len(v) for k, v in lists.items()},
    )


def document_excerpts(*, organization, document_id, limit: int) -> list[RetrievedChunk]:
    """The first `limit` retrievable chunks of one document in reading order
    (for "summarize this document"). Same authorized base queryset."""
    rows = (
        retrievable_chunks(organization=organization)
        .filter(document_id=document_id)
        .select_related("document")
        .order_by("chunk_index")[:limit]
    )
    return [
        RetrievedChunk(
            chunk_id=str(row.id), document_id=str(row.document_id), document_title=row.document.title,
            document_type=row.document_type, chunk_index=row.chunk_index, page_number=row.page_number,
            section=row.section, text=row.text, score=0.0, matched_by=("document",),
        )
        for row in rows
    ]
