import type { Metadata } from "next";
import Link from "next/link";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar } from "@/components/ui/filter-bar";
import { Badge } from "@/components/ui/badge";
import { Money } from "@/components/ui/money";
import { ForbiddenState } from "@/components/ui/states";
import { serverApi, tryServer } from "@/lib/api/server";
import { parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import type { ApiError } from "@/lib/api/errors";
import type { Customer } from "@/types/api/sales";

export const metadata: Metadata = { title: "Customers" };

const RESOURCE = "sales/customers";

export default async function CustomersPage({
  searchParams,
}: {
  searchParams: Promise<RawSearchParams>;
}) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_CUSTOMERS)) {
    return (
      <>
        <PageHeader title="Customers" />
        <ForbiddenState resource="customers" />
      </>
    );
  }

  const canManage = roleHasPermission(session.role, PERMISSIONS.MANAGE_CUSTOMERS);
  const query = parseListQuery("/sales/customers", RESOURCE, params);

  const result = await tryServer(() =>
    serverApi.list<Customer>(RESOURCE, { query: query.apiParams }),
  );

  const columns: Column<Customer>[] = [
    {
      key: "display_name",
      header: "Customer",
      cell: (customer) => customer.display_name,
    },
    {
      key: "customer_code",
      header: "Code",
      hideBelow: "sm",
      cell: (customer) => <span className="tabular text-ink-600">{customer.customer_code}</span>,
    },
    {
      key: "email",
      header: "Email",
      hideBelow: "md",
      cell: (customer) =>
        customer.email ? (
          <a href={`mailto:${customer.email}`} className="text-ink-700 hover:underline">
            {customer.email}
          </a>
        ) : (
          <span className="text-ink-400">—</span>
        ),
    },
    {
      key: "gstin",
      header: "GSTIN",
      hideBelow: "lg",
      cell: (customer) =>
        customer.gstin ? (
          <span className="tabular text-ink-600">{customer.gstin}</span>
        ) : (
          <span className="text-ink-400">—</span>
        ),
    },
    {
      key: "payment_terms_days",
      header: "Terms",
      numeric: true,
      hideBelow: "lg",
      cell: (customer) => <span className="text-ink-600">Net {customer.payment_terms_days}</span>,
    },
    {
      key: "credit_limit",
      header: "Credit limit",
      numeric: true,
      hideBelow: "md",
      cell: (customer) =>
        customer.credit_limit ? (
          <Money value={customer.credit_limit} currency={customer.currency} />
        ) : (
          <span className="text-ink-400">—</span>
        ),
    },
    {
      key: "is_active",
      header: "Status",
      cell: (customer) => (
        <Badge tone={customer.is_active ? "success" : "neutral"} marker={customer.is_active}>
          {customer.is_active ? "Active" : "Inactive"}
        </Badge>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Customers"
        description="People and businesses you invoice."
        actions={
          canManage ? (
            <Link
              href="/sales/customers/new"
              className="inline-flex h-9 items-center rounded-md border border-brand-700 bg-brand-700 px-3.5 text-sm font-medium text-white transition-colors hover:bg-brand-800"
            >
              New customer
            </Link>
          ) : null
        }
      />

      <PageBody>
        <FilterBar
          groups={[
            {
              key: "is_active",
              label: "Status",
              value: query.filters["is_active"] ?? "",
              options: [
                { value: "true", label: "Active" },
                { value: "false", label: "Inactive" },
              ],
            },
          ]}
          buildFilterHref={query.buildFilterHref}
          clearHref={query.clearHref}
          activeFilterCount={query.activeFilterCount}
        />

        <DataTable
          caption="Customers"
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(customer) => customer.id}
          getRowHref={(customer) => `/sales/customers/${customer.id}`}
          emptyTitle="No customers yet"
          emptyDescription="Add a customer to start quoting and invoicing."
          {...(canManage
            ? { emptyAction: { label: "New customer", href: "/sales/customers/new" } }
            : {})}
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={query.buildPageHref}
        />
      </PageBody>
    </>
  );
}
