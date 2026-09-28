import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { OrganizationForm } from "@/features/onboarding/organization-form";
import { serverApi, tryServer } from "@/lib/api/server";
import { readSession } from "@/lib/auth/session";
import type { Organization } from "@/types/api/accounts";

export const metadata: Metadata = { title: "Set up your organization" };

/**
 * Where requireSession() sends a signed-in user who belongs to no
 * organization, and where an existing user starts a second set of books.
 *
 * It must NOT call requireSession() (that is what redirects here). It reads
 * the session directly and lists organizations with GET organizations/, which
 * is not organization-scoped (AuthenticatedAPIView), so it works with none.
 */
export default async function OnboardingOrganizationPage() {
  const session = await readSession();
  if (!session) redirect("/login?next=/onboarding/organization");

  const existing = await tryServer(() => serverApi.get<Organization[]>("organizations"));
  const count = existing.ok ? existing.data.length : 0;

  return (
    <div className="rounded-lg border border-ink-200 bg-white p-6 shadow-sm">
      <h1 className="text-base font-semibold text-ink-900">
        {count > 0 ? "Create another organization" : "Set up your organization"}
      </h1>
      <p className="mt-1 text-sm text-ink-500">
        An organization is one set of books: its own customers, ledger, GST registration and members. You become its
        owner.
      </p>

      {count > 0 ? (
        <p className="mt-3 text-sm text-ink-600">
          You already belong to {count === 1 ? "one organization" : `${count} organizations`}.{" "}
          <Link href="/dashboard" className="text-brand-700 hover:underline">
            Back to EasyBook
          </Link>
        </p>
      ) : null}

      <div className="mt-5">
        <OrganizationForm />
      </div>
    </div>
  );
}
