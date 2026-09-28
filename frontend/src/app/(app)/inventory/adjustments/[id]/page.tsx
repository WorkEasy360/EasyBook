import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import { Badge } from "@/components/ui/badge";
import { LinkButton } from "@/components/ui/link-button";
import { Money, Quantity } from "@/components/ui/money";
import { StatusBadge, STOCK_ADJUSTMENT_STATUS } from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { serverApi, tryServer } from "@/lib/api/server";
import { wholeList } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate, formatDateTime } from "@/lib/datetime";
import type { Account } from "@/types/api/accounting";
import type { Item } from "@/types/api/items";
import {
  ADJUSTMENT_REASON_LABELS,
  type StockAdjustment,
  type StockAdjustmentLine,
  type Warehouse,
} from "@/types/api/inventory";

export const metadata: Metadata = { title: "Stock adjustment" };

export default async function AdjustmentDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_INVENTORY)) {
    return (
      <>
        <PageHeader title="Stock adjustment" />
        <ForbiddenState resource="stock adjustments" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<StockAdjustment>(`inventory/adjustments/${id}`));

  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Stock adjustment" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const adjustment = result.data;
  const canAdjust = roleHasPermission(session.role, PERMISSIONS.ADJUST_INVENTORY);
  const canViewAccounting = roleHasPermission(session.role, PERMISSIONS.VIEW_ACCOUNTING);
  const isDraft = adjustment.status === "draft";

  const [warehouse, items, contra] = await Promise.all([
    tryServer(() => serverApi.get<Warehouse>(`inventory/warehouses/${adjustment.warehouse}`)),
    roleHasPermission(session.role, PERMISSIONS.VIEW_ITEMS)
      ? tryServer(() => serverApi.list<Item>("items", { query: { page_size: 200 } }))
      : Promise.resolve({ ok: false as const, error: new Error("no permission") }),
    adjustment.contra_account && canViewAccounting
      ? tryServer(() => serverApi.get<Account>(`accounting/accounts/${adjustment.contra_account}`))
      : Promise.resolve({ ok: false as const, error: new Error("none") }),
  ]);

  const itemById = new Map((items.ok ? items.data.results : []).map((row) => [row.id, row]));
  const title = `${ADJUSTMENT_REASON_LABELS[adjustment.reason] ?? adjustment.reason} · ${formatDate(adjustment.adjustment_date)}`;

  const columns: Column<StockAdjustmentLine>[] = [
    { key: "line_number", header: "#", width: "3rem", cell: (line) => line.line_number },
    {
      key: "item",
      header: "Item",
      cell: (line) => {
        const item = itemById.get(line.item);
        return (
          <Link href={`/items/${line.item}`} className="text-brand-700 hover:underline">
            {item ? item.name : `Item ${line.item.slice(0, 8)}`}
            {item ? <span className="ml-1.5 text-xs text-ink-500 tabular">{item.sku}</span> : null}
          </Link>
        );
      },
    },
    {
      key: "direction",
      header: "Direction",
      cell: (line) => (
        <Badge tone={line.direction === "adjustment_in" ? "success" : "warning"} size="sm">
          {line.direction === "adjustment_in" ? "In" : "Out"}
        </Badge>
      ),
    },
    { key: "quantity", header: "Quantity", numeric: true, cell: (line) => <Quantity value={line.quantity} /> },
    {
      key: "unit_cost",
      header: "Unit cost",
      numeric: true,
      cell: (line) =>
        line.unit_cost ? (
          <Money value={line.unit_cost} />
        ) : (
          <span className="text-xs text-ink-500">{isDraft ? "At posting" : "Moving average"}</span>
        ),
    },
    {
      key: "notes",
      header: "Note",
      hideBelow: "md",
      cell: (line) => line.notes || <span className="text-ink-400">—</span>,
    },
  ];

  return (
    <>
      <PageHeader
        title={title}
        breadcrumbs={[{ label: "Stock adjustments", href: "/inventory/adjustments" }, { label: title }]}
        meta={<StatusBadge status={adjustment.status} map={STOCK_ADJUSTMENT_STATUS} />}
        actions={
          isDraft && canAdjust ? (
            <>
              <LinkButton href={`/inventory/adjustments/${adjustment.id}/edit`}>Edit</LinkButton>
              <DocumentAction
                resource="inventory/adjustments"
                id={adjustment.id}
                action="post"
                label="Post adjustment"
                variant="primary"
                confirmTitle="Post this adjustment?"
                confirmMessage={
                  <>
                    <p>
                      {adjustment.lines.length} {adjustment.lines.length === 1 ? "line" : "lines"} will be written to
                      the stock ledger. A posted adjustment cannot be edited or deleted.
                    </p>
                    {adjustment.contra_account ? null : (
                      <p className="mt-2 text-warning-700">
                        No contra account is set, so no journal will be posted: stock will change but the
                        ledger&apos;s inventory balance will not.
                      </p>
                    )}
                  </>
                }
                successTitle="Adjustment posted"
              />
            </>
          ) : null
        }
      />

      <PageBody>
        <Card>
          <CardHeader title="Details" />
          <CardBody>
            <DetailList
              columns={3}
              items={[
                {
                  label: "Warehouse",
                  value: warehouse.ok ? warehouse.data.name : "—",
                },
                { label: "Date", value: formatDate(adjustment.adjustment_date) },
                { label: "Reason", value: ADJUSTMENT_REASON_LABELS[adjustment.reason] ?? adjustment.reason },
                {
                  label: "Contra account",
                  value: adjustment.contra_account
                    ? contra.ok
                      ? `${contra.data.code} · ${contra.data.name}`
                      : "Configured"
                    : "None — no journal is posted",
                },
                {
                  label: "Journal",
                  value: adjustment.accounting_journal ? (
                    canViewAccounting ? (
                      <Link
                        href={`/accounting/journals/${adjustment.accounting_journal}`}
                        className="text-brand-700 hover:underline"
                      >
                        View journal
                      </Link>
                    ) : (
                      "Posted"
                    )
                  ) : (
                    "—"
                  ),
                },
                {
                  label: "Posted",
                  value: adjustment.posted_at
                    ? formatDateTime(adjustment.posted_at, { timeZone: session.timeZone })
                    : "Not posted",
                },
                ...(adjustment.reverses
                  ? [
                      {
                        label: "Reverses",
                        value: (
                          <Link href={`/inventory/adjustments/${adjustment.reverses}`} className="text-brand-700 hover:underline">
                            Original adjustment
                          </Link>
                        ),
                      },
                    ]
                  : []),
                ...(adjustment.memo ? [{ label: "Memo", value: adjustment.memo, span: true }] : []),
              ]}
            />
          </CardBody>
        </Card>

        <Section
          title="Lines"
          {...(adjustment.status === "posted"
            ? {
                description:
                  "Posted adjustments are permanent. To undo one, record a new adjustment in the opposite direction.",
              }
            : {})}
        >
          <DataTable
            caption="Adjustment lines"
            columns={columns}
            data={wholeList(adjustment.lines)}
            getRowId={(line) => line.id}
            emptyTitle="No lines"
            page={1}
            pageSize={Math.max(adjustment.lines.length, 1)}
            buildPageHref={() => `/inventory/adjustments/${adjustment.id}`}
          />
        </Section>
      </PageBody>
    </>
  );
}
