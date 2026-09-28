import type { MatchExceptionCode } from "@/types/api/purchases";

/**
 * Wording for backend codes specific to purchases. Transcribed from the
 * backend (services/three_way_match.py :: MatchException) — never invented;
 * an unknown code is shown raw by the callers so a backend change stays
 * visible.
 */
export const MATCH_EXCEPTION_LABELS: Record<MatchExceptionCode, { label: string; description: string }> = {
  quantity_over_received: {
    label: "Over-received",
    description: "More was received than was ordered.",
  },
  quantity_over_billed: {
    label: "Over-billed",
    description: "More was billed than was ordered.",
  },
  billed_not_received: {
    label: "Billed, not received",
    description: "The vendor has billed for more than has arrived.",
  },
  received_not_billed: {
    label: "Received, not billed",
    description: "Goods have arrived that no posted bill covers yet.",
  },
  price_mismatch: {
    label: "Price differs",
    description: "A billed unit price differs from the ordered price by more than the tolerance.",
  },
  not_on_order: {
    label: "Not on order",
    description: "A bill line points at no line of the purchase order.",
  },
};

export function matchExceptionLabel(code: string): string {
  return (MATCH_EXCEPTION_LABELS as Record<string, { label: string }>)[code]?.label ?? code;
}

/** "Net 30" / "Due on receipt" — purchases/models/vendor.py: 0 means due on receipt. */
export function paymentTermsLabel(days: number): string {
  return days === 0 ? "Due on receipt" : `Net ${days}`;
}
