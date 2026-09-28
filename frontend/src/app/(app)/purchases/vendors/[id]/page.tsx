import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/ui/data-table";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { BILL_STATUS, StatusBadge } from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { OverdueHint } from "@/features/documents/overdue-hint";
import { AddressBlock } from "@/features/purchases/address-block";
import { paymentTermsLabel } from "@/features/purchases/labels";
import { serverApi, tryServer } from "@/lib/api/server";
import { accountLabel, recordsById } from "@/lib/api/lookups";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate, todayInZone } from "@/lib/datetime";
import type { Account } from "@/types/api/accounting";
import type { Bill, Vendor } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Vendor" };

const RECENT = 10;

export default async function VendorDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_VENDORS)) {
    return (
      <>
        <PageHeader title="Vendor" />
        <ForbiddenState resource="vendors" />
      </>
    );
  }

  const canViewBills = roleHasPermission(role, PERMISSIONS.VIEW_BILLS);

  // In parallel: the bill list filters by the id from the URL and does not
  // need the vendor record first.
  const [vendorResult, billsResult] = await Promise.all([
    tryServer(() => serverApi.get<Vendor>(`purchases/vendors/${id}`)),
    canViewBills
      ? tryServer(() => serverApi.list<Bill>("purchases/bills", { query: { vendor: id, page_size: RECENT } }))
      : Promise.resolve(null),
  ]);

  if (!vendorResult.ok) {
    if (vendorResult.error instanceof ApiError && vendorResult.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Vendor" />
        <ErrorState message={vendorResult.error.message} reference={referenceOf(vendorResult.error)} />
      </>
    );
  }

  const vendor = vendorResult.data;
  const canViewAccounting = roleHasPermission(role, PERMISSIONS.VIEW_ACCOUNTING);
  const accounts = canViewAccounting
    ? await recordsById<Account>("accounting/accounts", [vendor.default_payable_account])
    : new Map<string, Account>();
  const today = todayInZone(session.timeZone);

  const billColumns: Column<Bill>[] = [
    {
      key: "bill_number",
      header: "Bill",
      cell: (bill) => (bill.bill_number ? <span className="tabular">{bill.bill_number}</span> : <span className="italic">Draft</span>),
    },
    {
      key: "vendor_bill_number",
      header: "Vendor ref.",
      hideBelow: "md",
      cell: (bill) => bill.vendor_bill_number || <span className="text-ink-400">—</span>,
    },
    { key: "bill_date", header: "Date", cell: (bill) => <span className="tabular">{formatDate(bill.bill_date)}</span> },
    {
      key: "due_date",
      header: "Due",
      hideBelow: "sm",
      cell: (bill) => (
        <span className="tabular whitespace-nowrap">
          {formatDate(bill.due_date)}
          <OverdueHint invoice={bill} today={today} />
        </span>
      ),
    },
    { key: "status", header: "Status", cell: (bill) => <StatusBadge status={bill.status} map={BILL_STATUS} size="sm" /> },
    { key: "total", header: "Total", numeric: true, hideBelow: "sm", cell: (bill) => <Money value={bill.total} currency={bill.currency} /> },
    {
      key: "amount_due",
      header: "Balance due",
      numeric: true,
      cell: (bill) =>
        bill.status === "draft" || bill.status === "void" ? (
          <span className="text-ink-400">—</span>
        ) : (
          <Money value={bill.amount_due} currency={bill.currency} strong />
        ),
    },
  ];

  return (
    <>
      <PageHeader
        title={vendor.display_name}
        breadcrumbs={[{ label: "Vendors", href: "/purchases/vendors" }, { label: vendor.display_name }]}
        meta={
          <Badge tone={vendor.is_active ? "success" : "neutral"} marker={vendor.is_active}>
            {vendor.is_active ? "Active" : "Inactive"}
          </Badge>
        }
        description={
          <span className="tabular">
            {vendor.vendor_code}
            {vendor.gstin ? ` · GSTIN ${vendor.gstin}` : ""}
          </span>
        }
        actions={
          <>
            {roleHasPermission(role, PERMISSIONS.MANAGE_VENDORS) ? (
              <LinkButton href={`/purchases/vendors/${vendor.id}/edit`}>Edit</LinkButton>
            ) : null}
            {/* An inactive vendor is refused on new documents (vendor_inactive), so the shortcuts are hidden. */}
            {vendor.is_active && roleHasPermission(role, PERMISSIONS.MANAGE_PURCHASE_ORDERS) ? (
              <LinkButton href={`/purchases/orders/new?vendor=${vendor.id}`}>New purchase order</LinkButton>
            ) : null}
            {roleHasPermission(role, PERMISSIONS.RECORD_VENDOR_PAYMENT) ? (
              <LinkButton href={`/purchases/payments/new?vendor=${vendor.id}`}>Record payment</LinkButton>
            ) : null}
            {vendor.is_active && roleHasPermission(role, PERMISSIONS.CREATE_BILL) ? (
              <LinkButton href={`/purchases/bills/new?vendor=${vendor.id}`} variant="primary">
                New bill
              </LinkButton>
            ) : null}
          </>
        }
      />

      <PageBody>
        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader title="Details" />
            <CardBody>
              <DetailList
                items={[
                  { label: "Legal name", value: vendor.legal_name || "—" },
                  { label: "Currency", value: vendor.currency },
                  {
                    label: "Email",
                    value: vendor.email ? (
                      <a href={`mailto:${vendor.email}`} className="text-brand-700 hover:underline">
                        {vendor.email}
                      </a>
                    ) : (
                      "—"
                    ),
                  },
                  { label: "Phone", value: vendor.phone || "—" },
                  { label: "GSTIN", value: vendor.gstin || "—" },
                  { label: "PAN", value: vendor.pan || "—" },
                  { label: "Payment terms", value: paymentTermsLabel(vendor.payment_terms_days) },
                  ...(canViewAccounting
                    ? [{ label: "Default payable account", value: accountLabel(accounts, vendor.default_payable_account, "None") }]
                    : []),
                  ...(vendor.notes
                    ? [{ label: "Notes", value: <span className="whitespace-pre-line">{vendor.notes}</span>, span: true }]
                    : []),
                ]}
              />
            </CardBody>
          </Card>

          <Card>
            <CardHeader title="Addresses" />
            <CardBody className="flex flex-col gap-4">
              <AddressBlock label="Billing" address={vendor.billing_address} />
              <AddressBlock label="Shipping" address={vendor.shipping_address} />
            </CardBody>
          </Card>
        </div>

        {billsResult ? (
          <Section
            title="Recent bills"
            description="The latest bills for this vendor. Balances come from each bill; the payables aging report is the authoritative view of what is owed."
            actions={
              <Link href={`/purchases/bills?vendor=${vendor.id}`} className="text-xs font-medium text-brand-700 hover:underline">
                View all
              </Link>
            }
          >
            <DataTable
              caption={`Recent bills from ${vendor.display_name}`}
              columns={billColumns}
              data={billsResult.ok ? billsResult.data : undefined}
              error={billsResult.ok ? null : (billsResult.error as ApiError)}
              getRowId={(bill) => bill.id}
              getRowHref={(bill) => `/purchases/bills/${bill.id}`}
              emptyTitle="No bills yet"
              emptyDescription="Bills recorded for this vendor will appear here."
              page={1}
              pageSize={RECENT}
              buildPageHref={() => `/purchases/bills?vendor=${vendor.id}`}
            />
          </Section>
        ) : null}
      </PageBody>
    </>
  );
}
