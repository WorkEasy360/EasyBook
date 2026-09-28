import type { DateString, DateTimeString, DecimalString, UUID } from "@/lib/api/types";

/**
 * Ask Books contracts.
 *
 * VERIFIED 2026-09-17 against backend/ai/api/views.py (the serializers live in
 * the view module, so the serializer dump finds none), ai/orchestration/
 * service.py, ai/orchestration/assist.py, ai/sources.py and live responses
 * from the dev tenant.
 *
 * The rule this module exists to enforce: the assistant EXPLAINS figures, it
 * never produces them. `structured_data` is built by backend code from
 * deterministic tools; every entry in `sources` was registered by the backend
 * for this request (ai/orchestration/citations.py). The UI renders only what
 * the API returns and never fabricates a citation.
 */

/**
 * ai/sources.py :: Source.to_public.
 *
 * `route` is a DJANGO API path, not an in-app route — live capture:
 * "/api/v1/reports/profit-loss/?from_date=2026-07-01&to_date=2026-09-17",
 * "/api/v1/sales/invoices/<id>/", "/api/v1/documents/<id>/". Never link to it
 * directly; features/ai/sources.ts maps it onto the app's own pages.
 */
export interface AiSource {
  source_id: string;
  type: "report" | "invoice" | "bill" | "document" | (string & {});
  /** Report key for reports ("profit_and_loss"), record id otherwise. */
  id: string | null;
  label: string;
  route: string | null;
  document_id: string | null;
  page: number | null;
  section: string | null;
}

/** ai/orchestration/service.py :: AskResult.status */
export type AskStatus = "answered" | "partial" | "no_data" | "refused";

/** ai/orchestration/router.py :: INTENTS */
export type AskIntent = "structured" | "rag" | "both" | "conversational";

/** One deterministic tool run — service.py :: _structured_data. */
export interface AiToolResult {
  tool: string;
  /** Echo of the tool arguments, truncated server-side. */
  arguments: Record<string, unknown>;
  status: string;
  /** Scalar figures come back as decimal strings ("8899.99"). */
  summary: Record<string, unknown> | null;
  data: Record<string, unknown> | null;
  truncated: boolean;
  error_code: string | null;
  source_ids: string[];
}

export interface AiStructuredData {
  tool_results: AiToolResult[];
}

/**
 * Codes in `warnings` (service.py :: _finalize). Anything else is shown raw
 * rather than guessed at.
 */
export type AskWarning =
  | "unverified_citations_removed"
  | "citations_attached_from_context"
  | "ungrounded_figures_replaced"
  | "prompt_disclosure_blocked"
  | (string & {});

/** POST ai/ask/ (200) — ai/orchestration/service.py :: AskResult.to_response */
export interface AskResponse {
  /** Plain text. Guards strip HTML/javascript links, but render as text regardless. */
  answer: string;
  status: AskStatus;
  intent: AskIntent | null;
  sources: AiSource[];
  structured_data: AiStructuredData | null;
  request_id: string;
  conversation_id: UUID | null;
  /** "hybrid" | "lexical_only" when documents were searched, else null. */
  retrieval_mode: string | null;
  warnings: AskWarning[];
}

/** ai/api/views.py :: AskSerializer */
export interface AskRequest {
  question: string;
  conversation_id?: UUID;
  document_id?: UUID;
}

/** ai/api/views.py :: ConversationSerializer — owner-only list, paginated. */
export interface AiConversation {
  id: UUID;
  title: string;
  created_at: DateTimeString;
  last_message_at: DateTimeString | null;
}

/**
 * ai/api/views.py :: MessageSerializer. Stored messages keep the answer text
 * and sources but NOT structured_data or warnings. A user message has
 * `status: ""` and no sources.
 */
export interface AiMessage {
  id: UUID;
  role: "user" | "assistant" | (string & {});
  content: string;
  sources: AiSource[];
  status: AskStatus | "";
  request_id: string;
  created_at: DateTimeString;
}

/** GET ai/conversations/{id}/ — 404 `conversation_not_found` for another user's id. */
export interface AiConversationDetail extends AiConversation {
  messages: AiMessage[];
}

/** GET ai/usage/?from_date&to_date (VIEW_AI_USAGE) — views.py :: UsageView */
export interface AiUsage {
  from_date: DateString;
  to_date: DateString;
  totals: {
    requests: number;
    /** Sums are null when no row carries the column. */
    input_tokens: number | null;
    output_tokens: number | null;
    cached_input_tokens: number | null;
    embedding_tokens: number | null;
  };
  by_model: Array<{
    provider: string;
    model: string;
    requests: number;
    input_tokens: number | null;
    output_tokens: number | null;
    /**
     * Present only when AI_MODEL_PRICING has the model. An estimate at the
     * CURRENT configured per-million prices, in whatever unit those prices
     * were configured in — the API names no currency.
     */
    estimated_cost?: DecimalString;
  }>;
  by_status: Array<{ status: string; requests: number }>;
  by_feature: Array<{ feature: string; requests: number }>;
}

/** POST ai/drafts/payment-reminder/ — ai/orchestration/assist.py :: draft_payment_reminder */
export interface PaymentReminderDraft {
  subject: string;
  body: string;
  status: "draft";
  /** Always false: drafting never sends anything. */
  sent: false;
  /** The authoritative invoice facts the draft was checked against. */
  facts: {
    customer_name: string;
    invoice_number: string;
    invoice_date: DateString;
    due_date: DateString;
    currency: string;
    amount_due: DecimalString;
  };
  sources: AiSource[];
  request_id: string;
}

/** POST ai/suggestions/expense-account/ — assist.py :: suggest_expense_account */
export interface ExpenseAccountSuggestionResponse {
  /** From this organization's active expense accounts only, or null. */
  suggested_account: { id: UUID; code: string; name: string } | null;
  confidence: "low" | "medium" | "high";
  reasoning: string;
  /** Always false: a suggestion applies nothing. */
  applied: false;
  request_id: string;
}

/** GET ai/documents/{id}/index/ — views.py :: _index_payload */
export interface DocumentIndexState {
  document_id: UUID;
  status: string;
  reason_code: string;
  chunk_count: number;
  embedding_config_key: string;
  indexed_at: DateTimeString | null;
}
