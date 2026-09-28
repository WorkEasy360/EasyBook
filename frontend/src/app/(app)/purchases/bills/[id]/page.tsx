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
  BILL_STATUS,
  PAYMENT_METHOD_LABELS,
  StatusBadge,
  VENDOR_CREDIT_STATUS,
} from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { DocumentTotalsCard, PartyCard, PricedLinesTable } from "@/features/documents/document-view";
import { OverdueHint, isPastDue } from "@/features/documents/overdue-hint";
import { BillMatchPanel, MatchError, MatchToleranceForm, toleranceQuery } from "@/features/purchases/three-way-match";
import { serverApi, tryServer } from "@/lib/api/server";
import { accountLabel, recordsById } from "@/lib/api/lookups";
import { wholeList, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate, formatDateTime, todayInZone } from "@/lib/datetime";
import type { Account } from "@/types/api/accounting";
import type { Item } from "@/types/api/items";
import type { Warehouse } from "@/types/api/inventory";
import type {
  Bill,
  BillLine,
  BillMatch,
  PurchaseOrder,
  Vendor,
  VendorCredit,
  VendorPayment,
  VendorPaymentAllocation,
} from "@/types/api/purchases";

export const metadata: Metadata = { title: "Bill" };

export default async function BillDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<RawSearchParams>;
}) {
  const session = await requireSession();
  const { id } = await params;
  const search = await searchParams;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_BILLS)) {
    return (
      <>
        <PageHeader title="Bill" />
        <ForbiddenState resource="bills" />
      </>
    );
  }

  const tolerances = toleranceQuery(search);
  const [result, matchResult] = await Promise.all([
    tryServer(() => serverApi.get<Bill>(`purchases/bills/${id}`)),
    tryServer(() => serverApi.get<BillMatch>(`purchases/bills/${id}/match`, { query: { ...tolerances } })),
  ]);

  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Bill" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const bill = result.data;
  const selfHref = `/purchases/bills/${bill.id}`;
  const isDraft = bill.status === "draft";
  const isVoid = bill.status === "void";
  // services/payments.py: only open and partially paid bills accept a payment
  // allocation (bill_not_payable); services/bills.py voids the same two.
  const isOpen = bill.status === "open" || bill.status === "partially_paid";
  const canViewAccounting = roleHasPermission(role, PERMISSIONS.VIEW_ACCOUNTING);

  const [vendor, items, accounts, warehouse, order, payments, credits] = await Promise.all([
    roleHasPermission(role, PERMISSIONS.VIEW_VENDORS)
      ? tryServer(() => serverApi.get<Vendor>(`purchases/vendors/${bill.vendor}`))
      : Promise.resolve(null),
    recordsById<Item>("items", bill.lines.map((line) => line.item)),
    canViewAccounting
      ? recordsById<Account>("accounting/accounts", [
          bill.payable_account,
          bill.tax_recoverable_account,
          bill.price_variance_account,
          ...bill.lines.map((line) => line.expense_account),
        ])
      : Promise.resolve(new Map<string, Account>()),
    bill.warehouse ? tryServer(() => serverApi.get<Warehouse>(`inventory/warehouses/${bill.warehouse}`)) : Promise.resolve(null),
    bill.source_purchase_order && roleHasPermission(role, PERMISSIONS.VIEW_PURCHASE_ORDERS)
      ? tryServer(() => serverApi.get<PurchaseOrder>(`purchases/orders/${bill.source_purchase_order}`))
      : Promise.resolve(null),
    // Payments and credits cannot be filtered by bill, only by vendor. One page
    // of the vendor's is scanned for this bill; the header amounts come from
    // the bill itself regardless.
    !isDraft && roleHasPermission(role, PERMISSIONS.VIEW_VENDOR_PAYMENTS)
      ? tryServer(() => serverApi.list<VendorPayment>("purchases/payments", { query: { vendor: bill.vendor, page_size: 200 } }))
      : Promise.resolve(null),
    !isDraft && roleHasPermission(role, PERMISSIONS.VIEW_VENDOR_CREDITS)
      ? tryServer(() => serverApi.list<VendorCredit>("purchases/vendor-credits", { query: { vendor: bill.vendor, page_size: 200 } }))
      : Promise.resolve(null),
  ]);

  const today = todayInZone(session.timeZone);
  const title = bill.bill_number || "Draft bill";
  const vendorName = vendor?.ok ? vendor.data.display_name : null;

  const appliedPayments = payments?.ok
    ? payments.data.results.flatMap((payment) =>
        payment.allocations
          .filter((allocation) => allocation.bill === bill.id)
          .map((allocation) => ({ payment, allocation })),
      )
    : [];
  const relatedCredits = credits?.ok ? credits.data.results.filter((credit) => credit.source_bill === bill.id) : [];

  const lineAccountColumn: Column<BillLine> = {
    key: "account",
    header: "Charged to",
    hideBelow: "lg",
    cell: (line) => {
      const item = items.get(line.item);
      if (item && item.item_type === "product" && item.track_inventory) {
        return (
          <span className="text-xs text-ink-600">
            Inventory
            {line.source_goods_receipt_line ? <span className="block text-ink-500">From goods receipt</span> : null}
          </span>
        );
      }
      if (!canViewAccounting) return <span className="text-xs text-ink-600">Expense</span>;
      return (
        <span className="text-xs text-ink-600">
          {line.expense_account ? accountLabel(accounts, line.expense_account) : "Item's purchase account"}
        </span>
      );
    },
  };

  const paymentColumns: Column<{ payment: VendorPayment; allocation: VendorPaymentAllocation }>[] = [
    { key: "number", header: "Payment", cell: ({ payment }) => <span className="tabular">{payment.payment_number}</span> },
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

  const creditColumns: Column<VendorCredit>[] = [
    {
      key: "number",
      header: "Vendor credit",
      cell: (credit) => (credit.credit_number ? <span className="tabular">{credit.credit_number}</span> : <span className="italic">Draft</span>),
    },
    { key: "date", header: "Date", cell: (credit) => formatDate(credit.credit_date) },
    { key: "status", header: "Status", cell: (credit) => <StatusBadge status={credit.status} map={VENDOR_CREDIT_STATUS} size="sm" /> },
    { key: "total", header: "Total", numeric: true, hideBelow: "sm", cell: (credit) => <Money value={credit.total} currency={credit.currency} /> },
    {
      key: "applied",
      header: "Applied to bill",
      numeric: true,
      cell: (credit) =>
        credit.status === "issued" ? <Money value={credit.amount_applied_to_bill} currency={credit.currency} /> : <span className="text-ink-400">—</span>,
    },
  ];

  return (
    <>
      <PageHeader
        title={title}
        breadcrumbs={[{ label: "Bills", href: "/purchases/bills" }, { label: title }]}
        meta={
          <>
            <StatusBadge status={bill.status} map={BILL_STATUS} />
            <OverdueHint invoice={bill} today={today} />
          </>
        }
        description={vendorName ? `From ${vendorName}${bill.vendor_bill_number ? ` · their ref. ${bill.vendor_bill_number}` : ""}` : undefined}
        actions={
          <>
            <PrintButton />
            {isDraft && roleHasPermission(role, PERMISSIONS.CREATE_BILL) ? (
              <LinkButton href={`${selfHref}/edit`}>Edit</LinkButton>
            ) : null}
            {!isDraft && !isVoid && roleHasPermission(role, PERMISSIONS.ISSUE_VENDOR_CREDIT) ? (
              <LinkButton href={`/purchases/vendor-credits/new?bill=${bill.id}`}>Vendor credit</LinkButton>
            ) : null}
            {isOpen && roleHasPermission(role, PERMISSIONS.VOID_BILL) ? (
              <DocumentAction
                resource="purchases/bills"
                id={bill.id}
                action="void"
                label="Void"
                variant="danger"
                confirmTitle={`Void ${title}?`}
                confirmMessage={
                  <>
                    <p>
                      Voiding reverses this bill&apos;s journal and takes back out of stock anything the bill itself
                      received. Stock that arrived on a goods receipt is not touched. The bill stays on record, marked
                      void.
                    </p>
                    <p className="mt-2">
                      A bill with payments or vendor credits applied cannot be voided until those are reversed. This
                      cannot be undone.
                    </p>
                  </>
                }
                reason={{ label: "Reason", hint: "Stored with the void for the audit trail." }}
                successTitle="Bill voided"
              />
            ) : null}
            {isOpen && roleHasPermission(role, PERMISSIONS.RECORD_VENDOR_PAYMENT) ? (
              <LinkButton href={`/purchases/payments/new?vendor=${bill.vendor}&bill=${bill.id}`} variant="primary">
                Record payment
              </LinkButton>
            ) : null}
            {isDraft && roleHasPermission(role, PERMISSIONS.POST_BILL) ? (
              <DocumentAction
                resource="purchases/bills"
                id={bill.id}
                action="post"
                label="Post bill"
                variant="primary"
                confirmTitle="Post this bill?"
                confirmMessage={
                  <>
                    <p>
                      Posting records <Money value={bill.total} currency={bill.currency} /> owed to the vendor in the
                      ledger, charges each line to inventory or expense with its recoverable tax, and receives into
                      stock any stocked line not already received on a goods receipt.
                    </p>
                    <p className="mt-2">It assigns the bill number and locks the bill. To correct it afterwards, void it or raise a vendor credit.</p>
                  </>
                }
                successTitle="Bill posted"
              />
            ) : null}
          </>
        }
      />

      <PageBody className="print-document">
        {!isDraft ? (
          <StatGrid columns={3}>
            <StatCard label="Total" value={<Money value={bill.total} currency={bill.currency} />} />
            <StatCard label="Paid" value={<Money value={bill.amount_paid} currency={bill.currency} />} />
            <StatCard
              label="Balance due"
              // A void bill's journal is reversed; the serializer still derives
              // total − paid for it, which is not an amount owed, so none is shown.
              value={isVoid ? <span className="text-ink-400">—</span> : <Money value={bill.amount_due} currency={bill.currency} />}
              tone={isPastDue(bill, today) ? "negative" : "default"}
              hint={isOpen ? `Due ${formatDate(bill.due_date)}` : isVoid ? "Void: nothing is owed on this bill" : undefined}
            />
          </StatGrid>
        ) : null}

        <div className="grid gap-4 lg:grid-cols-3">
          <PartyCard
            title="Vendor"
            href={`/purchases/vendors/${bill.vendor}`}
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
                  { label: "Bill date", value: formatDate(bill.bill_date) },
                  { label: "Due date", value: formatDate(bill.due_date) },
                  { label: "Vendor bill number", value: bill.vendor_bill_number || "—" },
                  { label: "Reference", value: bill.reference || "—" },
                  { label: "Currency", value: bill.currency },
                  ...(canViewAccounting
                    ? [
                        { label: "Payable account", value: accountLabel(accounts, bill.payable_account) },
                        { label: "Tax recoverable account", value: accountLabel(accounts, bill.tax_recoverable_account, "None") },
                        { label: "Price variance account", value: accountLabel(accounts, bill.price_variance_account, "None") },
                      ]
                    : []),
                  { label: "Warehouse", value: warehouse?.ok ? warehouse.data.name : bill.warehouse ? "Configured" : "None" },
                  {
                    label: "Purchase order",
                    value: bill.source_purchase_order ? (
                      <Link href={`/purchases/orders/${bill.source_purchase_order}`} className="text-brand-700 hover:underline">
                        {order?.ok ? order.data.order_number : "View order"}
                      </Link>
                    ) : (
                      "None"
                    ),
                  },
                  {
                    label: "Journal",
                    value: bill.accounting_journal ? (
                      canViewAccounting ? (
                        <Link href={`/accounting/journals/${bill.accounting_journal}`} className="text-brand-700 hover:underline">
                          View journal
                        </Link>
                      ) : (
                        "Posted"
                      )
                    ) : (
                      "Not posted"
                    ),
                  },
                  { label: "Created", value: formatDateTime(bill.created_at, { timeZone: session.timeZone }) },
                ]}
              />
            </CardBody>
          </Card>
        </div>

        <Section title="Lines">
          <PricedLinesTable
            lines={bill.lines}
            items={items}
            currency={bill.currency}
            caption={`Lines of ${title}`}
            selfHref={selfHref}
            extraColumns={[lineAccountColumn]}
          />
        </Section>

        <div className="grid gap-4 lg:grid-cols-5">
          <div className="flex flex-col gap-4 lg:col-span-3">
            {bill.notes ? (
              <Card>
                <CardHeader title="Notes" />
                <CardBody>
                  <p className="text-sm whitespace-pre-line text-ink-800">{bill.notes}</p>
                </CardBody>
              </Card>
            ) : null}
          </div>
          <div className="lg:col-span-2">
            <DocumentTotalsCard
              totals={bill}
              currency={bill.currency}
              settlement={
                isDraft || isVoid
                  ? []
                  : [
                      { label: "Paid", value: bill.amount_paid },
                      { label: "Balance due", value: bill.amount_due, emphasis: true },
                    ]
              }
              {...(isDraft
                ? { footnote: "Calculated by the accounting engine when the draft was saved." }
                : relatedCredits.some((credit) => credit.status === "issued")
                  ? { footnote: "Balance due is net of payments and vendor credits applied." }
                  : {})}
            />
          </div>
        </div>

        <Section
          title="Three-way match"
          description="This bill against its purchase order and what has been received. Informational: it never blocks posting."
          {...(bill.source_purchase_order ? { actions: <MatchToleranceForm action={selfHref} query={tolerances} /> } : {})}
        >
          {matchResult.ok ? (
            <BillMatchPanel match={matchResult.data} currency={bill.currency} selfHref={selfHref} />
          ) : (
            <MatchError error={matchResult.error} />
          )}
        </Section>

        {payments ? (
          <Section title="Payments applied" description="Allocations from this vendor's recorded payments.">
            <DataTable
              caption={`Payments applied to ${title}`}
              columns={paymentColumns}
              data={payments.ok ? wholeList(appliedPayments) : undefined}
              error={payments.ok ? null : (payments.error as ApiError)}
              getRowId={({ allocation }) => allocation.id}
              getRowHref={({ payment }) => `/purchases/payments/${payment.id}`}
              emptyTitle="No payments applied yet"
              page={1}
              pageSize={Math.max(appliedPayments.length, 1)}
              buildPageHref={() => selfHref}
            />
          </Section>
        ) : null}

        {credits?.ok && relatedCredits.length > 0 ? (
          <Section title="Vendor credits" description="Credits raised against this bill. Only issued credits reduce its balance.">
            <DataTable
              caption={`Vendor credits against ${title}`}
              columns={creditColumns}
              data={wholeList(relatedCredits)}
              getRowId={(credit) => credit.id}
              getRowHref={(credit) => `/purchases/vendor-credits/${credit.id}`}
              page={1}
              pageSize={relatedCredits.length}
              buildPageHref={() => selfHref}
            />
          </Section>
        ) : null}
      </PageBody>
    </>
  );
}
