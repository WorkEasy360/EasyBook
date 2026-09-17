import { Badge } from "@/components/ui/badge";
import type { DateString } from "@/lib/api/types";

/**
 * Marks a posted, unpaid invoice (or bill) whose due date has passed.
 *
 * The backend never stores an "overdue" status — nothing transitions a
 * document into it; lateness is derived from due_date by the overdue reports
 * (sales/selectors.py). This is the same derivation, for display only: it
 * changes no figure, and the overdue report remains the authoritative list.
 * ISO dates compare correctly as strings, and "today" is the organization's
 * today, not the browser's.
 */
export function isPastDue(
  document: { status: string; due_date: DateString; amount_due: string },
  today: DateString,
): boolean {
  const open = document.status === "sent" || document.status === "open" || document.status === "partially_paid";
  return open && document.due_date < today && !/^0*(\.0*)?$/.test(document.amount_due);
}

export function OverdueHint({
  invoice,
  today,
}: {
  invoice: { status: string; due_date: DateString; amount_due: string };
  today: DateString;
}) {
  if (!isPastDue(invoice, today)) return null;
  return (
    <Badge tone="danger" size="sm" marker className="ml-2 align-middle">
      Overdue
    </Badge>
  );
}
