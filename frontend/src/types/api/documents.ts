import type { DateString, DateTimeString, DecimalString, UUID } from "@/lib/api/types";

/**
 * Documents contracts.
 *
 * VERIFIED 2026-09-17 against `scripts/dump-serializers.py` (MODULES=documents),
 * backend/documents/api/views.py and live responses from the dev tenant — not
 * transcribed from memory. Facts that shape the UI:
 *
 *  - `storage_key` is never serialized; the only way to the bytes is
 *    POST documents/{id}/download/, which mints a short-lived URL.
 *  - GET documents/{id}/links/ is a BARE ARRAY, not a paginated envelope.
 *  - GET documents/{id}/ocr/ is 404 `ocr_result_not_found` until a worker has
 *    processed the document.
 *  - There is no GET for a review: DocumentReview is only returned by the
 *    POST documents/{id}/review/ that creates it.
 *  - There is no folder or tag REST resource, and no endpoint that lists
 *    documents (or links) for a given business record.
 */

/** documents/models/document.py :: DocumentType */
export type DocumentType =
  | "receipt"
  | "invoice"
  | "bill"
  | "bank_statement"
  | "tax_document"
  | "contract"
  | "attachment"
  | "general";

/**
 * documents/models/document.py :: UploadStatus. Transitions (backend-enforced):
 * uploading → scanning | failed; scanning → ready | quarantined | failed;
 * ready → archived. failed, quarantined and archived are terminal.
 */
export type UploadStatus = "uploading" | "scanning" | "ready" | "failed" | "quarantined" | "archived";

/**
 * documents/models/document.py :: OCRStatus. not_requested → queued →
 * processing → needs_review | completed | failed; needs_review → completed
 * (only by an approving review); failed → queued (a retry).
 */
export type OcrStatus = "not_requested" | "queued" | "processing" | "needs_review" | "completed" | "failed";

/** documents.DocumentSerializer — every field read-only. */
export interface Document {
  id: UUID;
  title: string;
  original_filename: string;
  mime_type: string;
  /** Bytes. A plain JSON number (BigIntegerField). */
  file_size: number;
  checksum_sha256: string;
  document_type: DocumentType;
  upload_status: UploadStatus;
  ocr_status: OcrStatus;
  folder: UUID | null;
  uploaded_by: UUID | null;
  retention_until: DateString | null;
  legal_hold: boolean;
  archived_at: DateTimeString | null;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** POST documents/upload/ (201) — the document plus same-checksum matches in this organization. */
export interface DocumentUploadResponse extends Document {
  /** A hint only: the backend never rejects a duplicate. */
  possible_duplicate_ids: UUID[];
}

/** documents.DocumentUpdateSerializer — PATCH documents/{id}/ (MANAGE_DOCUMENTS). */
export interface DocumentUpdateInput {
  title?: string;
  document_type?: DocumentType;
  folder_id?: UUID | null;
}

/** documents/models/link.py :: LinkedEntityType */
export type LinkedEntityType =
  | "invoice"
  | "sales_order"
  | "customer"
  | "bill"
  | "purchase_order"
  | "vendor"
  | "expense"
  | "bank_transaction"
  | "project"
  | "journal_entry"
  | "einvoice"
  | "ewaybill";

/** documents.DocumentLinkSerializer */
export interface DocumentLink {
  id: UUID;
  entity_type: LinkedEntityType;
  entity_id: UUID;
  created_by: UUID | null;
  created_at: DateTimeString;
}

/**
 * documents.DocumentLinkCreateSerializer. The backend resolves `entity_id`
 * through the target module under the caller's tenant — an id from another
 * organization fails with `entity_not_found`. A repeat of an existing link
 * returns the existing row.
 */
export interface DocumentLinkCreateInput {
  entity_type: LinkedEntityType;
  entity_id: UUID;
}

/** documents.OCRResultSerializer — one row per document, overwritten on re-run. */
export interface OcrResult {
  provider: string;
  provider_version: string;
  /** Untrusted extracted text. Render as text, never as HTML. */
  raw_text: string;
  /** Provider-shaped extraction. Never an accounting figure. */
  structured_payload: Record<string, unknown>;
  /** "0.0000".."1.0000", or null when the provider gave none. */
  confidence: DecimalString | null;
  error_code: string;
  error_message: string;
  processed_at: DateTimeString | null;
}

/** documents/models/review.py :: ReviewStatus */
export type ReviewStatus = "pending" | "approved" | "rejected";

/** documents.DocumentReviewSerializer — returned by POST documents/{id}/review/ only. */
export interface DocumentReview {
  status: ReviewStatus;
  reviewer: UUID | null;
  reviewed_at: DateTimeString | null;
  corrected_fields: Record<string, unknown>;
  notes: string;
}

/**
 * documents.ReviewActionSerializer. `corrected_fields` is ignored by the
 * backend on a rejection (reject_review stores none).
 */
export interface ReviewActionInput {
  status: "approved" | "rejected";
  notes?: string;
  corrected_fields?: Record<string, string>;
}

/**
 * POST documents/{id}/download/ — NOT a GET. Live capture (local storage):
 * `{"url": "/api/v1/documents/local-storage/<signed-token>/", "expires_in": 300}`.
 * With S3 the url is an absolute presigned https URL.
 */
export interface DownloadGrant {
  url: string;
  /** Seconds the URL stays valid (DOCUMENT_SIGNED_URL_TTL_SECONDS). */
  expires_in: number;
}

/** Human file size. Bytes are a count, not money — float arithmetic is fine here. */
export function formatFileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unitIndex]}`;
}
