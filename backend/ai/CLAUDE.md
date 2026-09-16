# AI (ASK BOOKS)

PURPOSE
Tenant-safe advisory AI and RAG layer over deterministic EasyBook services (Phase 10). Top layer: imports `reports`, `documents`, `sales`, `purchases`, `accounting`, `inventory`, `banking`, `compliance`, `core`/`authz`/`audit`; nothing may import `ai`.

INVARIANTS
- AI never owns financial truth. Figures come only from existing selectors via `tools/`; `structured_data` in every response is built by backend code, never parsed from model prose.
- Numbers -> structured tools. Document content -> RAG. Both -> tools first, then retrieval, then synthesis (`orchestration/service.py`). `orchestration/router.py` guardrails: a numeric question is never RAG-only; document signals are never answered as chat.
- Authorization before retrieval, and at EVERY tool call (`tools/executor.py`): tenant context == caller org, active membership re-read, declared permissions, strict schema (unknown keys rejected), handler in an always-rolled-back savepoint, bounded output.
- Retrieved documents are untrusted: they only ever reach a model as `UntrustedDocument` blocks, in a turn that offers NO tools. Tool planning sees only the user's own prior questions (never assistant history, which can quote documents).
- No SQL/query tool, no write tool. `tools/registry.py::register` refuses `read_only=False`; tests assert tool names.
- Citations must be genuine: `orchestration/citations.py` keeps only source_ids produced for this request; unknown inline ids are stripped.
- Output guards (`orchestration/guards.py`): system-prompt echo -> refused; a figure not present in supplied data -> replaced with authoritative figures (`status: partial`); HTML/javascript links stripped. Frontend must still sanitize Markdown.
- Chunks/embeddings tenant-scoped with RLS (`migrations/0002_enable_rls.py`). Retrieval filters (org, READY, OCR COMPLETED for OCR chunks, current index hash, embedding config key) are IN the SQL — never post-filtered.
- Archived/quarantined/failed documents: not retrievable at query time; chunks purged in the same transaction (`rag/signals.py`); never indexed.
- Bounded everything: tool calls, LLM rounds, tool output chars/rows, context chars, retrieved chunks, question length, per-request deadline, retries (<=5), rate limits. No setting means "unlimited".
- Tests never call live providers. Only `fake` LLM/embedding providers exist; `config.py` refuses to boot Ask Books on a fake provider without `AI_ALLOW_FAKE_PROVIDERS`.

DOES NOT OWN
Accounting calculations, tax/GST calculation (there is NO net GST payable figure — `get_gst_summary` says so explicitly), inventory valuation, bank reconciliation, document security/state (`documents`), financial posting.

PROVIDERS
- `providers/base.py` `LLMProvider` (generate/structured_output/tool_calling; streaming deferred), `embeddings/base.py` `EmbeddingProvider` (embed_documents/embed_query) — separate configs.
- Adding a vendor: implement the ABC, map vendor errors onto `providers/errors.py` (only `retryable` errors are retried), honour `request.timeout_seconds`, add the name to `config.KNOWN_*_PROVIDERS`, verify against the vendor's current docs with real credentials. Keep SDKs lazily imported. `retry.run_with_timeout` hard-bounds every call regardless.
- Changing embedding model/dims/version changes `EmbeddingSpec.config_key`; old vectors stop matching. Run `manage.py ai_reindex_documents [--force]`.

CHUNKING (`rag/chunking.py`, defaults in settings)
Characters, not tokens (token counts are model-specific and would churn content hashes). Target 1200 / overlap 200 / max 2000 chars (~300/50/500 English tokens): big enough for a full contract clause, small enough that 6 chunks fit well inside the 48k-char context budget. Pages (`\f`) are hard boundaries so page citations are exact; then paragraphs, sentences, word-boundary hard split. Bump `ChunkingConfig.version` when the algorithm changes.

