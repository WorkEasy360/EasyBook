import type { Metadata } from "next";
import Link from "next/link";
import { AuthCard } from "@/features/auth/auth-card";
import { LoginForm } from "@/features/auth/login-form";
import { Workeasy360Button } from "@/features/auth/workeasy360-button";
import { readSignInMethods, safePath, ssoErrorMessage } from "@/lib/auth/workeasy360";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; reason?: string; registered?: string; sso_error?: string; method?: string }>;
}) {
  const params = await searchParams;
  const expired = params.reason === "expired";
  const ssoError = ssoErrorMessage(params.sso_error);
  // Which methods Django allows (WORKEASY360_SSO_ONLY): in production only
  // Workeasy360, locally usually both.
  const methods = await readSignInMethods();
  const nextPath = safePath(params.next);
  // With Workeasy360 offered, EasyBook's own password is "another sign-in
  // method", shown on request (?method=password) - a plain link, no script.
  const showPassword = methods.password && (!methods.workeasy360 || params.method === "password");
  const passwordHref = `/login?${new URLSearchParams({ ...(nextPath ? { next: nextPath } : {}), method: "password" })}`;

  return (
    <AuthCard
      title="Sign in"
      description={
        methods.workeasy360
          ? "EasyBook uses your Workeasy360 account - the same email and password you use for Workeasy360."
          : "Access your organization’s books."
      }
      notice={
        ssoError ??
        (expired
          ? "Your session expired. Please sign in again."
          : params.registered
            ? "Your account is ready. Sign in to continue."
            : null)
      }
      noticeTone={ssoError || expired ? "warning" : "success"}
      footer={
        methods.password ? (
          <>
            New to EasyBook?{" "}
            <Link href="/register" className="font-medium text-brand-700 hover:underline">
              Create an account
            </Link>
          </>
        ) : null
      }
    >
      <div className="flex flex-col gap-4">
        {methods.workeasy360 ? (
          <section aria-labelledby="workeasy360-access" className="flex flex-col gap-2 rounded-md border border-ink-200 p-4">
            <h2 id="workeasy360-access" className="text-sm font-semibold text-ink-900">
              Already a Workeasy360 user?
            </h2>
            <p className="text-sm text-ink-600">Access EasyBook using your Workeasy360 account.</p>
            <Workeasy360Button nextPath={nextPath} primary />
          </section>
        ) : null}
        {methods.workeasy360 && methods.password && !showPassword ? (
          <a
            href={passwordHref}
            className="inline-flex h-9 w-full items-center justify-center rounded-md border border-ink-300 bg-white px-3.5 text-sm font-medium text-ink-800 hover:bg-ink-50"
          >
            Use another sign-in method
          </a>
        ) : null}
        {methods.workeasy360 && showPassword ? (
          <p className="text-center text-xs text-ink-500" aria-hidden="true">
            or sign in with an EasyBook password
          </p>
        ) : null}
        {/*
          `next` is passed through as a PATH only and is re-validated in
          the form before it is used, so a crafted ?next=https://evil
          cannot turn sign-in into an open redirect (spec §80).
        */}
        {showPassword ? <LoginForm nextPath={params.next ?? null} /> : null}
      </div>
    </AuthCard>
  );
}
