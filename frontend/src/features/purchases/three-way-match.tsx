import { appHref } from "@/lib/routes";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Money, Quantity } from "@/components/ui/money";
import { ErrorState } from "@/components/ui/states";
import { wholeList } from "@/lib/list-query";
import { referenceOf } from "@/lib/api/errors";
import { MATCH_EXCEPTION_LABELS, matchExceptionLabel } from "./labels";
import type {
  BillMatch,
  BillMatchLine,
  PurchaseOrderMatch,
  PurchaseOrderMatchLine,
  ThreeWayMatchQuery,
  UnorderedBillLine,
} from "@/types/api/purchases";

/**
 * Three-way match (order vs receipt vs bill), rendered exactly as
 * purchases/services/three_way_match.py returns it.
 *
 * It is a READ-ONLY analysis on the backend: it never blocks posting and no
 * verdict is stored, so this panel only reports. Every quantity, price and
 * exception code shown is the server's; nothing is recomputed here. The
 * tolerances are per-request query params (ThreeWayMatchQuerySerializer,
 * default 0 = exact) — there is no organization-level setting to read.
 */

export { toleranceQuery } from "./match-query";

export function MatchToleranceForm({ action, query }: { action: string; query: ThreeWayMatchQuery }) {
  return (
    <form method="get" action={action} className="flex flex-wrap items-end gap-3" data-print="hide" aria-label="Match tolerances">
      <div className="flex flex-col gap-1">
        <label htmlFor="match-quantity-tolerance" className="text-2xs font-medium tracking-wide text-ink-500 uppercase">
          Quantity tolerance
        </label>
        <input
          id="match-quantity-tolerance"
          name="quantity_tolerance"
          inputMode="decimal"
          pattern="\d+(\.\d{1,4})?"
          placeholder="0"
          defaultValue={query.quantity_tolerance ?? ""}
          className="h-9 w-28 rounded-md border border-ink-300 bg-white px-2.5 text-right text-sm tabular text-ink-900 hover:border-ink-400 focus:border-brand-600"
        />
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor="match-price-tolerance" className="text-2xs font-medium tracking-wide text-ink-500 uppercase">
          Price tolerance
        </label>
        <input
          id="match-price-tolerance"
          name="price_tolerance"
          inputMode="decimal"
          pattern="\d+(\.\d{1,2})?"
          placeholder="0.00"
          defaultValue={query.price_tolerance ?? ""}
          className="h-9 w-28 rounded-md border border-ink-300 bg-white px-2.5 text-right text-sm tabular text-ink-900 hover:border-ink-400 focus:border-brand-600"
        />
      </div>
      <button
        type="submit"
        className="inline-flex h-9 items-center rounded-md border border-ink-300 bg-white px-3.5 text-sm font-medium text-ink-800 transition-colors hover:bg-ink-50"
      >
        Re-check
      </button>
      {query.quantity_tolerance || query.price_tolerance ? (
        <Link href={appHref(action)} className="pb-2 text-xs font-medium text-brand-700 hover:underline">
          Exact match
        </Link>
      ) : null}
    </form>
  );
}

function ExceptionBadges({ codes }: { codes: readonly string[] }) {
  if (codes.length === 0) {
    return (
      <Badge tone="success" size="sm" marker>
        Matched
      </Badge>
    );
  }
  return (
    <span className="flex flex-wrap justify-end gap-1">
      {codes.map((code) => (
        <Badge key={code} tone="warning" size="sm" marker>
          {matchExceptionLabel(code)}
        </Badge>
      ))}
    </span>
  );
}

