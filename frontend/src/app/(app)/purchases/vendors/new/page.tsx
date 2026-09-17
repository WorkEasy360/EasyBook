import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { ForbiddenState } from "@/components/ui/states";
import { VendorForm } from "@/features/purchases/vendor-form";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";

export const metadata: Metadata = { title: "New vendor" };

const CRUMBS = [{ label: "Vendors", href: "/purchases/vendors" }, { label: "New" }];

export default async function NewVendorPage() {
  const session = await requireSession();

  if (!roleHasPermission(session.role, PERMISSIONS.MANAGE_VENDORS)) {
    return (
      <>
        <PageHeader title="New vendor" breadcrumbs={CRUMBS} />
        <ForbiddenState resource="creating vendors" />
      </>
    );
  }

  return (
    <>
      <PageHeader title="New vendor" breadcrumbs={CRUMBS} />
      <PageBody className="max-w-3xl">
        <VendorForm />
      </PageBody>
    </>
  );
}
