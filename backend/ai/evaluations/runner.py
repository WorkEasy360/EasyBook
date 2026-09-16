"""Retrieval evaluation runner.

`load_corpus` ingests the corpus through the REAL upload + indexing
pipeline; `evaluate_retrieval` runs every case through the REAL hybrid
retrieval under the case organization's tenant context and reports:

  * recall_at_k      — fraction of cases with relevant docs whose top-K
                       contains at least one relevant document
  * per-category hit rate
  * forbidden_hits   — any forbidden (e.g. other-tenant, injection) document
                       returned at any rank: MUST be zero
  * negative_leaks   — negative cases that returned anything

Used by ai/tests/test_evaluation.py as a regression gate. Numbers produced
with the fake hashing embedding measure pipeline correctness, not the
semantic quality of any production embedding model — re-baseline when a
real embedding provider is configured.
"""

from collections import defaultdict

from ai.evaluations.datasets import CASES, CORPUS
from ai.rag.indexing import index_document
from ai.retrieval.search import hybrid_search
from core.tenancy import tenant_context
from documents.services.uploads import upload_document


def load_corpus(*, organizations: dict, users: dict) -> dict:
    """organizations/users: {"A": ..., "B": ...}. Returns {document_id: key}."""
    keys = {}
    for doc in CORPUS:
        organization, user = organizations[doc.org], users[doc.org]
        with tenant_context(organization_id=organization.id, user_id=user.id):
            document = upload_document(
                organization=organization, uploaded_by=user, content=doc.text.encode("utf-8"),
                original_filename=f"{doc.key}.txt", title=doc.title, declared_content_type="text/plain",
                document_type=doc.document_type,
            )
            index_document(document_id=document.pk)
        keys[str(document.pk)] = doc.key
    return keys


def evaluate_retrieval(*, organizations: dict, document_keys: dict, k: int = 5) -> dict:
    per_case, hits, totals = [], defaultdict(int), defaultdict(int)
    forbidden_hits, negative_leaks, recall_cases, recall_hits = [], [], 0, 0
    for case in CASES:
        organization = organizations[case.org]
        with tenant_context(organization_id=organization.id):
            result = hybrid_search(organization=organization, query=case.query, limit=k)
        ranked = [document_keys.get(chunk.document_id, "?") for chunk in result.chunks]
        top_keys = list(dict.fromkeys(ranked))[:k]
        hit = bool(set(top_keys) & set(case.relevant)) if case.relevant else None
        if case.relevant:
            recall_cases += 1
            recall_hits += int(hit)
            totals[case.category] += 1
            hits[case.category] += int(hit)
        leaked = sorted(set(ranked) & set(case.forbidden))
        if leaked:
            forbidden_hits.append({"case": case.case_id, "documents": leaked})
        if case.category == "negative" and ranked:
            negative_leaks.append(case.case_id)
        per_case.append({"case": case.case_id, "category": case.category, "top": top_keys, "hit": hit})
    return {
        "k": k,
        "recall_at_k": round(recall_hits / recall_cases, 4) if recall_cases else None,
        "hit_rate_by_category": {cat: round(hits[cat] / totals[cat], 4) for cat in sorted(totals)},
        "forbidden_hits": forbidden_hits,
        "negative_leaks": negative_leaks,
        "cases": per_case,
    }
