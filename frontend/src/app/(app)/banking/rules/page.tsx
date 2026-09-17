import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/ui/data-table";
import { SortNote } from "@/components/ui/filter-bar";
import { LinkButton } from "@/components/ui/link-button";
import { Money } from "@/components/ui/money";
import { ForbiddenState } from "@/components/ui/states";
import { serverApi, tryServer } from "@/lib/api/server";
import { accountLabel, recordsById } from "@/lib/api/lookups";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import type { ApiError } from "@/lib/api/errors";
import type { Account } from "@/types/api/accounting";
import { RULE_DIRECTION_LABELS, type BankAccount, type BankRule } from "@/types/api/banking";

export const metadata: Metadata = { title: "Bank rules" };

/**
 * Bank rules, in the order they run. The first active rule that matches a
 * line decides what happens to it; rules never combine
 * (banking/services/rules.py :: find_matching_rule).
 */
export default async function BankRulesPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;
  const role = session.role;

  // Reading rules needs VIEW_BANK_TRANSACTIONS (BankRuleListCreateView);
  // changing them needs MANAGE_BANK_RULES.
  if (!roleHasPermission(role, PERMISSIONS.VIEW_BANK_TRANSACTIONS)) {
    return (
      <>
        <PageHeader title="Bank rules" />
        <ForbiddenState resource="bank rules" />
      </>
    );
  }

  const canManage = roleHasPermission(role, PERMISSIONS.MANAGE_BANK_RULES);
  const query = parseListQuery("/banking/rules", "bank-rules", params);
  const [result, accounts] = await Promise.all([
    tryServer(() => serverApi.list<BankRule>("bank-rules", { query: query.apiParams })),
    roleHasPermission(role, PERMISSIONS.VIEW_BANK_ACCOUNTS)
      ? tryServer(() => serverApi.list<BankAccount>("bank-accounts", { query: { page_size: 200 } }))
      : Promise.resolve(null),
  ]);
  const rows = result.ok ? result.data.results : [];
  const ledger = roleHasPermission(role, PERMISSIONS.VIEW_ACCOUNTING)
    ? await recordsById<Account>("accounting/accounts", rows.map((row) => row.target_account))
    : new Map<string, Account>();
  const bankAccountById = new Map((accounts?.ok ? accounts.data.results : []).map((row) => [row.id, row]));

  const columns: Column<BankRule>[] = [
    { key: "priority", header: "Priority", width: "5rem", cell: (row) => <span className="tabular">{row.priority}</span> },
    {
      key: "name",
      header: "Rule",
      cell: (row) => (
        <span>
          <span className="font-medium">{row.name}</span>
          <span className="block text-xs text-ink-500">
            {row.bank_account ? (bankAccountById.get(row.bank_account)?.name ?? "One bank account") : "All bank accounts"}
          </span>
        </span>
      ),
    },
    {
      key: "conditions",
      header: "When",
      hideBelow: "md",
      cell: (row) => (
        <ul className="flex flex-col gap-0.5 text-xs text-ink-700">
          {row.description_contains ? <li>Description contains “{row.description_contains}”</li> : null}
          {row.counterparty_contains ? <li>Counterparty contains “{row.counterparty_contains}”</li> : null}
          {row.direction !== "any" ? <li>{RULE_DIRECTION_LABELS[row.direction]}</li> : null}
          {row.amount_min !== null ? (
            <li>
              Amount at least <Money value={row.amount_min} />
            </li>
          ) : null}
          {row.amount_max !== null ? (
            <li>
              Amount at most <Money value={row.amount_max} />
            </li>
          ) : null}
        </ul>
      ),
    },
    {
      key: "action",
      header: "Then",
      cell: (row) =>
        row.action === "exclude" ? (
          "Exclude"
        ) : (
          <span>
            Categorize to {accountLabel(ledger, row.target_account)}
            {row.auto_confirm ? (
              <span className="mt-0.5 block">
                <Badge tone="warning" size="sm" marker>
                  Posts without review
                </Badge>
              </span>
            ) : null}
          </span>
        ),
    },
    {
      key: "is_active",
      header: "Status",
      cell: (row) =>
        row.is_active ? (
          <Badge tone="success" size="sm" marker>
            Active
          </Badge>
        ) : (
          <Badge tone="neutral" size="sm">
            Inactive
          </Badge>
        ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Bank rules"
        description="Rules run in priority order, lowest number first, and the first one that matches a line decides. A categorize rule posts a journal only when set to post automatically."
        actions={
          canManage ? (
            <LinkButton href="/banking/rules/new" variant="primary">
              New rule
            </LinkButton>
          ) : null
        }
      />
      <PageBody>
        <DataTable
          caption="Bank rules"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          {...(canManage ? { getRowHref: (row: BankRule) => `/banking/rules/${row.id}/edit` } : {})}
          emptyTitle="No bank rules yet"
          emptyDescription="A rule categorizes or excludes recurring statement lines, such as bank charges."
          {...(canManage ? { emptyAction: { label: "New rule", href: "/banking/rules/new" } } : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
          note={<SortNote description={capabilitiesFor("bank-rules").defaultOrder ?? "priority"} />}
        />
      </PageBody>
    </>
  );
}
