"use client";

import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Button } from "@/components/ui/button";
import { FormError, FormField, Input } from "@/components/ui/field";
import { fieldErrorsOf, formErrorOf, referenceOf, toApiError } from "@/lib/api/errors";
import {
  EMPTY_REGISTER_VALUES,
  PASSWORD_MIN_LENGTH,
  isServerField,
  registerSchema,
  type RegisterValues,
} from "./register-schema";

/**
 * Sign-up form.
 *
 * Posts to the same-origin /api/auth/register route, which creates the
 * account and sets the httpOnly session cookies — no token reaches this
 * component (spec §12, §80). Backend field errors (a taken email, a password
 * the validators reject) are attached to their inputs.
 */
export function RegisterForm() {
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<RegisterValues>({
    resolver: zodResolver(registerSchema),
    defaultValues: EMPTY_REGISTER_VALUES,
  });

  async function onSubmit(values: RegisterValues) {
    setFormError({ message: null, reference: null });

    let response: Response;
    let payload: unknown;
    try {
      response = await fetch("/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          first_name: values.first_name,
          last_name: values.last_name,
          email: values.email,
          password: values.password,
        }),
      });
      payload = await response.json();
    } catch {
      setFormError({ message: "Could not reach EasyBook. Check your connection and try again.", reference: null });
      return;
    }

    if (!response.ok) {
      const error = toApiError(response.status, payload, null);
      const fields = fieldErrorsOf(error);
      let attached = false;
      for (const [field, messages] of Object.entries(fields)) {
        if (isServerField(field) && messages.length > 0) {
          setError(field, { type: "server", message: messages.join(" ") });
          attached = true;
        }
      }
      setFormError({
        message: attached ? "Please fix the highlighted fields." : (formErrorOf(error) ?? error.message),
        reference: referenceOf(error),
      });
      return;
    }

    // A full navigation: the session cookie was just set, and nothing cached
    // for the signed-out state may survive.
    const redirectTo = (payload as { redirectTo?: unknown } | null)?.redirectTo;
    window.location.assign(typeof redirectTo === "string" ? redirectTo : "/onboarding/organization");
  }

  return (
    <form
      // POST, never the GET default: an unhydrated form must not put the
      // password in the URL (see login-form.tsx).
      method="post"
      onSubmit={(event) => void handleSubmit(onSubmit)(event)}
      className="flex flex-col gap-4"
      noValidate
    >
      <FormError message={formError.message} reference={formError.reference} />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <FormField label="First name" error={errors.first_name?.message ?? null}>
          <Input autoComplete="given-name" autoFocus {...register("first_name")} />
        </FormField>
        <FormField label="Last name" error={errors.last_name?.message ?? null}>
          <Input autoComplete="family-name" {...register("last_name")} />
        </FormField>
      </div>

      <FormField label="Email address" required error={errors.email?.message ?? null}>
        <Input type="email" autoComplete="email" placeholder="you@company.com" {...register("email")} />
      </FormField>

      <FormField
        label="Password"
        required
        hint={`At least ${PASSWORD_MIN_LENGTH} characters. Avoid common passwords and ones like your name or email.`}
        error={errors.password?.message ?? null}
      >
        <Input type="password" autoComplete="new-password" {...register("password")} />
      </FormField>

      <FormField label="Confirm password" required error={errors.confirm_password?.message ?? null}>
        <Input type="password" autoComplete="new-password" {...register("confirm_password")} />
      </FormField>

      <Button type="submit" variant="primary" fullWidth loading={isSubmitting} loadingLabel="Creating account">
        Create account
      </Button>
    </form>
  );
}
