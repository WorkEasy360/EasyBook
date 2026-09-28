import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { CustomerForm } from "@/features/sales/customer-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import type { Customer } from "@/types/api/sales";

export const metadata: Metadata = { title: "Edit customer" };

export default async function EditCustomerPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const session = await requireSession();
  const { id } = await params;

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_CUSTOMERS)) {
    return (
      <>
        <PageHeader title="Edit customer" />
        <ForbiddenState resource="editing customers" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<Customer>(`sales/customers/${id}`));

  if (!result.ok) {
    const error = result.error;
    if (error instanceof ApiError && error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Edit customer" />
        <ErrorState message={error.message} reference={referenceOf(error)} />
      </>
    );
  }

  const customer = result.data;

  return (
    <>
      <PageHeader
        title={`Edit ${customer.display_name}`}
        breadcrumbs={[
          { label: "Customers", href: "/sales/customers" },
          { label: customer.display_name, href: `/sales/customers/${customer.id}` },
          { label: "Edit" },
        ]}
      />
      <PageBody className="max-w-3xl">
        <CustomerForm customer={customer} />
      </PageBody>
    </>
  );
}
