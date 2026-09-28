"use client";

import * as React from "react";
import { Button } from "@/components/ui/button";
import { FormError, FormField, Input } from "@/components/ui/field";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import type { DateString } from "@/lib/api/types";
import type { FiscalYear, FiscalYearCreateInput } from "@/types/api/accounting";
import { coversToday, fiscalYearRangeProblem } from "./fiscal-year";

/**
 * Creates the organization's current fiscal year (POST accounting/fiscal-years/
 * → accounting.services.fiscal.create_fiscal_year). The dates arrive
 * prefilled from the organization's start month, but nothing is created until
 * the user confirms them. A repeated submit is harmless: the API returns the
 * existing year for an identical range.
 */
export function FiscalYearForm({
  today,
  suggestion,
}: {
  today: DateString;
  suggestion: { start_date: DateString; end_date: DateString };
}) {
  const [startDate, setStartDate] = React.useState<string>(suggestion.start_date);
  const [endDate, setEndDate] = React.useState<string>(suggestion.end_date);
  const [submitting, setSubmitting] = React.useState(false);
  const [problem, setProblem] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });
  const [fieldErrors, setFieldErrors] = React.useState<{ start_date?: string; end_date?: string }>({});

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setProblem({ message: null, reference: null });
    setFieldErrors({});

    const rangeProblem = fiscalYearRangeProblem(startDate, endDate);
    if (rangeProblem) {
      setProblem({ message: rangeProblem, reference: null });
      return;
    }
    if (!coversToday(startDate, endDate, today)) {
      setProblem({
        message: `These dates do not include today (${today}). Set up the fiscal year you are in now to continue.`,
        reference: null,
      });
      return;
    }

    setSubmitting(true);
    const input: FiscalYearCreateInput = { start_date: startDate, end_date: endDate };
    try {
      await api.post<FiscalYear>("accounting/fiscal-years", input);
    } catch (error) {
      setProblem({
        message: formErrorOf(error) ?? "The fiscal year could not be created.",
        reference: referenceOf(error),
      });
      const errors = fieldErrorsOf(error);
      setFieldErrors({ start_date: errors.start_date?.[0], end_date: errors.end_date?.[0] });
      setSubmitting(false);
      return;
    }
    // Full navigation: requireSession() re-checks setup on the next render.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination -- the hard navigation is the point
    window.location.assign("/dashboard");
  }

  return (
    <form method="post" noValidate onSubmit={(event) => void onSubmit(event)} className="flex flex-col gap-4">
      <FormError message={problem.message} reference={problem.reference} />
      <div className="grid gap-4 sm:grid-cols-2">
        <FormField label="First day" required error={fieldErrors.start_date ?? null}>
          <Input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
        </FormField>
        <FormField label="Last day" required error={fieldErrors.end_date ?? null}>
          <Input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
        </FormField>
      </div>
      <Button type="submit" variant="primary" fullWidth loading={submitting} loadingLabel="Saving">
        Confirm fiscal year
      </Button>
    </form>
  );
}
