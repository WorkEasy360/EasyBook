import type { Metadata } from "next";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Section } from "@/components/ui/detail";
import { Money } from "@/components/ui/money";
import { serverApi, tryServer } from "@/lib/api/server";
import { csvExportHref } from "@/lib/api/capabilities";
import type { ApiError } from "@/lib/api/errors";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDate, todayInZone } from "@/lib/datetime";
import { wholeList, type RawSearchParams } from "@/lib/list-query";
import { reportEntry } from "@/features/reports/catalogue";
import { hrefWith, isReversedRange, monthPresets, reportTimestamp, resolveMonthRange } from "@/features/reports/period";
import {
  PeriodCaption,
  RangeControls,
  ReportForbidden,
  ReportShell,
  ReversedRangeNotice,
} from "@/features/reports/report-shell";
import { humanizeGstKey } from "@/features/tax/gst";
import { TaxReportLinks, TaxScopeNotice } from "@/features/tax/gst-tables";
import type { InputTaxRegisterRow, OutputTaxRegisterRow, TaxRegister } from "@/types/api/reports";

const PATH = "/tax/registers";
const ENTRY = reportEntry(PATH);

export const metadata: Metadata = { title: ENTRY.title };

/** Where a register row's document lives. Unknown document types are not linked. */
const DOCUMENT_ROUTES: Record<string, { label: string; href: (id: string) => string }> = {
  invoice: { label: "Invoice", href: (id) => `/sales/invoices/${id}` },
  credit_note: { label: "Credit note", href: (id) => `/sales/credit-notes/${id}` },
  bill: { label: "Bill", href: (id) => `/purchases/bills/${id}` },
};

function registerColumns<Row extends OutputTaxRegisterRow>(extra: Column<Row>[] = []): Column<Row>[] {
  return [
    {
      key: "document_number",
      header: "Document",
      cell: (row) => <span className="tabular">{row.document_number || "Document"}</span>,
    },
    {
      key: "document_type",
      header: "Type",
      hideBelow: "sm",
      cell: (row) => DOCUMENT_ROUTES[row.document_type]?.label ?? humanizeGstKey(row.document_type),
    },
    ...extra,
    {
      key: "document_date",
      header: "Date",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDate(row.document_date)}</span>,
    },
    { key: "party", header: "Party", cell: (row) => row.party },
    {
      key: "gstin",
      header: "GSTIN",
      hideBelow: "md",
      cell: (row) => (row.gstin ? <span className="tabular">{row.gstin}</span> : <span className="text-ink-400">—</span>),
    },
    {
      key: "place_of_supply",
      header: "Place of supply",
      hideBelow: "lg",
      cell: (row) => row.place_of_supply ?? <span className="text-ink-400">—</span>,
    },
    {
      key: "supply_nature",
      header: "Supply nature",
      hideBelow: "lg",
      cell: (row) => <code className="text-xs text-ink-600">{row.supply_nature}</code>,
    },
    {
      key: "is_reverse_charge",
      header: "Reverse charge",
      hideBelow: "lg",
      cell: (row) => (row.is_reverse_charge ? "Yes" : "No"),
    },
    {
      key: "taxable_value",
      header: "Taxable value",
      numeric: true,
      cell: (row) => <Money value={row.taxable_value} />,
    },
    { key: "cgst", header: "CGST", numeric: true, hideBelow: "md", cell: (row) => <Money value={row.cgst} /> },
    { key: "sgst", header: "SGST", numeric: true, hideBelow: "md", cell: (row) => <Money value={row.sgst} /> },
    { key: "igst", header: "IGST", numeric: true, hideBelow: "md", cell: (row) => <Money value={row.igst} /> },
    { key: "cess", header: "Cess", numeric: true, hideBelow: "lg", cell: (row) => <Money value={row.cess} /> },
    { key: "total", header: "Total", numeric: true, cell: (row) => <Money value={row.total} strong /> },
  ];
}

/**
 * The output and input tax registers for a period, document by document.
 * Credit notes appear in the output register with NEGATIVE amounts (so the
 * register nets to the period's liability); the input register lists bills
 * only. The API returns no register totals, so none are shown.
 */
export default async function TaxRegistersPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_RETURNS)) {
    return <ReportForbidden title={ENTRY.title} resource="GST returns" />;
  }

  const range = resolveMonthRange(params, session.timeZone);
  const [output, input] = await Promise.all([
    tryServer(() =>
      serverApi.get<TaxRegister<OutputTaxRegisterRow>>("reports/tax/output-register", { query: range }),
    ),
    tryServer(() => serverApi.get<TaxRegister<InputTaxRegisterRow>>("reports/tax/input-register", { query: range })),
  ]);
  const outputRows = output.ok ? output.data.rows : [];
  const inputRows = input.ok ? input.data.rows : [];

  const rowHref = (row: OutputTaxRegisterRow) => {
    const route = DOCUMENT_ROUTES[row.document_type];
    return route ? route.href(row.document_id) : PATH;
  };

  return (
    <ReportShell
      title={ENTRY.title}
      description={ENTRY.description}
      // Neither register is wired for CSV on the backend — null here.
      csvHref={csvExportHref("tax/output-register", range)}
      generatedAt={output.ok || input.ok ? reportTimestamp() : null}
      timeZone={session.timeZone}
    >
      <TaxReportLinks current={PATH} query={hrefWith("", range)} />
      <TaxScopeNotice />
      <RangeControls path={PATH} range={range} presets={monthPresets(todayInZone(session.timeZone))} />
      <PeriodCaption from={range.from_date} to={range.to_date} />
      {isReversedRange(range) ? <ReversedRangeNotice /> : null}

      <Section
        title="Output tax register"
        description="Invoices and credit notes dated in the period. Credit notes carry negative amounts."
      >
        <DataTable
          caption="Output tax register"
          columns={registerColumns<OutputTaxRegisterRow>()}
          data={output.ok ? wholeList(outputRows) : undefined}
          error={output.ok ? null : (output.error as ApiError)}
          getRowId={(row) => `${row.document_type}:${row.document_id}`}
          getRowHref={rowHref}
          emptyTitle="No outward supplies in this period"
          emptyDescription="Posted invoices and credit notes dated in the period appear here."
          page={1}
          pageSize={Math.max(outputRows.length, 1)}
          buildPageHref={() => PATH}
          note="Sorted by document date, then number."
        />
      </Section>

      <Section title="Input tax register" description="Bills dated in the period. Expenses are not part of this register.">
        <DataTable
          caption="Input tax register"
          columns={registerColumns<InputTaxRegisterRow>([
            {
              key: "vendor_bill_number",
              header: "Vendor bill no.",
              hideBelow: "md",
              cell: (row) => row.vendor_bill_number || <span className="text-ink-400">—</span>,
            },
          ])}
          data={input.ok ? wholeList(inputRows) : undefined}
          error={input.ok ? null : (input.error as ApiError)}
          getRowId={(row) => `${row.document_type}:${row.document_id}`}
          getRowHref={rowHref}
          emptyTitle="No inward supplies in this period"
          emptyDescription="Posted bills dated in the period appear here."
          page={1}
          pageSize={Math.max(inputRows.length, 1)}
          buildPageHref={() => PATH}
          note="Sorted by document date, then number."
        />
      </Section>
    </ReportShell>
  );
}
