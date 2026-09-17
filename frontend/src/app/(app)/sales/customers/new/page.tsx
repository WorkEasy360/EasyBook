import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ForbiddenState } from "@/components/ui/states";
import { CustomerForm } from "@/features/sales/customer-form";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";

export const metadata: Metadata = { title: "New customer" };

export default async function NewCustomerPage() {
  const session = await requireSession();

  // Checked on the server so the form is never sent to a role that cannot use
  // it. Django re-checks on submit regardless (spec §62).
  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_CUSTOMERS)) {
    return (
      <>
        <PageHeader
          title="New customer"
          breadcrumbs={[{ label: "Customers", href: "/sales/customers" }, { label: "New" }]}
        />
        <ForbiddenState resource="creating customers" />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="New customer"
        breadcrumbs={[{ label: "Customers", href: "/sales/customers" }, { label: "New" }]}
      />
      <PageBody className="max-w-3xl">
        <CustomerForm />
      </PageBody>
    </>
  );
}
