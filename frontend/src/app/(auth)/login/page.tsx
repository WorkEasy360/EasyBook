import type { Metadata } from "next";
import Link from "next/link";
import { AuthCard } from "@/features/auth/auth-card";
import { LoginForm } from "@/features/auth/login-form";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; reason?: string; registered?: string }>;
}) {
  const params = await searchParams;
  const expired = params.reason === "expired";

  return (
    <AuthCard
      title="Sign in"
      description="Access your organization’s books."
      notice={
        expired
          ? "Your session expired. Please sign in again."
          : params.registered
            ? "Your account is ready. Sign in to continue."
            : null
      }
      noticeTone={expired ? "warning" : "success"}
      footer={
        <>
          New to EasyBook?{" "}
          <Link href="/register" className="font-medium text-brand-700 hover:underline">
            Create an account
          </Link>
        </>
      }
    >
      {/*
        `next` is passed through as a PATH only and is re-validated in
        the form before it is used, so a crafted ?next=https://evil
        cannot turn sign-in into an open redirect (spec §80).
      */}
      <LoginForm nextPath={params.next ?? null} />
    </AuthCard>
  );
}
