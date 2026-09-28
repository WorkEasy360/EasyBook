import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Money } from "@/components/ui/money";
import { PrintButton } from "@/components/ui/print-button";
import { StatCard, StatGrid } from "@/components/ui/stat-card";
import { INVOICE_STATUS, PAYMENT_METHOD_LABELS, StatusBadge } from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { PartyCard } from "@/features/documents/document-view";
import { serverApi, tryServer } from "@/lib/api/server";
import { accountLabel, recordsById } from "@/lib/api/lookups";
import { wholeList } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate, formatDateTime } from "@/lib/datetime";
import type { Account } from "@/types/api/accounting";
import type { Customer, CustomerPayment, Invoice, PaymentAllocation } from "@/types/api/sales";

export const metadata: Metadata = { title: "Payment" };

/**
 * Read-only: CustomerPaymentDetailView is a RetrieveAPIView and the model
 * refuses any update after insert (sales/models/payment.py), so there is no
 * edit, and no reversal endpoint either.
 */
export default async function PaymentDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_PAYMENTS)) {
    return (
      <>
        <PageHeader title="Payment" />
        <ForbiddenState resource="payments" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<CustomerPayment>(`sales/payments/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Payment" />
        <ErrorState title="Could not load the payment" message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const payment = result.data;
  const canViewAccounting = roleHasPermission(role, PERMISSIONS.VIEW_ACCOUNTING);
  const applied = payment.allocations.filter((allocation) => allocation.invoice !== null);
  const unapplied = payment.allocations.find((allocation) => allocation.invoice === null);

  const [customer, invoices, accounts] = await Promise.all([
    roleHasPermission(role, PERMISSIONS.VIEW_CUSTOMERS)
      ? tryServer(() => serverApi.get<Customer>(`sales/customers/${payment.customer}`))
      : Promise.resolve(null),
    roleHasPermission(role, PERMISSIONS.VIEW_INVOICES)
      ? recordsById<Invoice>("sales/invoices", applied.map((allocation) => allocation.invoice))
      : Promise.resolve(new Map<string, Invoice>()),
    canViewAccounting
      ? recordsById<Account>("accounting/accounts", [payment.destination_account])
      : Promise.resolve(new Map<string, Account>()),
  ]);

  const customerName = customer?.ok ? customer.data.display_name : null;

  const columns: Column<PaymentAllocation>[] = [
    {
      key: "invoice",
      header: "Invoice",
      cell: (allocation) => {
        const invoice = allocation.invoice ? invoices.get(allocation.invoice) : undefined;
        return <span className="tabular">{invoice?.invoice_number || "Invoice"}</span>;
      },
    },
    {
      key: "date",
      header: "Invoice date",
      hideBelow: "sm",
      cell: (allocation) => {
        const invoice = allocation.invoice ? invoices.get(allocation.invoice) : undefined;
        return invoice ? formatDate(invoice.invoice_date) : <span className="text-ink-400">—</span>;
      },
    },
    {
      key: "status",
      header: "Invoice status now",
      hideBelow: "md",
      cell: (allocation) => {
        const invoice = allocation.invoice ? invoices.get(allocation.invoice) : undefined;
        return invoice ? <StatusBadge status={invoice.status} map={INVOICE_STATUS} size="sm" /> : <span className="text-ink-400">—</span>;
      },
    },
    {
      key: "due",
      header: "Balance due now",
      numeric: true,
      hideBelow: "md",
      cell: (allocation) => {
        const invoice = allocation.invoice ? invoices.get(allocation.invoice) : undefined;
        return invoice ? <Money value={invoice.amount_due} currency={invoice.currency} /> : <span className="text-ink-400">—</span>;
      },
    },
    {
      key: "amount",
      header: "Applied",
      numeric: true,
      cell: (allocation) => <Money value={allocation.amount} currency={payment.currency} strong />,
    },
  ];

  return (
    <>
      <PageHeader
        title={payment.payment_number}
        breadcrumbs={[{ label: "Payments received", href: "/sales/payments" }, { label: payment.payment_number }]}
        description={customerName ? `Received from ${customerName}` : undefined}
        actions={<PrintButton />}
      />

      <PageBody className="print-document">
        <StatGrid columns={2}>
          <StatCard label="Amount received" value={<Money value={payment.amount} currency={payment.currency} />} />
          <StatCard
            label="Unapplied credit"
            value={
              unapplied ? <Money value={unapplied.amount} currency={payment.currency} /> : <Money value="0" currency={payment.currency} />
            }
            hint={unapplied ? "Held as customer credit, not applied to any invoice." : "Fully applied to invoices."}
          />
        </StatGrid>

        <div className="grid gap-4 lg:grid-cols-3">
          <PartyCard
            title="Customer"
            href={`/sales/customers/${payment.customer}`}
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
                  { label: "Payment date", value: formatDate(payment.payment_date) },
                  { label: "Method", value: PAYMENT_METHOD_LABELS[payment.payment_method] ?? payment.payment_method },
                  { label: "Reference", value: payment.reference || "—" },
                  { label: "Currency", value: payment.currency },
                  ...(canViewAccounting
                    ? [{ label: "Received into", value: accountLabel(accounts, payment.destination_account) }]
                    : []),
                  { label: "Recorded", value: formatDateTime(payment.created_at, { timeZone: session.timeZone }) },
                  ...(payment.notes
                    ? [{ label: "Notes", value: <span className="whitespace-pre-line">{payment.notes}</span>, span: true }]
                    : []),
                ]}
              />
            </CardBody>
          </Card>
        </div>

        <Section
          title="Applied to invoices"
          description="Amounts applied when the payment was recorded. Invoice status and balance are as they stand today."
        >
          <DataTable
            caption={`Invoices ${payment.payment_number} was applied to`}
            columns={columns}
            data={wholeList(applied)}
            getRowId={(allocation) => allocation.id}
            getRowHref={(allocation) => `/sales/invoices/${allocation.invoice}`}
            emptyTitle="Not applied to any invoice"
            emptyDescription="The whole payment is held as unapplied customer credit."
            page={1}
            pageSize={Math.max(applied.length, 1)}
            buildPageHref={() => `/sales/payments/${payment.id}`}
          />
        </Section>

        <p className="text-xs text-ink-500">
          A recorded payment cannot be edited or deleted. See{" "}
          <Link href={`/sales/invoices?customer=${payment.customer}`} className="text-brand-700 hover:underline">
            this customer&apos;s invoices
          </Link>
          .
        </p>
      </PageBody>
    </>
  );
}
