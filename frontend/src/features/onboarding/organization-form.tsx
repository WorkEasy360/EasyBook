"use client";

import * as React from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { FormError, FormField, Input, Select } from "@/components/ui/field";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { MONTHS } from "@/features/settings/months";
import type { Organization, OrganizationCreateInput } from "@/types/api/accounts";
import { isValidTimeZone } from "./time-zone";

/**
 * Creates an organization (POST organizations/ — OrganizationCreateSerializer)
 * and opens it.
 *
 * The serializer checks only that the currency code exists; it does not
 * validate the time zone or the month range, and a bad time zone would break
 * every date the app renders for that organization. So both are checked here
 * before anything is sent.
 *
 * After creation the new organization is selected through the same
 * /api/auth/organization route the switcher uses (it verifies membership
 * before writing the cookie), then a FULL navigation loads /dashboard so no
 * state from the organization-less session survives.
 *
 * No fiscal year is created with the organization, and none can be created
 * from the app (BACKEND CONTRACT BLOCKER: a fiscal-year create endpoint), so
 * the form says plainly that posting will not work yet.
 */

const schema = z.object({
  name: z.string().trim().min(1, "Enter the organization's name.").max(255),
  legal_name: z.string().trim().max(255),
  default_currency: z
    .string()
    .trim()
    .regex(/^[A-Za-z]{3}$/, "Enter a three-letter currency code, such as INR."),
  timezone: z
    .string()
    .trim()
    .max(64)
    .refine((value) => isValidTimeZone(value), "Enter a time zone such as Asia/Kolkata."),
  fiscal_year_start_month: z.string().regex(/^(?:[1-9]|1[0-2])$/, "Choose a month."),
  gstin: z.string().trim().max(15, "A GSTIN has 15 characters."),
});

type Values = z.infer<typeof schema>;

const SERVER_FIELDS = new Set<keyof Values>([
  "name",
  "legal_name",
  "default_currency",
  "timezone",
  "fiscal_year_start_month",
  "gstin",
]);

export function OrganizationForm() {
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });
  // Once created, a failed switch must not create the organization again.
  const [created, setCreated] = React.useState<Organization | null>(null);
  const [opening, setOpening] = React.useState(false);

  const {
    register,
    handleSubmit,
    setError,
    formState: { errors, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: "",
      legal_name: "",
      default_currency: "INR",
      timezone: "Asia/Kolkata",
      fiscal_year_start_month: "4",
      gstin: "",
    },
  });

  async function open(organization: Organization) {
    setOpening(true);
    setFormError({ message: null, reference: null });
    try {
      const response = await fetch("/api/auth/organization", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ organizationId: organization.id }),
      });
      if (!response.ok) {
        const payload = (await response.json().catch(() => null)) as {
          error?: { message?: string; request_id?: string };
        } | null;
        setFormError({
          message: `${organization.name} was created, but could not be opened. ${payload?.error?.message ?? ""}`.trim(),
          reference: payload?.error?.request_id ?? null,
        });
        setOpening(false);
        return;
      }
    } catch {
      setFormError({
        message: `${organization.name} was created, but EasyBook could not be reached to open it. Try again.`,
        reference: null,
      });
      setOpening(false);
      return;
    }
    // A full navigation, not router.push: the organization cookie just
    // changed, and nothing rendered for the previous state may be reused.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- the hard navigation is the point
    window.location.assign("/dashboard");
  }

  async function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });
    const input: OrganizationCreateInput = {
      name: values.name,
      legal_name: values.legal_name,
      default_currency: values.default_currency.toUpperCase(),
      timezone: values.timezone,
      fiscal_year_start_month: Number(values.fiscal_year_start_month),
      gstin: values.gstin.toUpperCase(),
    };
    try {
      const organization = await api.post<Organization>("organizations", input);
      setCreated(organization);
      await open(organization);
    } catch (error) {
      setFormError({ message: formErrorOf(error) ?? "The organization could not be created.", reference: referenceOf(error) });
      for (const [field, messages] of Object.entries(fieldErrorsOf(error))) {
        if (SERVER_FIELDS.has(field as keyof Values) && messages[0]) {
          const message =
            field === "default_currency" ? "That currency is not available. Use a code such as INR or USD." : messages[0];
          setError(field as keyof Values, { type: "server", message });
        }
      }
    }
  }

  if (created) {
    return (
      <div className="flex flex-col gap-4">
        <FormError message={formError.message} reference={formError.reference} />
        <p role="status" className="text-sm text-ink-700">
          {opening ? `Opening ${created.name}…` : `${created.name} is ready.`}
        </p>
        {!opening ? (
          <Button variant="primary" onClick={() => void open(created)}>
            Open {created.name}
          </Button>
        ) : null}
      </div>
    );
  }

  return (
    <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
      <FormError message={formError.message} reference={formError.reference} />

      <div className="grid gap-4 sm:grid-cols-2">
        <FormField label="Organization name" required error={errors.name?.message ?? null} className="sm:col-span-2">
          <Input autoComplete="organization" autoFocus {...register("name")} />
        </FormField>

        <FormField
          label="Legal name"
          error={errors.legal_name?.message ?? null}
          hint="As registered, if different."
          className="sm:col-span-2"
        >
          <Input {...register("legal_name")} />
        </FormField>

        <FormField
          label="Base currency"
          required
          error={errors.default_currency?.message ?? null}
          hint="Three-letter code, e.g. INR."
        >
          <Input className="uppercase" maxLength={3} autoComplete="off" {...register("default_currency")} />
        </FormField>

        <FormField
          label="Time zone"
          required
          error={errors.timezone?.message ?? null}
          hint="Dates and times are shown in this zone, e.g. Asia/Kolkata."
        >
          <Input autoComplete="off" {...register("timezone")} />
        </FormField>

        <FormField label="Fiscal year starts in" required error={errors.fiscal_year_start_month?.message ?? null}>
          <Select {...register("fiscal_year_start_month")}>
            {MONTHS.map((month, index) => (
              <option key={month} value={String(index + 1)}>
                {month}
              </option>
            ))}
          </Select>
        </FormField>

        <FormField label="GSTIN" error={errors.gstin?.message ?? null} hint="Leave blank if not registered.">
          <Input className="uppercase" maxLength={15} autoComplete="off" {...register("gstin")} />
        </FormField>
      </div>

      <p role="note" className="rounded-md border border-warning-100 bg-warning-50 px-3 py-2 text-sm text-warning-700">
        Fiscal years cannot be set up in the app yet. Until one exists for the organization, invoices, bills, payments
        and journals can be saved as drafts but not posted.
      </p>

      <Button type="submit" variant="primary" fullWidth loading={isSubmitting} loadingLabel="Creating">
        Create organization
      </Button>
    </form>
  );
}
