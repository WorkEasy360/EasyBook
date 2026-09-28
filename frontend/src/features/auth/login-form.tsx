"use client";

import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { FormError, FormField, Input } from "@/components/ui/field";

const schema = z.object({
  email: z.email({ message: "Enter a valid email address." }),
  password: z.string().min(1, "Enter your password."),
});

type LoginValues = z.infer<typeof schema>;

interface LoginResult {
  redirectTo?: string;
  error?: { message?: string; request_id?: string };
}

/**
 * Sign-in form.
 *
 * Credentials go to the same-origin /api/auth/login route, which exchanges
 * them for httpOnly cookies. No token ever reaches this component — there is
 * nothing here for an XSS to steal (spec §12, §80).
 */
export function LoginForm({ nextPath }: { nextPath: string | null }) {
  const [formError, setFormError] = React.useState<string | null>(null);
  const [reference, setReference] = React.useState<string | null>(null);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({
    resolver: zodResolver(schema),
    defaultValues: { email: "", password: "" },
  });

  async function onSubmit(values: LoginValues) {
    setFormError(null);
    setReference(null);

    let payload: LoginResult;
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(values),
      });
      payload = (await response.json()) as LoginResult;

      if (!response.ok) {
        setFormError(payload.error?.message ?? "Sign-in failed. Please try again.");
        setReference(payload.error?.request_id ?? null);
        return;
      }
    } catch {
      setFormError("Could not reach EasyBook. Check your connection and try again.");
      return;
    }

    // A full navigation rather than router.push: the session cookie was just
    // set, and every Server Component payload cached for the signed-out state
    // must be discarded.
    window.location.assign(safeRedirect(nextPath) ?? payload.redirectTo ?? "/dashboard");
  }

  return (
    <form
      /*
       * method="post" is a SECURITY control, not a formality. If this
       * component has not hydrated — a chunk 404, a CSP block, a slow
       * network, JS disabled — clicking the button performs a NATIVE
       * submission. The HTML default is GET, which would put the password in
       * the URL, the browser history, the Referer header and every server
       * access log. A native POST to this route simply fails instead.
       */
      method="post"
      onSubmit={(event) => void handleSubmit(onSubmit)(event)}
      className="flex flex-col gap-4"
      noValidate
    >
      <FormError message={formError} reference={reference} />

      <FormField label="Email address" required error={errors.email?.message ?? null}>
        <Input
          type="email"
          autoComplete="email"
          autoFocus
          placeholder="you@company.com"
          {...register("email")}
        />
      </FormField>

      <FormField label="Password" required error={errors.password?.message ?? null}>
        <Input type="password" autoComplete="current-password" {...register("password")} />
      </FormField>

      <Button type="submit" variant="primary" fullWidth loading={isSubmitting} loadingLabel="Signing in">
        Sign in
      </Button>
    </form>
  );
}

/**
 * Only same-site absolute PATHS are honoured. "//evil.com" and
 * "https://evil.com" both parse as off-site and are rejected — the classic
 * open-redirect via a post-login `next` parameter.
 */
function safeRedirect(target: string | null): string | null {
  if (!target) return null;
  if (!target.startsWith("/")) return null;
  if (target.startsWith("//")) return null;
  if (target.startsWith("/\\")) return null;
  return target;
}
