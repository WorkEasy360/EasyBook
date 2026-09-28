import { isApiError } from "@/lib/api/errors";

/**
 * Per-line validation messages from a journal write.
 *
 * DRF reports nested line errors positionally —
 * `details: { lines: [{ debit: ["A valid number is required."] }, {}] }` —
 * which the shared `fieldErrorsOf` flattens into one list. The journal form
 * needs them by index and field to put each message under its own input.
 * (Captured live from POST /accounting/journals/.)
 *
 * Service-layer rules (`journal_line_both_sides`, `journal_too_few_lines`,
 * `account_inactive`…) arrive as a plain message with no details; those are
 * the form-level error, not a line error.
 */
export type LineFieldErrors = Array<Record<string, string>>;

export function lineErrorsOf(error: unknown): LineFieldErrors {
  if (!isApiError(error) || !error.isValidation) return [];
  const details = error.details;
  if (typeof details !== "object" || details === null || Array.isArray(details)) return [];
  const lines = (details as Record<string, unknown>)["lines"];
  if (!Array.isArray(lines)) return [];

  return lines.map((entry) => {
    const out: Record<string, string> = {};
    if (typeof entry !== "object" || entry === null || Array.isArray(entry)) return out;
    for (const [field, messages] of Object.entries(entry as Record<string, unknown>)) {
      const first = Array.isArray(messages) ? messages[0] : messages;
      if (typeof first === "string") out[field] = first;
    }
    return out;
  });
}
