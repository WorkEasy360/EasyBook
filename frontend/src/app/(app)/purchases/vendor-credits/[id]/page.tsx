import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { type Column } from "@/components/ui/data-table";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { PrintButton } from "@/components/ui/print-button";
import { StatusBadge, VENDOR_CREDIT_REASON_LABELS, VENDOR_CREDIT_STATUS } from "@/components/ui/status-badge";
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
import type { Bill, Vendor, VendorCredit, VendorCreditLine } from "@/types/api/purchases";

export const metadata: Metadata = { title: "Vendor credit" };

export default async function VendorCreditDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_VENDOR_CREDITS)) {
    return (
      <>
        <PageHeader title="Vendor credit" />
        <ForbiddenState resource="vendor credits" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<VendorCredit>(`purchases/vendor-credits/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Vendor credit" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const credit = result.data;
  const selfHref = `/purchases/vendor-credits/${credit.id}`;
  const isDraft = credit.status === "draft";
  const isIssued = credit.status === "issued";
  const canIssue = roleHasPermission(role, PERMISSIONS.ISSUE_VENDOR_CREDIT);
  const canViewAccounting = roleHasPermission(role, PERMISSIONS.VIEW_ACCOUNTING);
  const returnLines = credit.lines.filter((line) => line.return_stock);

  const [vendor, items, accounts, warehouse, bill] = await Promise.all([
    roleHasPermission(role, PERMISSIONS.VIEW_VENDORS)
      ? tryServer(() => serverApi.get<Vendor>(`purchases/vendors/${credit.vendor}`))
      : Promise.resolve(null),
    recordsById<Item>("items", credit.lines.map((line) => line.item)),
    canViewAccounting
      ? recordsById<Account>("accounting/accounts", [
          credit.payable_account,
          credit.tax_recoverable_account,
          credit.unapplied_credit_account,
          ...credit.lines.map((line) => line.expense_account),
        ])
      : Promise.resolve(new Map<string, Account>()),
    credit.warehouse ? tryServer(() => serverApi.get<Warehouse>(`inventory/warehouses/${credit.warehouse}`)) : Promise.resolve(null),
    credit.source_bill && roleHasPermission(role, PERMISSIONS.VIEW_BILLS)
      ? tryServer(() => serverApi.get<Bill>(`purchases/bills/${credit.source_bill}`))
      : Promise.resolve(null),
  ]);

  const title = credit.credit_number || "Draft vendor credit";
  const vendorName = vendor?.ok ? vendor.data.display_name : null;
  const warehouseName = warehouse?.ok ? warehouse.data.name : "the credit's warehouse";
  const billLabel = bill?.ok ? bill.data.bill_number : "the bill";

  const extraColumns: Column<VendorCreditLine>[] = [
    {
      key: "return",
      header: "Return",
      hideBelow: "md",
      cell: (line) =>
        line.return_stock ? (
          <span className="flex flex-col items-start gap-0.5">
            <Badge tone="warning" size="sm" marker>
              Returned
            </Badge>
            {line.unit_cost ? (
              <span className="text-2xs text-ink-500">
                at <Money value={line.unit_cost} currency={credit.currency} />
              </span>
            ) : null}
          </span>
        ) : (
          <span className="text-ink-400">—</span>
        ),
    },
    ...(canViewAccounting
      ? [
          {
            key: "account",
            header: "Credits",
            hideBelow: "lg",
            cell: (line: VendorCreditLine) => {
              const item = items.get(line.item);
              if (item && item.item_type === "product" && item.track_inventory) return <span className="text-xs text-ink-600">Inventory</span>;
              return (
                <span className="text-xs text-ink-600">
                  {line.expense_account ? accountLabel(accounts, line.expense_account) : "Item's purchase account"}
                </span>
              );
            },
          } satisfies Column<VendorCreditLine>,
        ]
      : []),
  ];

  return (
    <>
      <PageHeader
        title={title}
        breadcrumbs={[{ label: "Vendor credits", href: "/purchases/vendor-credits" }, { label: title }]}
        meta={<StatusBadge status={credit.status} map={VENDOR_CREDIT_STATUS} />}
        description={vendorName ? `From ${vendorName}` : undefined}
        actions={
          <>
            <PrintButton />
            {isDraft && canIssue ? <LinkButton href={`${selfHref}/edit`}>Edit</LinkButton> : null}
            {isIssued && canIssue ? (
              <DocumentAction
                resource="purchases/vendor-credits"
                id={credit.id}
                action="void"
                label="Void"
                variant="danger"
                confirmTitle={`Void ${title}?`}
                confirmMessage={
                  <>
                    <p>
                      Voiding posts a reversal of this credit&apos;s journal, dated today
                      {credit.source_bill ? `, so ${billLabel}'s balance due goes back up by what was applied` : ""}.
                    </p>
                    {returnLines.length > 0 ? (
                      <p className="mt-2">
                        The {returnLines.length} returned {returnLines.length === 1 ? "line comes" : "lines come"} back into
                        stock in {warehouseName}.
                      </p>
                    ) : null}
                    <p className="mt-2">The credit stays on record, marked void. This cannot be undone.</p>
                  </>
                }
                successTitle="Vendor credit voided"
              />
            ) : null}
            {isDraft && canIssue ? (
              <DocumentAction
                resource="purchases/vendor-credits"
                id={credit.id}
                action="issue"
                label="Issue credit"
                variant="primary"
                confirmTitle="Issue this vendor credit?"
                confirmMessage={
                  <>
                    <p>
                      Issuing posts <Money value={credit.total} currency={credit.currency} /> to the ledger, dated{" "}
                      {formatDate(credit.credit_date)}.{" "}
                      {credit.source_bill
                        ? `It first reduces what is still owed on ${billLabel}; any excess is held in the unapplied credit account.`
                        : "With no bill to net against, the whole credit is held in the unapplied credit account."}{" "}
                      Each line reverses the inventory or expense account the purchase was charged to, with its tax.
                    </p>
                    {returnLines.length > 0 ? (
                      <p className="mt-2">
                        {returnLines.length} {returnLines.length === 1 ? "line is" : "lines are"} taken out of stock in{" "}
                        {warehouseName}.
                      </p>
                    ) : null}
                    <p className="mt-2">It assigns the credit number and locks it. To undo it, void it.</p>
                  </>
                }
                successTitle="Vendor credit issued"
              />
            ) : null}
          </>
        }
      />

      <PageBody className="print-document">
        <div className="grid gap-4 lg:grid-cols-3">
          <PartyCard
            title="Vendor"
            href={`/purchases/vendors/${credit.vendor}`}
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
                  { label: "Credit date", value: formatDate(credit.credit_date) },
                  { label: "Reason", value: VENDOR_CREDIT_REASON_LABELS[credit.reason] ?? credit.reason },
                  { label: "Vendor credit note", value: credit.vendor_credit_number || "—" },
                  { label: "Reference", value: credit.reference || "—" },
                  {
                    label: "Bill",
                    value: credit.source_bill ? (
                      <Link href={`/purchases/bills/${credit.source_bill}`} className="text-brand-700 hover:underline">
                        {bill?.ok ? bill.data.bill_number || "Draft bill" : "View bill"}
                      </Link>
                    ) : (
                      "None"
                    ),
                  },
                  { label: "Warehouse", value: warehouse?.ok ? warehouse.data.name : credit.warehouse ? "Configured" : "None" },
                  ...(canViewAccounting
                    ? [
                        {
                          label: "Payable account",
                          value: accountLabel(accounts, credit.payable_account, credit.source_bill ? "The bill's" : "None"),
                        },
                        {
                          label: "Tax recoverable account",
                          value: accountLabel(accounts, credit.tax_recoverable_account, credit.source_bill ? "The bill's" : "None"),
                        },
                        { label: "Unapplied credit account", value: accountLabel(accounts, credit.unapplied_credit_account, "None") },
                      ]
                    : []),
                  {
                    label: "Journal",
                    value: credit.accounting_journal ? (
                      canViewAccounting ? (
                        <Link href={`/accounting/journals/${credit.accounting_journal}`} className="text-brand-700 hover:underline">
                          View journal
                        </Link>
                      ) : (
                        "Posted"
                      )
                    ) : (
                      "Not issued"
                    ),
                  },
                  { label: "Created", value: formatDateTime(credit.created_at, { timeZone: session.timeZone }) },
                ]}
              />
            </CardBody>
          </Card>
        </div>

        <Section title="Lines">
          <PricedLinesTable
            lines={credit.lines}
            items={items}
            currency={credit.currency}
            caption={`Lines of ${title}`}
            selfHref={selfHref}
            extraColumns={extraColumns}
          />
        </Section>

        <div className="grid gap-4 lg:grid-cols-5">
          <div className="flex flex-col gap-4 lg:col-span-3">
            {credit.notes ? (
              <Card>
                <CardHeader title="Notes" />
                <CardBody>
                  <p className="text-sm whitespace-pre-line text-ink-800">{credit.notes}</p>
                </CardBody>
              </Card>
            ) : null}
          </div>
          <div className="lg:col-span-2">
            <DocumentTotalsCard
              totals={credit}
              currency={credit.currency}
              settlement={credit.status === "draft" || !credit.source_bill ? [] : [{ label: "Applied to bill", value: credit.amount_applied_to_bill, emphasis: true }]}
              {...(isDraft
                ? { footnote: "Calculated by the accounting engine when the draft was saved. How much applies to the bill is decided on issue." }
                : {})}
            />
          </div>
        </div>
      </PageBody>
    </>
  );
}
