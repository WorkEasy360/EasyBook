# DOCUMENTS

PURPOSE
Secure, tenant-scoped document management and OCR foundation (Phase 9). Sits ABOVE every module it can link to (like `reports`), never the reverse.

OWNS
- `models/document.py` — `Document` plus `UploadStatus`/`OCRStatus` state machines (`UPLOAD_STATUS_TRANSITIONS`/`OCR_STATUS_TRANSITIONS`) enforced only through `services/transitions.py` — never edit `.upload_status`/`.ocr_status` directly.
- `models/link.py` — `DocumentLink`. `entity_id` is a UUID, not a real FK or `GenericForeignKey`: `services/links.py::_resolve_entity` proves same-tenant ownership by resolving it through the TARGET module's own `TenantManager` under the caller's ambient tenant context, so a cross-org id simply fails to resolve. Imports of sales/purchases/banking/projects/accounting/compliance models are lazy (inside `_resolvers()`) for exactly this layering reason. Extend `LinkedEntityType` + `_resolvers()` together when a new linkable type is needed.
- `models/folder.py`, `models/tag.py` — lightweight, not exposed as their own REST resource yet (phase section 25 didn't ask for one); assign via `Document.folder` / `services/tags.py`.
- `models/ocr_result.py` — `OCRResult`, one row per document (re-run overwrites it under a lock, no history table). `raw_text` capped at `MAX_RAW_TEXT_CHARS`.
- `models/review.py` — `DocumentReview`, one row per document. `services/review.py::approve_review`/`reject_review` are idempotent-by-construction on a repeat of the SAME outcome (locked no-op — this is how "two reviewers approve concurrently" resolves to one authoritative approval); the OPPOSITE outcome on an already-finalized review raises `review_already_finalized` instead of silently overwriting.
- `storage/` — `DocumentStorage` ABC (`get_storage()` factory). `local.py` (dev/test default, `FileSystemStorage` under `settings.DOCUMENT_LOCAL_STORAGE_ROOT`, signed via `django.core.signing` + the proxy view) and `s3.py` (production, lazy `boto3` import so dev/test never need the package installed).
- `services/validation.py` — extension + MIME + magic-byte allowlist (`ALLOWED_TYPES`). Deliberately minimal: pdf/png/jpg/jpeg/csv/txt only.
- `services/malware_scan.py` — `EicarAwareScanner`, a mock scanner using the real industry-standard EICAR test string. **NOT a production antivirus integration** — replace `DOCUMENT_MALWARE_SCANNER_BACKEND` with a real engine (e.g. clamd) before production go-live.
- `services/uploads.py` — `upload_document` is deliberately NOT wrapped in one `@transaction.atomic`: if `storage.put` fails after the `Document` row commits, the row must survive as an explicit `FAILED` record, not be rolled back into a silent gap.
- `services/ocr.py` + `tasks.py` — `request_ocr` only enqueues (`run_ocr_task.delay`); `process_ocr` is the Celery task body and never raises for an extraction failure (stored as `OCRResult.error_code`/`error_message` instead). Confidence routing: `>= DOCUMENT_OCR_CONFIDENCE_HIGH` -> `COMPLETED`, else `NEEDS_REVIEW`.
- `ocr/providers/` — `OCRProvider` ABC. Only `ManualOCRProvider` is implemented: it does NOT perform real text recognition (no verified OCR engine available in this environment) and always returns zero confidence, routing every document to `NEEDS_REVIEW`. Wire a real provider (Textract/Document AI/Azure) behind this same interface when one is available and verified against its own docs.
- `search/` — `search_documents`: plain `icontains` over title/filename/`OCRResult.raw_text`, real Postgres capability, not a stub GIN-indexed `SearchVector`. Upgrade only on measured need (phase section 18 — do not introduce Elasticsearch/OpenSearch pre-emptively).
- `selectors.py` — `find_possible_duplicates` (checksum match, hint only — never auto-rejected).

DOES NOT OWN (DELIBERATELY DEFERRED)
- **Draft business-record creation from OCR** (phase section 17/section 39, marked optional/"if implemented"). Not built: `ManualOCRProvider` always returns empty `fields`, so there is nothing real to draft a Bill/Invoice/Expense from yet, and building the bridge against a provider that can never populate it would be dead code with no way to genuinely exercise "invalid extraction rejected". Revisit once a real OCR provider exists. The bridge, when built, MUST go through the target module's own domain service (e.g. `purchases.services.bills.create_bill`) — `documents` must never insert a `Bill`/`Invoice`/`Expense`/`JournalEntry` row directly (root CLAUDE.md pipeline rule).
- RAG/embeddings — owned by `ai` (Phase 10, `ai/CLAUDE.md`), which sits ABOVE this module and reacts to `Document`/`OCRResult` saves via receivers in `ai/rag/signals.py`. `documents` must never import `ai`. Any change to `UploadStatus`/`OCRStatus` semantics must be mirrored in `ai/rag/text_source.py` and `ai/retrieval/search.py::retrievable_chunks`.
- A dedicated Folder/Tag REST resource — the phase's own API list (section 25) didn't ask for one; `folder_id`/tags are set through the Document endpoints and `services/tags.py`.

INVARIANTS
- Every model here is `TenantScopedModel` with a matching entry in `migrations/0002_enable_rls.py` — `documents/tests/test_rls.py` asserts this exhaustively, mirroring `purchases/tests/test_rls.py`.
- Every service that accepts a `document`/`link` instance from a caller RE-RESOLVES it via the tenant-scoped manager first (`Document.objects.get(pk=document.pk)`) rather than trusting the passed-in Python object — defense in depth for a service called from outside the API layer (a management command, another service) that might hold a stale cross-org reference. See `services/downloads.py`, `services/uploads.py`, `services/links.py`.
- No class-level tenant-scoped `.objects.all()` querysets in `api/views.py` — always built in `get_queryset()`.
- `storage_key` is never serialized to the client (`api/serializers.py::DocumentSerializer`) and never derived from the client filename (`services/uploads.py::generate_storage_key` is a fresh UUID).
- OCR output is untrusted: `OCRResult` fields are never used to auto-post `JournalEntry`/`Invoice`/`Bill`/`Expense`/`Payment`/`StockMovement` (root CLAUDE.md). Reviewing/approving only sets `Document.ocr_status = COMPLETED` and records `DocumentReview.corrected_fields` — it does not create any accounting or business record on its own.
- Quarantined documents are never downloadable (`services/downloads.py::get_download_url`) and never returned by search (`search/__init__.py` excludes `UploadStatus.QUARANTINED` unconditionally).
- Signed URLs are short-lived (`settings.DOCUMENT_SIGNED_URL_TTL_SECONDS`, default 300s) regardless of backend.
- `LocalStorageDownloadView` (the local backend's presigned-URL equivalent) is intentionally NOT tenant-gated — possession of the signed, unexpired token is the credential, exactly like a real S3 presigned URL. It cannot re-check tenant/quarantine state at serve time (a `TenantScopedModel` query with no GUC set is blocked by RLS regardless of manager), so that check happens once, at `get_download_url` mint time. The residual "quarantined after the token was already minted, within the TTL window" race is inherent to presigned URLs generally, not specific to this backend.

SECURITY
- Upload validation never trusts the client `Content-Type` header alone — extension, canonical MIME, and (where the format allows) magic bytes must all agree (`services/validation.py`).
- `api/views.py::DocumentUploadView.post` checks `UploadedFile.size` against `services/validation.py::max_upload_size()` BEFORE calling `.read()` — otherwise an oversized upload is pulled fully into memory before `validate_upload()` ever gets a chance to reject it on size, a memory-exhaustion DoS available to any authenticated user holding `UPLOAD_DOCUMENT` (phase 12 security review, 2026-09-16). `max_upload_size()` resolves the category from the filename alone, matching `validate_upload()`'s own extension-based lookup, specifically so it never needs to read content either.
- Malware scanning is a hook, not a proven integration — see OWNS above.
- OCR raw text is untrusted third-party/extracted content: matched against in search, never rendered as HTML by this backend. A frontend must escape it like any other user-supplied string (prompt-injection-aware handling if it is ever fed to an LLM in a later phase).

TESTS
`tests/test_rls.py` (whole-app RLS coverage + fail-closed), `tests/test_security.py` (cross-tenant rejection per operation), `tests/test_ocr.py`/`tests/test_review.py` (each has a `TransactionTestCase`-based concurrency race), `tests/test_uploads.py` (storage-failure handling — a `Document` row surviving as `FAILED` rather than vanishing).

TOKEN DISCIPLINE
Do not duplicate root, `core`, or `audit` CLAUDE.md content here. Read `sales`/`purchases`/`banking`/`projects`/`accounting`/`compliance` CLAUDE.md only when touching `services/links.py`'s resolver map.
