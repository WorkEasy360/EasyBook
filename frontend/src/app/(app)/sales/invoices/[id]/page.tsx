import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { PrintButton } from "@/components/ui/print-button";
import { StatCard, StatGrid } from "@/components/ui/stat-card";
import {
  CREDIT_NOTE_STATUS,
  INVOICE_STATUS,
  PAYMENT_METHOD_LABELS,
  StatusBadge,
} from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { PaymentReminderDraftButton } from "@/features/ai/payment-reminder-draft";
import { DocumentTotalsCard, PartyCard, PricedLinesTable } from "@/features/documents/document-view";
import { OverdueHint, isPastDue } from "@/features/documents/overdue-hint";
import { serverApi, tryServer } from "@/lib/api/server";
import { accountLabel, recordsById } from "@/lib/api/lookups";
import { wholeList } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate, formatDateTime, todayInZone } from "@/lib/datetime";
import type { Account } from "@/types/api/accounting";
import type { Item } from "@/types/api/items";
import type { Warehouse } from "@/types/api/inventory";
import type { CreditNote, Customer, CustomerPayment, Invoice } from "@/types/api/sales";

export const metadata: Metadata = { title: "Invoice" };

export default async function InvoiceDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_INVOICES)) {
    return (
      <>
        <PageHeader title="Invoice" />
        <ForbiddenState resource="invoices" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<Invoice>(`sales/invoices/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Invoice" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const invoice = result.data;
  const isDraft = invoice.status === "draft";
  const isVoid = invoice.status === "void";
  const isOpen = invoice.status === "sent" || invoice.status === "partially_paid";
  const canViewAccounting = roleHasPermission(role, PERMISSIONS.VIEW_ACCOUNTING);

  const [customer, items, accounts, warehouse, payments, creditNotes] = await Promise.all([
    roleHasPermission(role, PERMISSIONS.VIEW_CUSTOMERS)
      ? tryServer(() => serverApi.get<Customer>(`sales/customers/${invoice.customer}`))
      : Promise.resolve(null),
    recordsById<Item>("items", invoice.lines.map((line) => line.item)),
    canViewAccounting
      ? recordsById<Account>("accounting/accounts", [invoice.receivable_account, invoice.tax_payable_account])
      : Promise.resolve(new Map<string, Account>()),
    invoice.warehouse
      ? tryServer(() => serverApi.get<Warehouse>(`inventory/warehouses/${invoice.warehouse}`))
      : Promise.resolve(null),
    // Payments cannot be filtered by invoice, only by customer. One page of
    // the customer's payments is scanned for allocations to this invoice; the
    // amounts shown in the header come from the invoice itself regardless.
    !isDraft && roleHasPermission(role, PERMISSIONS.VIEW_PAYMENTS)
      ? tryServer(() =>
          serverApi.list<CustomerPayment>("sales/payments", { query: { customer: invoice.customer, page_size: 200 } }),
        )
      : Promise.resolve(null),
    !isDraft && roleHasPermission(role, PERMISSIONS.VIEW_CREDIT_NOTES)
      ? tryServer(() =>
          serverApi.list<CreditNote>("sales/credit-notes", { query: { customer: invoice.customer, page_size: 200 } }),
        )
      : Promise.resolve(null),
  ]);

  const today = todayInZone(session.timeZone);
  const title = invoice.invoice_number || "Draft invoice";
  const customerName = customer?.ok ? customer.data.display_name : null;

  const appliedPayments = payments?.ok
    ? payments.data.results.flatMap((payment) =>
        payment.allocations
          .filter((allocation) => allocation.invoice === invoice.id)
          .map((allocation) => ({ payment, allocation })),
      )
    : [];
  const relatedCredits = creditNotes?.ok
    ? creditNotes.data.results.filter((note) => note.source_invoice === invoice.id)
    : [];

  const paymentColumns: Column<(typeof appliedPayments)[number]>[] = [
    {
      key: "number",
      header: "Payment",
      cell: ({ payment }) => <span className="tabular">{payment.payment_number}</span>,
    },
    { key: "date", header: "Date", cell: ({ payment }) => formatDate(payment.payment_date) },
    {
      key: "method",
      header: "Method",
      hideBelow: "sm",
      cell: ({ payment }) => PAYMENT_METHOD_LABELS[payment.payment_method] ?? payment.payment_method,
    },
    {
      key: "amount",
      header: "Applied",
      numeric: true,
      cell: ({ allocation, payment }) => <Money value={allocation.amount} currency={payment.currency} />,
    },
  ];

  const creditColumns: Column<CreditNote>[] = [
    {
      key: "number",
      header: "Credit note",
      cell: (note) => (note.credit_note_number ? <span className="tabular">{note.credit_note_number}</span> : "Draft"),
    },
    { key: "date", header: "Date", cell: (note) => formatDate(note.credit_note_date) },
    { key: "status", header: "Status", cell: (note) => <StatusBadge status={note.status} map={CREDIT_NOTE_STATUS} size="sm" /> },
    { key: "total", header: "Total", numeric: true, cell: (note) => <Money value={note.total} currency={note.currency} /> },
  ];

  return (
    <>
      <PageHeader
        title={title}
        breadcrumbs={[{ label: "Invoices", href: "/sales/invoices" }, { label: title }]}
        meta={
          <>
            <StatusBadge status={invoice.status} map={INVOICE_STATUS} />
            <OverdueHint invoice={invoice} today={today} />
          </>
        }
        description={customerName ? `Billed to ${customerName}` : undefined}
        actions={
          <>
            <PrintButton />
            {/* Drafts text for the user to copy; nothing is sent (features/ai). */}
            {isOpen && roleHasPermission(role, PERMISSIONS.USE_AI_ASSISTANT) ? (
              <PaymentReminderDraftButton invoiceId={invoice.id} invoiceLabel={title} />
            ) : null}
            {isDraft && roleHasPermission(role, PERMISSIONS.CREATE_INVOICE) ? (
              <LinkButton href={`/sales/invoices/${invoice.id}/edit`}>Edit</LinkButton>
            ) : null}
            {isOpen && roleHasPermission(role, PERMISSIONS.ISSUE_CREDIT_NOTE) ? (
              <LinkButton href={`/sales/credit-notes/new?invoice=${invoice.id}`}>Credit note</LinkButton>
            ) : null}
            {!isDraft && !isVoid && roleHasPermission(role, PERMISSIONS.VOID_INVOICE) ? (
              <DocumentAction
                resource="sales/invoices"
                id={invoice.id}
                action="void"
                label="Void"
                variant="danger"
                confirmTitle={`Void ${title}?`}
                confirmMessage={
                  <p>
                    Voiding reverses this invoice&apos;s journal and any stock it issued. The invoice stays on record,
                    marked void. This cannot be undone.
                  </p>
                }
                reason={{ label: "Reason", hint: "Kept on the invoice for the audit trail." }}
                successTitle="Invoice voided"
              />
            ) : null}
            {isOpen && roleHasPermission(role, PERMISSIONS.RECORD_PAYMENT) ? (
              <LinkButton
                href={`/sales/payments/new?customer=${invoice.customer}&invoice=${invoice.id}`}
                variant="primary"
              >
                Record payment
              </LinkButton>
            ) : null}
            {isDraft && roleHasPermission(role, PERMISSIONS.POST_INVOICE) ? (
              <DocumentAction
                resource="sales/invoices"
                id={invoice.id}
                action="post"
                label="Post invoice"
                variant="primary"
                confirmTitle="Post this invoice?"
                confirmMessage={
                  <>
                    <p>
                      Posting records the invoice in the ledger for <Money value={invoice.total} currency={invoice.currency} />
                      , issues any tracked stock not already delivered, and locks it against editing.
                    </p>
                    <p className="mt-2">To correct a posted invoice, void it or raise a credit note.</p>
                  </>
                }
                successTitle="Invoice posted"
              />
            ) : null}
          </>
        }
      />

      <PageBody className="print-document">
        {!isDraft ? (
          <StatGrid columns={3}>
            <StatCard label="Total" value={<Money value={invoice.total} currency={invoice.currency} />} />
            <StatCard label="Paid" value={<Money value={invoice.amount_paid} currency={invoice.currency} />} />
            <StatCard
              label="Balance due"
              value={<Money value={invoice.amount_due} currency={invoice.currency} />}
              tone={isPastDue(invoice, today) ? "negative" : "default"}
              hint={isOpen ? `Due ${formatDate(invoice.due_date)}` : undefined}
            />
          </StatGrid>
        ) : null}

        <div className="grid gap-4 lg:grid-cols-3">
          <PartyCard
            title="Customer"
            href={`/sales/customers/${invoice.customer}`}
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
                  { label: "Invoice date", value: formatDate(invoice.invoice_date) },
                  { label: "Due date", value: formatDate(invoice.due_date) },
                  { label: "Reference", value: invoice.reference || "—" },
                  { label: "Currency", value: invoice.currency },
                  ...(canViewAccounting
                    ? [
                        { label: "Receivable account", value: accountLabel(accounts, invoice.receivable_account) },
                        { label: "Tax payable account", value: accountLabel(accounts, invoice.tax_payable_account, "None") },
                      ]
                    : []),
                  { label: "Warehouse", value: warehouse?.ok ? warehouse.data.name : invoice.warehouse ? "Configured" : "None" },
                  ...(invoice.source_quote
                    ? [
                        {
                          label: "From quote",
                          value: (
                            <Link href={`/sales/quotes/${invoice.source_quote}`} className="text-brand-700 hover:underline">
                              View quote
                            </Link>
                          ),
                        },
                      ]
                    : []),
                  ...(invoice.posted_at
                    ? [{ label: "Posted", value: formatDateTime(invoice.posted_at, { timeZone: session.timeZone }) }]
                    : []),
                  ...(invoice.voided_at
                    ? [
                        { label: "Voided", value: formatDateTime(invoice.voided_at, { timeZone: session.timeZone }) },
                        { label: "Void reason", value: invoice.void_reason || "—", span: true },
                      ]
                    : []),
                ]}
              />
            </CardBody>
          </Card>
        </div>

        <Section title="Lines">
          <PricedLinesTable
            lines={invoice.lines}
            items={items}
            currency={invoice.currency}
            caption={`Lines of ${title}`}
            selfHref={`/sales/invoices/${invoice.id}`}
          />
        </Section>

        <div className="grid gap-4 lg:grid-cols-5">
          <div className="flex flex-col gap-4 lg:col-span-3">
            {invoice.notes || invoice.terms ? (
              <Card>
                <CardHeader title="Notes and terms" />
                <CardBody>
                  <DetailList
                    columns={1}
                    items={[
                      ...(invoice.notes ? [{ label: "Notes", value: <span className="whitespace-pre-line">{invoice.notes}</span> }] : []),
                      ...(invoice.terms ? [{ label: "Terms", value: <span className="whitespace-pre-line">{invoice.terms}</span> }] : []),
                    ]}
                  />
                </CardBody>
              </Card>
            ) : null}
          </div>
          <div className="lg:col-span-2">
            <DocumentTotalsCard
              totals={invoice}
              currency={invoice.currency}
              settlement={
                isDraft || isVoid
                  ? []
                  : [
                      { label: "Paid", value: invoice.amount_paid },
                      { label: "Balance due", value: invoice.amount_due, emphasis: true },
                    ]
              }
              {...(isDraft ? { footnote: "Calculated by the accounting engine when the draft was saved." } : {})}
            />
          </div>
        </div>

        {payments ? (
          <Section title="Payments applied" description="Allocations from this customer's recorded payments.">
            <DataTable
              caption={`Payments applied to ${title}`}
              columns={paymentColumns}
              data={payments.ok ? wholeList(appliedPayments) : undefined}
              error={payments.ok ? null : (payments.error as ApiError)}
              getRowId={({ allocation }) => allocation.id}
              getRowHref={({ payment }) => `/sales/payments/${payment.id}`}
              emptyTitle="No payments applied yet"
              page={1}
              pageSize={Math.max(appliedPayments.length, 1)}
              buildPageHref={() => `/sales/invoices/${invoice.id}`}
            />
          </Section>
        ) : null}

        {creditNotes?.ok && relatedCredits.length > 0 ? (
          <Section title="Credit notes">
            <DataTable
              caption={`Credit notes raised against ${title}`}
              columns={creditColumns}
              data={wholeList(relatedCredits)}
              getRowId={(note) => note.id}
              getRowHref={(note) => `/sales/credit-notes/${note.id}`}
              page={1}
              pageSize={relatedCredits.length}
              buildPageHref={() => `/sales/invoices/${invoice.id}`}
            />
          </Section>
        ) : null}
      </PageBody>
    </>
  );
}
