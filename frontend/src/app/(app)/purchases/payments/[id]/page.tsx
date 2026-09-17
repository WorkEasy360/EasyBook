import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Money } from "@/components/ui/money";
import { PrintButton } from "@/components/ui/print-button";
import { StatCard, StatGrid } from "@/components/ui/stat-card";
import { BILL_STATUS, PAYMENT_METHOD_LABELS, StatusBadge } from "@/components/ui/status-badge";
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
import type { Bill, Vendor, VendorPayment, VendorPaymentAllocation } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Vendor payment" };

/**
 * A recorded vendor payment. Read-only by design: VendorPayment is
 * append-only (models/payment.py) and the API exposes GET only. Every amount
 * shown is an allocation row the server created; nothing is summed here.
 */
export default async function VendorPaymentDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_VENDOR_PAYMENTS)) {
    return (
      <>
        <PageHeader title="Vendor payment" />
        <ForbiddenState resource="vendor payments" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<VendorPayment>(`purchases/payments/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Vendor payment" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const payment = result.data;
  const selfHref = `/purchases/payments/${payment.id}`;
  const canViewAccounting = roleHasPermission(role, PERMISSIONS.VIEW_ACCOUNTING);

  const [vendor, bills, accounts] = await Promise.all([
    roleHasPermission(role, PERMISSIONS.VIEW_VENDORS)
      ? tryServer(() => serverApi.get<Vendor>(`purchases/vendors/${payment.vendor}`))
      : Promise.resolve(null),
    roleHasPermission(role, PERMISSIONS.VIEW_BILLS)
      ? recordsById<Bill>("purchases/bills", payment.allocations.map((allocation) => allocation.bill))
      : Promise.resolve(new Map<string, Bill>()),
    canViewAccounting
      ? recordsById<Account>("accounting/accounts", [payment.source_account])
      : Promise.resolve(new Map<string, Account>()),
  ]);

  const vendorName = vendor?.ok ? vendor.data.display_name : null;

  const columns: Column<VendorPaymentAllocation>[] = [
    {
      key: "bill",
      header: "Applied to",
      cell: (allocation) => {
        if (!allocation.bill) {
          return (
            <span>
              Vendor advance
              <span className="block text-xs text-ink-500">Held for this vendor, not applied to a bill</span>
            </span>
          );
        }
        const bill = bills.get(allocation.bill);
        return (
          <Link href={`/purchases/bills/${allocation.bill}`} className="tabular text-brand-700 hover:underline">
            {bill?.bill_number || "Bill"}
          </Link>
        );
      },
    },
    {
      key: "bill_date",
      header: "Bill date",
      hideBelow: "sm",
      cell: (allocation) => {
        const bill = allocation.bill ? bills.get(allocation.bill) : undefined;
        return bill ? formatDate(bill.bill_date) : <span className="text-ink-400">—</span>;
      },
    },
    {
      key: "status",
      header: "Bill status now",
      hideBelow: "md",
      cell: (allocation) => {
        const bill = allocation.bill ? bills.get(allocation.bill) : undefined;
        return bill ? <StatusBadge status={bill.status} map={BILL_STATUS} size="sm" /> : <span className="text-ink-400">—</span>;
      },
    },
    {
      key: "amount",
      header: "Amount",
      numeric: true,
      cell: (allocation) => <Money value={allocation.amount} currency={payment.currency} strong />,
    },
  ];

  return (
    <>
      <PageHeader
        title={payment.payment_number}
        breadcrumbs={[{ label: "Payments made", href: "/purchases/payments" }, { label: payment.payment_number }]}
        meta={
          <Badge tone="success" marker>
            Posted
          </Badge>
        }
        description={vendorName ? `Paid to ${vendorName}` : undefined}
        actions={<PrintButton />}
      />

      <PageBody className="print-document">
        <StatGrid columns={2}>
          <StatCard label="Amount paid" value={<Money value={payment.amount} currency={payment.currency} />} />
          <StatCard label="Payment date" value={formatDate(payment.payment_date)} hint={PAYMENT_METHOD_LABELS[payment.payment_method] ?? payment.payment_method} />
        </StatGrid>

        <div className="grid gap-4 lg:grid-cols-3">
          <PartyCard
            title="Vendor"
            href={`/purchases/vendors/${payment.vendor}`}
            name={vendorName}
            code={vendor?.ok ? vendor.data.vendor_code : null}
            gstin={vendor?.ok ? vendor.data.gstin : null}
            address={vendor?.ok ? vendor.data.billing_address : null}
          />
          <Card className="lg:col-span-2">
            <CardHeader title="Details" />
            <CardBody>
              <DetailList
                items={[
                  { label: "Method", value: PAYMENT_METHOD_LABELS[payment.payment_method] ?? payment.payment_method },
                  { label: "Reference", value: payment.reference || "—" },
                  { label: "Currency", value: payment.currency },
                  ...(canViewAccounting ? [{ label: "Paid from", value: accountLabel(accounts, payment.source_account) }] : []),
                  {
                    label: "Journal",
                    value: payment.accounting_journal ? (
                      canViewAccounting ? (
                        <Link href={`/accounting/journals/${payment.accounting_journal}`} className="text-brand-700 hover:underline">
                          View journal
                        </Link>
                      ) : (
                        "Posted"
                      )
                    ) : (
                      "—"
                    ),
                  },
                  { label: "Recorded", value: formatDateTime(payment.created_at, { timeZone: session.timeZone }) },
                  ...(payment.notes ? [{ label: "Notes", value: <span className="whitespace-pre-line">{payment.notes}</span>, span: true }] : []),
                ]}
              />
            </CardBody>
          </Card>
        </div>

        <Section
          title="Allocations"
          description="How the server applied this payment. A payment cannot be edited; to undo one, reverse its journal in accounting."
        >
          <DataTable
            caption={`Allocations of ${payment.payment_number}`}
            columns={columns}
            data={wholeList(payment.allocations)}
            getRowId={(allocation) => allocation.id}
            emptyTitle="No allocations"
            page={1}
            pageSize={Math.max(payment.allocations.length, 1)}
            buildPageHref={() => selfHref}
          />
        </Section>
      </PageBody>
    </>
  );
}
