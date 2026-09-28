import type { FilterGroup } from "@/components/ui/filter-bar";
import type { ListQuery } from "@/lib/list-query";
import type { Customer } from "@/types/api/sales";

/**
 * The customer filter chip for a sales list.
 *
 * Every sales list endpoint accepts `?customer=<id>`, but there is no search
 * over customers to pick one from inside a filter bar. So the chip appears
 * only when a customer filter is already in the URL — arriving from a
 * customer page or a document's party link — where it shows who is filtered
 * and can be cleared. Same shape as the invoice list.
 */
export function customerFilterGroups(query: ListQuery, customers: Map<string, Customer>): FilterGroup[] {
  const customerId = query.filters["customer"];
  if (!customerId) return [];
  return [
    {
      key: "customer",
      label: "Customer",
      value: customerId,
      options: [{ value: customerId, label: customers.get(customerId)?.display_name ?? "Selected customer" }],
    },
  ];
}
