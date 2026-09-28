import Link from "next/link";
import type { Column } from "@/components/ui/data-table";
import { Money } from "@/components/ui/money";
import { BILL_STATUS, INVOICE_STATUS, StatusBadge } from "@/components/ui/status-badge";
import { formatDate } from "@/lib/datetime";
import type { BillReportRow, InvoiceReportRow } from "@/types/api/reports";

/**
 * Columns for the invoice and bill rows the receivables/payables reports
 * return. The FIRST column is the document number because DataTable makes the
 * first cell the row's link (to the document); the party gets its own link.
 * `days_overdue` is shown only where the API supplies it (ageing rows) — the
 * UI does not count days itself.
 */

export function invoiceReportColumns<Row extends InvoiceReportRow>(
  options: { daysOverdue?: (row: Row) => number } = {},
): Column<Row>[] {
  const columns: Column<Row>[] = [
    {
      key: "invoice_number",
      header: "Invoice",
      cell: (row) => <span className="tabular">{row.invoice_number || "Invoice"}</span>,
    },
    {
      key: "customer_name",
      header: "Customer",
      cell: (row) => (
        <Link href={`/sales/customers/${row.customer_id}`} className="text-brand-700 hover:underline">
          {row.customer_name}
        </Link>
      ),
    },
    {
      key: "invoice_date",
      header: "Date",
      hideBelow: "md",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.invoice_date)}</span>,
    },
    {
      key: "due_date",
      header: "Due",
      hideBelow: "sm",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.due_date)}</span>,
    },
  ];
  if (options.daysOverdue) {
    const daysOverdue = options.daysOverdue;
    columns.push({
      key: "days_overdue",
      header: "Days overdue",
      numeric: true,
      cell: (row) => <span className="tabular">{daysOverdue(row)}</span>,
    });
  }
  columns.push(
    {
      key: "status",
      header: "Status",
      hideBelow: "lg",
      cell: (row) => <StatusBadge status={row.status} map={INVOICE_STATUS} size="sm" />,
    },
    {
      key: "total",
      header: "Total",
      numeric: true,
      hideBelow: "md",
      cell: (row) => <Money value={row.total} />,
    },
    {
      key: "amount_due",
      header: "Amount due",
      numeric: true,
      cell: (row) => <Money value={row.amount_due} strong />,
    },
  );
  return columns;
}

export function billReportColumns<Row extends BillReportRow>(
  options: { daysOverdue?: (row: Row) => number } = {},
): Column<Row>[] {
  const columns: Column<Row>[] = [
    {
      key: "bill_number",
      header: "Bill",
      cell: (row) => <span className="tabular">{row.bill_number || "Bill"}</span>,
    },
    {
      key: "vendor_name",
      header: "Vendor",
      cell: (row) => (
        <Link href={`/purchases/vendors/${row.vendor_id}`} className="text-brand-700 hover:underline">
          {row.vendor_name}
        </Link>
      ),
    },
    {
      key: "bill_date",
      header: "Date",
      hideBelow: "md",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.bill_date)}</span>,
    },
    {
      key: "due_date",
      header: "Due",
      hideBelow: "sm",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.due_date)}</span>,
    },
  ];
  if (options.daysOverdue) {
    const daysOverdue = options.daysOverdue;
    columns.push({
      key: "days_overdue",
      header: "Days overdue",
      numeric: true,
      cell: (row) => <span className="tabular">{daysOverdue(row)}</span>,
    });
  }
  columns.push(
    {
      key: "status",
      header: "Status",
      hideBelow: "lg",
      cell: (row) => <StatusBadge status={row.status} map={BILL_STATUS} size="sm" />,
    },
    {
      key: "total",
      header: "Total",
      numeric: true,
      hideBelow: "md",
      cell: (row) => <Money value={row.total} />,
    },
    {
      key: "amount_due",
      header: "Amount due",
      numeric: true,
      cell: (row) => <Money value={row.amount_due} strong />,
    },
  );
  return columns;
}