INDEXING (`rag/indexing.py`)
Text sources: OCR text only when `ocr_status=COMPLETED`; native text/plain + text/csv. Anything else is `not_indexable` (never invented). Idempotent by content hash (text + chunking + embedding config). Embed outside locks; write under `SELECT ... FOR UPDATE` on the Document row with state/hash re-check; replace chunk set atomically. Auto-enqueued on commit when `AI_AUTO_INDEX_DOCUMENTS` (off in tests). Indexing is audited (`ai.DocumentIndex`).

RETRIEVAL (`retrieval/search.py`)
Hybrid RRF: `sum w/(k+rank)`, k=60, weights vector 1 / lexical (Postgres FTS `english`, stored GIN tsvector) 1 / exact identifier (ILIKE) 2; deterministic tie-break. Vector hits only above `AI_VECTOR_MIN_SIMILARITY` (0.15, calibrated for the FAKE embedding on the eval corpus — recalibrate for any real model). Embedding outage -> `lexical_only`. No reranker until a real embedding model is evaluated. No ANN index yet: exact cosine scan within one tenant is correct and adequate at current volume; when adding HNSW, use a partial expression index per embedding config (`(embedding::vector(N)) vector_cosine_ops WHERE embedding_config_key = ...`) and re-measure.

PGVECTOR / PRODUCTION
`vector` is an untrusted extension: a superuser (RDS: `rds_superuser`) must run `CREATE EXTENSION IF NOT EXISTS vector;` before `migrate`. `migrations/0001_initial.py` only verifies it and fails loudly if absent. Local: `pgvector/pgvector:pg16-trixie` + `infrastructure/postgres-init/02-pgvector.sql` (also installs into `template1` for the test DB). CI does the same.

DATA / RETENTION
`AIRequestLog`: metadata + token counts only (no prompts/questions/answers); cost is an estimate computed at read time from `AI_MODEL_PRICING`, never stored. `AIConversation`/`AIMessage`: owner-scoped, question/answer/sources only, never embedded. `rag/tasks.py::purge_expired_ai_data` enforces `AI_CONVERSATION_RETENTION_DAYS`/`AI_REQUEST_LOG_RETENTION_DAYS` — schedule it with celery beat (no beat schedule exists in this repo yet).

API (`api/urls.py`)
`POST ai/ask/` (Idempotency-Key replays per user), `GET ai/conversations/[id]/`, `GET|POST ai/documents/{id}/index/`, `POST ai/documents/{id}/reindex/`, `GET ai/usage/` (Owner/Admin), `POST ai/drafts/payment-reminder/` (draft only, never sent), `POST ai/suggestions/expense-account/` (allowlisted suggestion, applies nothing). Failures are RETURNED as envelopes, not raised, so ATOMIC_REQUESTS keeps the failure's AIRequestLog row. Permissions: `ai.ask` (all roles; grants no data), `ai.manage_index` (Accountant+), `ai.view_usage` (Owner/Admin).

PROMPTS
`prompts/__init__.py`, versioned by `PROMPT_VERSION`; `tests/test_prompts.py` pins a fingerprint — any wording change must bump the version and re-pin.

DEFERRED
Live vendor providers (no credentials to verify), streaming, reranking, ANN index, reconciliation suggestions (banking's deterministic matcher should expose ranked candidates first), per-document ACLs (documents has none; would belong in `retrievable_chunks` SQL), org-level AI settings model (global flag only).

EVALUATION
`evaluations/datasets.py` (curated corpus incl. cross-tenant twins + injection doc) + `evaluations/runner.py`; gated in `tests/test_evaluation.py` (exact & tenant hit rate 1.0, zero forbidden/negative hits, Recall@5 >= 0.9) plus financial grounding cases comparing structured values to selectors. Fake-embedding numbers measure pipeline correctness, not semantic quality — re-baseline with a real model.

TOKEN DISCIPLINE
Do not duplicate root, core, documents or reports CLAUDE.md. Read `orchestration/service.py` + `tools/executor.py` first.
