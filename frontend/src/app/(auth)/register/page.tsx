import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { AuthCard } from "@/features/auth/auth-card";
import { RegisterForm } from "@/features/auth/register-form";
import { readSignInMethods } from "@/lib/auth/workeasy360";

export const metadata: Metadata = { title: "Create account" };

export default async function RegisterPage() {
  // With Workeasy360 as the only way in, accounts are created in Workeasy360
  // (Django refuses /auth/register/ too); there is nothing to fill in here.
  if (!(await readSignInMethods()).password) redirect("/login");

  return (
    <AuthCard
      title="Create your account"
      description="Next you’ll set up your organization’s books."
      footer={
        <>
          Already have an account?{" "}
          <Link href="/login" className="font-medium text-brand-700 hover:underline">
            Sign in
          </Link>
        </>
      }
    >
      <RegisterForm />
    </AuthCard>
  );
}
