import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import type { Column } from "@/components/ui/data-table";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { PrintButton } from "@/components/ui/print-button";
import { CREDIT_NOTE_REASON_LABELS, CREDIT_NOTE_STATUS, INVOICE_STATUS, StatusBadge } from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { DocumentTotalsCard, PartyCard, PricedLinesTable } from "@/features/documents/document-view";
import { serverApi, tryServer } from "@/lib/api/server";
import { accountLabel, recordsById } from "@/lib/api/lookups";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate, formatDateTime } from "@/lib/datetime";
import type { Account } from "@/types/api/accounting";
import type { Item } from "@/types/api/items";
import type { Warehouse } from "@/types/api/inventory";
import type { CreditNote, CreditNoteLine, Customer, Invoice } from "@/types/api/sales";

export const metadata: Metadata = { title: "Credit note" };

export default async function CreditNoteDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_CREDIT_NOTES)) {
    return (
      <>
        <PageHeader title="Credit note" />
        <ForbiddenState resource="credit notes" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<CreditNote>(`sales/credit-notes/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Credit note" />
        <ErrorState title="Could not load the credit note" message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const note = result.data;
  const canIssue = roleHasPermission(role, PERMISSIONS.ISSUE_CREDIT_NOTE);
  const canViewAccounting = roleHasPermission(role, PERMISSIONS.VIEW_ACCOUNTING);
  const isDraft = note.status === "draft";
  const restocked = note.lines.filter((line) => line.restock);

  const [customer, items, accounts, warehouse, invoice] = await Promise.all([
    roleHasPermission(role, PERMISSIONS.VIEW_CUSTOMERS)
      ? tryServer(() => serverApi.get<Customer>(`sales/customers/${note.customer}`))
      : Promise.resolve(null),
    recordsById<Item>("items", note.lines.map((line) => line.item)),
    canViewAccounting
      ? recordsById<Account>("accounting/accounts", [
          note.receivable_account,
          note.tax_payable_account,
          note.unapplied_credit_account,
        ])
      : Promise.resolve(new Map<string, Account>()),
    note.warehouse && roleHasPermission(role, PERMISSIONS.VIEW_INVENTORY)
      ? tryServer(() => serverApi.get<Warehouse>(`inventory/warehouses/${note.warehouse}`))
      : Promise.resolve(null),
    note.source_invoice && roleHasPermission(role, PERMISSIONS.VIEW_INVOICES)
      ? tryServer(() => serverApi.get<Invoice>(`sales/invoices/${note.source_invoice}`))
      : Promise.resolve(null),
  ]);

  const title = note.credit_note_number || "Draft credit note";
  const customerName = customer?.ok ? customer.data.display_name : null;
  const warehouseName = warehouse?.ok ? warehouse.data.name : "the warehouse";
  const invoiceNumber = invoice?.ok ? invoice.data.invoice_number || "the draft invoice" : "the source invoice";

  const restockColumn: Column<CreditNoteLine>[] = [
    {
      key: "restock",
      header: "Restock",
      hideBelow: "sm",
      cell: (line) =>
        line.restock ? (
          <span className="whitespace-nowrap">
            Yes
            {line.unit_cost ? (
              <span className="block text-2xs text-ink-500">
                at <Money value={line.unit_cost} currency={note.currency} /> each
              </span>
            ) : null}
          </span>
        ) : (
          <span className="text-ink-400">No</span>
        ),
    },
  ];

  return (
    <>
      <PageHeader
        title={title}
        breadcrumbs={[{ label: "Credit notes", href: "/sales/credit-notes" }, { label: title }]}
        meta={<StatusBadge status={note.status} map={CREDIT_NOTE_STATUS} />}
        description={customerName ? `Credited to ${customerName}` : undefined}
        actions={
          <>
            <PrintButton />
            {canIssue && isDraft ? <LinkButton href={`/sales/credit-notes/${note.id}/edit`}>Edit</LinkButton> : null}
            {canIssue && note.status === "issued" ? (
              <DocumentAction
                resource="sales/credit-notes"
                id={note.id}
                action="void"
                label="Void"
                variant="danger"
                confirmTitle={`Void ${title}?`}
                confirmMessage={
                  <>
                    <p>
                      Posts a reversing journal for this credit note
                      {restocked.length > 0 ? `, and takes the ${restocked.length} restocked ${restocked.length === 1 ? "line" : "lines"} back out of ${warehouseName}` : ""}
                      . The credit note stays on record, marked void. This cannot be undone.
                    </p>
                    {note.source_invoice ? (
                      <p className="mt-2">
                        The amount it applied to {invoiceNumber} no longer counts against that invoice, so its balance due
                        goes back up.
                      </p>
                    ) : null}
                  </>
                }
                successTitle="Credit note voided"
              />
            ) : null}
            {canIssue && isDraft ? (
              <DocumentAction
                resource="sales/credit-notes"
                id={note.id}
                action="issue"
                label="Issue credit note"
                variant="primary"
                confirmTitle="Issue this credit note?"
                confirmMessage={
                  <>
                    <p>
                      Issuing numbers the credit note and posts a journal for{" "}
                      <Money value={note.total} currency={note.currency} />: revenue and output tax are reversed,
                      {note.source_invoice
                        ? ` the balance due on ${invoiceNumber} is reduced first, and any excess is held as unapplied customer credit.`
                        : " and the whole amount is held as unapplied customer credit."}
                    </p>
                    {restocked.length > 0 ? (
                      <p className="mt-2">
                        {restocked.length} restocked {restocked.length === 1 ? "line returns" : "lines return"} to{" "}
                        {warehouseName} at the unit cost entered, moving that value from cost of goods sold back to
                        inventory.
                      </p>
                    ) : null}
                    <p className="mt-2">An issued credit note cannot be edited; void it to reverse.</p>
                  </>
                }
                successTitle="Credit note issued"
              />
            ) : null}
          </>
        }
      />

      <PageBody className="print-document">
        <div className="grid gap-4 lg:grid-cols-3">
          <PartyCard
            title="Customer"
            href={`/sales/customers/${note.customer}`}
            name={customerName}
            code={customer?.ok ? customer.data.customer_code : null}
            gstin={customer?.ok ? customer.data.gstin : null}
            address={customer?.ok ? customer.data.billing_address : null}
          />
          <Card className="lg:col-span-2">
            <CardHeader title="Details" />
            <CardBody>
              <DetailList
                items={[
                  { label: "Credit note date", value: formatDate(note.credit_note_date) },
                  { label: "Reason", value: CREDIT_NOTE_REASON_LABELS[note.reason] ?? note.reason },
                  { label: "Reference", value: note.reference || "—" },
                  {
                    label: "Source invoice",
                    value: note.source_invoice ? (
                      <span className="flex items-center gap-2">
                        <Link href={`/sales/invoices/${note.source_invoice}`} className="tabular text-brand-700 hover:underline">
                          {invoice?.ok ? invoice.data.invoice_number || "Draft invoice" : "View invoice"}
                        </Link>
                        {invoice?.ok ? <StatusBadge status={invoice.data.status} map={INVOICE_STATUS} size="sm" /> : null}
                      </span>
                    ) : (
                      "None"
                    ),
                  },
                  ...(canViewAccounting
                    ? [
                        {
                          label: "Receivable account",
                          value: accountLabel(accounts, note.receivable_account, note.source_invoice ? "Invoice's account" : "None"),
                        },
                        {
                          label: "Tax payable account",
                          value: accountLabel(accounts, note.tax_payable_account, note.source_invoice ? "Invoice's account" : "None"),
                        },
                        { label: "Unapplied credit account", value: accountLabel(accounts, note.unapplied_credit_account, "None") },
                      ]
                    : []),
                  { label: "Warehouse", value: warehouse?.ok ? warehouse.data.name : note.warehouse ? "Configured" : "None" },
                  ...(note.issued_at
                    ? [{ label: "Issued", value: formatDateTime(note.issued_at, { timeZone: session.timeZone }) }]
                    : []),
                  ...(note.voided_at
                    ? [{ label: "Voided", value: formatDateTime(note.voided_at, { timeZone: session.timeZone }) }]
                    : []),
                ]}
              />
            </CardBody>
          </Card>
        </div>

        <Section title="Lines">
          <PricedLinesTable
            lines={note.lines}
            items={items}
            currency={note.currency}
            caption={`Lines of ${title}`}
            selfHref={`/sales/credit-notes/${note.id}`}
            extraColumns={restockColumn}
          />
        </Section>

        <div className="grid gap-4 lg:grid-cols-5">
          <div className="flex flex-col gap-4 lg:col-span-3">
            {note.notes ? (
              <Card>
                <CardHeader title="Notes" />
                <CardBody>
                  <p className="text-sm whitespace-pre-line text-ink-800">{note.notes}</p>
                </CardBody>
              </Card>
            ) : null}
            {invoice?.ok && !isDraft ? (
              <Card>
                <CardHeader title="Source invoice now" />
                <CardBody>
                  <DetailList
                    items={[
                      { label: "Invoice total", value: <Money value={invoice.data.total} currency={invoice.data.currency} /> },
                      { label: "Balance due", value: <Money value={invoice.data.amount_due} currency={invoice.data.currency} strong /> },
                    ]}
                  />
                </CardBody>
              </Card>
            ) : null}
          </div>
          <div className="lg:col-span-2">
            <DocumentTotalsCard
              totals={note}
              currency={note.currency}
              {...(isDraft ? { footnote: "Calculated by the accounting engine when the draft was saved." } : {})}
            />
          </div>
        </div>
      </PageBody>
    </>
  );
}
