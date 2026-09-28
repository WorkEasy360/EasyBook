import { appHref } from "@/lib/routes";
import type * as React from "react";
import Link from "next/link";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { TotalsPanel } from "@/components/ui/detail";
import { Money, Quantity } from "@/components/ui/money";
import { wholeList } from "@/lib/list-query";
import type { DecimalString } from "@/lib/api/types";
import type { Address, DocumentTotals, PricedLine } from "@/types/api/sales";

/**
 * Read-only building blocks for a posted or draft document's detail page.
 * Server Components: every value shown is exactly what the API returned.
 */

interface NamedItem {
  id: string;
  name: string;
  sku: string;
}

/** Lines of any priced document, with the backend's computed amounts. */
export function PricedLinesTable<TLine extends PricedLine>({
  lines,
  items,
  currency,
  caption,
  extraColumns = [],
  selfHref,
}: {
  lines: readonly TLine[];
  items: Map<string, NamedItem>;
  currency: string;
  caption: string;
  /** Inserted before the amount columns, e.g. a credit note's "Restock". */
  extraColumns?: Column<TLine>[];
  /** The page's own URL; lines are never paginated. */
  selfHref: string;
}) {
  const columns: Column<TLine>[] = [
    { key: "line_number", header: "#", width: "2.5rem", cell: (line) => line.line_number },
    {
      key: "item",
      header: "Item",
      cell: (line) => {
        const item = items.get(line.item);
        return (
          <div className="min-w-0">
            <Link href={`/items/${line.item}`} className="font-medium text-brand-700 hover:underline">
              {line.description || item?.name || "Item"}
            </Link>
            <p className="text-xs text-ink-500">
              {item ? <span className="tabular">{item.sku}</span> : null}
              {line.hsn_sac_snapshot ? <span className="tabular"> · HSN/SAC {line.hsn_sac_snapshot}</span> : null}
            </p>
          </div>
        );
      },
    },
    { key: "quantity", header: "Qty", numeric: true, cell: (line) => <Quantity value={line.quantity} /> },
    {
      key: "unit_price",
      header: "Rate",
      numeric: true,
      cell: (line) => <Money value={line.unit_price} currency={currency} />,
    },
    ...extraColumns,
    {
      key: "discount",
      header: "Discount",
      numeric: true,
      hideBelow: "md",
      cell: (line) =>
        isZeroString(line.discount_amount) ? (
          <span className="text-ink-400">—</span>
        ) : (
          <span>
            <Money value={line.discount_amount} currency={currency} />
            <span className="block text-2xs text-ink-500">{line.discount_percent}%</span>
          </span>
        ),
    },
    {
      key: "tax",
      header: "Tax",
      numeric: true,
      hideBelow: "sm",
      cell: (line) =>
        isZeroString(line.tax_amount) ? (
          <span className="text-ink-400">—</span>
        ) : (
          <span>
            <Money value={line.tax_amount} currency={currency} />
            <span className="block text-2xs text-ink-500">
              {line.tax_rate}%{line.tax_label ? ` ${line.tax_label}` : ""}
            </span>
          </span>
        ),
    },
    {
      key: "line_total",
      header: "Amount",
      numeric: true,
      cell: (line) => <Money value={line.line_total} currency={currency} strong />,
    },
  ];

  return (
    <DataTable
      caption={caption}
      columns={columns}
      data={wholeList(lines)}
      getRowId={(line) => line.id}
      emptyTitle="No lines"
      page={1}
      pageSize={Math.max(lines.length, 1)}
      buildPageHref={() => selfHref}
    />
  );
}

/** A decimal string that is numerically zero ("0", "0.00"). No float involved. */
function isZeroString(value: DecimalString | null | undefined): boolean {
  return !value || /^-?0*(\.0*)?$/.test(value);
}

/** Subtotal → total panel, plus optional settlement rows (paid, due). */
export function DocumentTotalsCard({
  totals,
  currency,
  settlement = [],
  footnote,
}: {
  totals: DocumentTotals;
  currency: string;
  settlement?: Array<{ label: string; value: DecimalString; emphasis?: boolean }>;
  footnote?: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader title="Totals" />
      <CardBody className="flex flex-col items-end gap-2">
        <TotalsPanel
          rows={[
            { label: "Subtotal", value: <Money value={totals.subtotal} currency={currency} /> },
            ...(isZeroString(totals.discount_total)
              ? []
              : [{ label: "Discount", value: <Money value={`-${totals.discount_total}`} currency={currency} colorNegative={false} />, muted: true }]),
            { label: "Tax", value: <Money value={totals.tax_total} currency={currency} /> },
            { label: "Total", value: <Money value={totals.total} currency={currency} strong />, emphasis: true },
            ...settlement.map((row) => ({
              label: row.label,
              value: <Money value={row.value} currency={currency} strong={row.emphasis ?? false} />,
              ...(row.emphasis ? { emphasis: true } : {}),
            })),
          ]}
        />
        {footnote ? <p className="text-xs text-ink-500">{footnote}</p> : null}
      </CardBody>
    </Card>
  );
}

/** Customer or vendor block for a document header. */
export function PartyCard({
  title,
  href,
  name,
  code,
  gstin,
  address,
}: {
  title: string;
  href: string;
  name: string | null;
  code?: string | null;
  gstin?: string | null;
  address?: Address | null;
}) {
  const lines = address
    ? [
        address.line1,
        address.line2,
        [address.city, address.state].filter(Boolean).join(", "),
        address.postal_code,
      ].filter((line): line is string => Boolean(line && line.trim()))
    : [];

  return (
    <Card>
      <CardHeader title={title} />
      <CardBody className="flex flex-col gap-1 text-sm">
        <Link href={appHref(href)} className="font-medium text-brand-700 hover:underline">
          {name ?? "View record"}
        </Link>
        {code ? <p className="tabular text-xs text-ink-500">{code}</p> : null}
        {gstin ? <p className="tabular text-xs text-ink-600">GSTIN {gstin}</p> : null}
        {lines.length > 0 ? (
          <address className="mt-1 text-xs not-italic text-ink-600">
            {lines.map((line) => (
              <span key={line} className="block">
                {line}
              </span>
            ))}
            {address?.state_code ? <span className="block">State code {address.state_code}</span> : null}
          </address>
        ) : null}
      </CardBody>
    </Card>
  );
}
