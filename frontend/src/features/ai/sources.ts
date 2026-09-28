import { ApiError, errorCodeOf } from "@/lib/api/errors";
import type { AiSource, AskStatus } from "@/types/api/ai";

/**
 * Ask Books presentation helpers — pure, so they are unit-tested.
 */

/** Shown verbatim whenever the backend says the assistant is off or unreachable. */
export const AI_DISABLED_MESSAGE = "AI Assistant is not currently enabled.";

/**
 * ai/orchestration/errors.py :: ai_disabled() (403, AI_ASK_BOOKS_ENABLED off)
 * and ai/providers/errors.py :: AIProviderError (503 `ai_unavailable`, also
 * what a misconfigured or unauthenticated provider maps to). A plain 403
 * `forbidden` is a permission problem, not "disabled", and keeps its own message.
 */
const DISABLED_CODES = new Set(["ai_disabled", "ai_unavailable"]);

export function isAiDisabledError(error: unknown): boolean {
  return error instanceof ApiError && DISABLED_CODES.has(errorCodeOf(error) ?? "");
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Report source id → the app page that shows that report.
 *
 * Keys are every `report="…"` id the backend's AI tools emit (grep
 * backend/ai for report_source). Values are the routes that exist under
 * src/app/(app)/reports, /accounting, /tax and /inventory — checked against
 * the built routes, not inferred from a naming pattern.
 */
const REPORT_ROUTES: Record<string, string> = {
  profit_and_loss: "/reports/profit-loss",
  balance_sheet: "/reports/balance-sheet",
  cash_flow: "/reports/cash-flow",
  trial_balance: "/accounting/trial-balance",
  general_ledger: "/accounting/general-ledger",
  ar_ageing: "/reports/receivables/ageing",
  outstanding_bills: "/reports/payables/outstanding-bills",
  inventory_summary: "/inventory",
  inventory_valuation: "/reports/inventory/valuation",
  low_stock: "/reports/inventory/low-stock",
  gst_summary: "/tax/summary",
  project_profitability: "/reports/projects/profitability",
};

/** Report params the app pages share with the API (they use the API's names). */
const CARRIED_PARAMS = ["from_date", "to_date", "as_of_date", "account"];

/**
 * In-app link for a cited source, or null when there is nothing to open.
 *
 * `source.route` is a Django API path and is never used as a link itself;
 * only its query string is read, and only known report parameters survive.
 */
export function sourceHref(source: AiSource): string | null {
  switch (source.type) {
    case "invoice":
      return source.id && UUID_PATTERN.test(source.id) ? `/sales/invoices/${source.id}` : null;
    case "bill":
      return source.id && UUID_PATTERN.test(source.id) ? `/purchases/bills/${source.id}` : null;
    case "document": {
      const id = source.document_id ?? source.id;
      return id && UUID_PATTERN.test(id) ? `/documents/${id}` : null;
    }
    case "report": {
      const base = source.id ? REPORT_ROUTES[source.id] : undefined;
      if (!base) return "/reports";
      const query = source.route?.includes("?") ? source.route.slice(source.route.indexOf("?") + 1) : "";
      const incoming = new URLSearchParams(query);
      const carried = new URLSearchParams();
      for (const key of CARRIED_PARAMS) {
        const value = incoming.get(key);
        if (value) carried.set(key, value);
      }
      const search = carried.toString();
      return search ? `${base}?${search}` : base;
    }
    default:
      return null;
  }
}

/** "page 3 · Terms" for a document citation. */
export function sourceLocation(source: AiSource): string | null {
  const parts = [source.page !== null ? `page ${source.page}` : null, source.section].filter(Boolean);
  return parts.length > 0 ? parts.join(" · ") : null;
}

const SOURCE_TYPE_LABELS: Record<string, string> = {
  report: "Report",
  invoice: "Invoice",
  bill: "Bill",
  document: "Document",
};

export function sourceTypeLabel(type: string): string {
  return SOURCE_TYPE_LABELS[type] ?? type;
}

/** ai/orchestration/service.py :: _finalize warning codes, in plain words. */
const WARNING_TEXT: Record<string, string> = {
  unverified_citations_removed: "Citations the assistant could not verify were removed.",
  citations_attached_from_context: "The assistant cited nothing itself; the sources it was given are listed instead.",
  ungrounded_figures_replaced:
    "The assistant stated figures that were not in the data, so its answer was replaced with the report figures.",
  prompt_disclosure_blocked: "The answer was withheld because it repeated internal instructions.",
};

export function warningText(code: string): string {
  return WARNING_TEXT[code] ?? code;
}

/** Status line for an answer. `answered` needs none. */
export function statusNote(status: AskStatus | "" | string): string | null {
  switch (status) {
    case "partial":
      return "Partial answer";
    case "no_data":
      return "No data found";
    case "refused":
      return "Declined";
    default:
      return null;
  }
}

/** "get_profit_and_loss" → "Profit and loss". */
export function toolLabel(tool: string): string {
  const words = tool.replace(/^get_/, "").replace(/_/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : tool;
}

/** "gross_profit" → "Gross profit". */
export function fieldLabel(key: string): string {
  const words = key.replace(/_/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : key;
}