/** Verdict line plus a legend for the exception codes present. */
function MatchSummary({ matched, exceptions }: { matched: boolean; exceptions: readonly string[] }) {
  return (
    <div className="flex flex-col gap-2 text-sm">
      <p className="flex flex-wrap items-center gap-2">
        {matched ? (
          <Badge tone="success" marker>
            Matched
          </Badge>
        ) : (
          <Badge tone="warning" marker>
            {exceptions.length === 1 ? "1 exception" : `${exceptions.length} exceptions`}
          </Badge>
        )}
        <span className="text-ink-600">
          {matched
            ? "Ordered, received and billed quantities and prices agree within the tolerances."
            : "Discrepancies are reported for review. They do not stop a bill from being posted."}
        </span>
      </p>
      {exceptions.length > 0 ? (
        <ul className="flex flex-col gap-0.5 text-xs text-ink-600">
          {exceptions.map((code) => (
            <li key={code}>
              <span className="font-medium text-ink-800">{matchExceptionLabel(code)}:</span>{" "}
              {(MATCH_EXCEPTION_LABELS as Record<string, { description: string }>)[code]?.description ?? "Reported by the matcher."}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function MatchError({ error }: { error: Error }) {
  return (
    <ErrorState
      compact
      title="Could not run the three-way match"
      message={error.message}
      reference={referenceOf(error)}
    />
  );
}

export function PurchaseOrderMatchPanel({
  match,
  currency,
  selfHref,
}: {
  match: PurchaseOrderMatch;
  currency: string;
  selfHref: string;
}) {
  const columns: Column<PurchaseOrderMatchLine>[] = [
    { key: "line_number", header: "#", width: "2.5rem", cell: (line) => line.line_number },
    { key: "description", header: "Item", cell: (line) => line.description || "Line" },
    { key: "ordered", header: "Ordered", numeric: true, cell: (line) => <Quantity value={line.ordered_quantity} /> },
    { key: "received", header: "Received", numeric: true, cell: (line) => <Quantity value={line.received_quantity} /> },
    { key: "billed", header: "Billed", numeric: true, cell: (line) => <Quantity value={line.billed_quantity} /> },
    {
      key: "ordered_price",
      header: "Ordered price",
      numeric: true,
      hideBelow: "md",
      cell: (line) => <Money value={line.ordered_unit_price} currency={currency} />,
    },
    {
      key: "billed_prices",
      header: "Billed at",
      numeric: true,
      hideBelow: "md",
      cell: (line) =>
        line.billed_unit_prices.length === 0 ? (
          <span className="text-ink-400">—</span>
        ) : (
          <span className="flex flex-col items-end">
            {line.billed_unit_prices.map((price) => (
              <Money key={String(price)} value={price} currency={currency} />
            ))}
          </span>
        ),
    },
    { key: "exceptions", header: "Result", numeric: true, cell: (line) => <ExceptionBadges codes={line.exceptions} /> },
  ];

  const unorderedColumns: Column<UnorderedBillLine>[] = [
    {
      key: "bill",
      header: "Bill",
      cell: (line) => (
        <Link href={`/purchases/bills/${line.bill_id}`} className="tabular text-brand-700 hover:underline">
          {line.bill_number || "Bill"}
        </Link>
      ),
    },
    { key: "line", header: "Line", numeric: true, cell: (line) => line.line_number },
    { key: "description", header: "Description", cell: (line) => line.description || "—" },
    { key: "quantity", header: "Qty", numeric: true, cell: (line) => <Quantity value={line.quantity} /> },
    { key: "total", header: "Amount", numeric: true, cell: (line) => <Money value={line.line_total} currency={currency} /> },
  ];

  return (
    <div className="flex flex-col gap-3">
      <MatchSummary matched={match.matched} exceptions={match.exceptions} />
      <DataTable
        caption={`Three-way match for ${match.order_number}`}
        columns={columns}
        data={wholeList(match.lines)}
        getRowId={(line) => line.line_id}
        emptyTitle="No lines to match"
        page={1}
        pageSize={Math.max(match.lines.length, 1)}
        buildPageHref={() => selfHref}
        note="Received counts goods receipts that have been received; billed counts posted bills (not drafts or voided bills)."
      />
      {match.unordered_bill_lines.length > 0 ? (
        <DataTable
          caption="Billed lines that are not on this order"
          columns={unorderedColumns}
          data={wholeList(match.unordered_bill_lines)}
          getRowId={(line) => `${line.bill_id}-${line.line_number}`}
          page={1}
          pageSize={match.unordered_bill_lines.length}
          buildPageHref={() => selfHref}
        />
      ) : null}
    </div>
  );
}

export function BillMatchPanel({ match, currency, selfHref }: { match: BillMatch; currency: string; selfHref: string }) {
  if (!match.purchase_order_id) {
    return (
      <p className="text-sm text-ink-600">
        This bill was not raised against a purchase order, so there is nothing agreed in advance to match it to.
      </p>
    );
  }

  const columns: Column<BillMatchLine>[] = [
    { key: "line_number", header: "#", width: "2.5rem", cell: (line) => line.line_number },
    { key: "description", header: "Item", cell: (line) => line.description || "Line" },
    { key: "billed", header: "Billed", numeric: true, cell: (line) => <Quantity value={line.billed_quantity} /> },
    { key: "ordered", header: "Ordered", numeric: true, cell: (line) => <Quantity value={line.ordered_quantity} /> },
    { key: "received", header: "Received", numeric: true, cell: (line) => <Quantity value={line.received_quantity} /> },
    {
      key: "billed_price",
      header: "Billed price",
      numeric: true,
      hideBelow: "md",
      cell: (line) => <Money value={line.billed_unit_price} currency={currency} />,
    },
    {
      key: "ordered_price",
      header: "Ordered price",
      numeric: true,
      hideBelow: "md",
      cell: (line) => <Money value={line.ordered_unit_price} currency={currency} />,
    },
    {
      key: "variance",
      header: "Variance",
      numeric: true,
      hideBelow: "lg",
      cell: (line) => (line.ordered_unit_price === null ? <span className="text-ink-400">—</span> : <Money value={line.price_variance} currency={currency} />),
    },
    { key: "exceptions", header: "Result", numeric: true, cell: (line) => <ExceptionBadges codes={line.exceptions} /> },
  ];

  return (
    <div className="flex flex-col gap-3">
      <MatchSummary matched={match.matched} exceptions={match.exceptions} />
      <DataTable
        caption={`Three-way match for ${match.bill_number || "this draft bill"}`}
        columns={columns}
        data={wholeList(match.lines)}
        getRowId={(line) => line.line_id}
        emptyTitle="No lines to match"
        page={1}
        pageSize={Math.max(match.lines.length, 1)}
        buildPageHref={() => selfHref}
        note="Ordered and received quantities are for the purchase order line each bill line links to; received counts goods receipts that have been received."
      />
    </div>
  );
}
