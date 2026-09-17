"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, FormProvider, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { FormError, FormField, Input, Textarea } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { CustomerPicker } from "@/features/shared/pickers";
import {
  EMPTY_PRICED_LINE,
  EstimatedTotals,
  PricedLinesEditor,
  fromPricedLine,
  pricedLineSchema,
  toPricedLineInput,
} from "@/features/documents/priced-lines-editor";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { todayInZone } from "@/lib/datetime";
import type { Quote, QuoteInput, QuoteUpdateInput } from "@/types/api/sales";

/**
 * Create or edit a DRAFT quote.
 *
 * A quote posts nothing — no ledger entry, no stock — at any status. Its
 * number is allocated when the draft is created (sales/services/quotes.py ::
 * create_quote). Once saved, the backend accepts only the dates, notes,
 * terms and lines (QuoteDetailView.update); the customer is shown read-only.
 */

const DATE = /^\d{4}-\d{2}-\d{2}$/;

const schema = z
  .object({
    customer_id: z.string().min(1, "Choose a customer."),
    issue_date: z.string().regex(DATE, "Enter the quote date."),
    expiry_date: z.string().refine((value) => value === "" || DATE.test(value), "Enter a valid date or leave it blank."),
    notes: z.string().max(4000),
    terms: z.string().max(4000),
    lines: z.array(pricedLineSchema).min(1, "Add at least one line."),
  })
  .refine((values) => values.expiry_date === "" || values.expiry_date >= values.issue_date, {
    // ISO dates compare correctly as strings.
    message: "The expiry date cannot be before the quote date.",
    path: ["expiry_date"],
  });

type Values = z.infer<typeof schema>;

export function QuoteForm({ quote, initialCustomerId }: { quote?: Quote; initialCustomerId?: string }) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const isEdit = Boolean(quote);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: quote
      ? {
          customer_id: quote.customer,
          issue_date: quote.issue_date,
          expiry_date: quote.expiry_date ?? "",
          notes: quote.notes,
          terms: quote.terms,
          lines: quote.lines.map(fromPricedLine),
        }
      : {
          customer_id: initialCustomerId ?? "",
          issue_date: todayInZone(org.timeZone),
          expiry_date: "",
          notes: "",
          terms: "",
          lines: [{ ...EMPTY_PRICED_LINE }],
        },
  });
  const {
    control,
    register,
    handleSubmit,
    setError,
    formState: { errors, isDirty, isSubmitting },
  } = form;

  useUnsavedChanges(isDirty && !isSubmitting);

  const mutation = useIdempotentMutation<Quote, QuoteInput | QuoteUpdateInput>(
    "sales/quotes",
    (input, idempotencyKey) =>
      quote
        ? api.patch<Quote>(`sales/quotes/${quote.id}`, input)
        : api.post<Quote>("sales/quotes", input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: isEdit ? "Draft quote saved" : "Draft quote created" });
        router.push(`/sales/quotes/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        for (const [field, messages] of Object.entries(fieldErrorsOf(error))) {
          if (field !== "lines" && field in schema.shape && messages[0]) {
            setError(field as keyof Values, { type: "server", message: messages[0] });
          }
        }
      },
    },
  );

  function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });
    const header = {
      issue_date: values.issue_date,
      expiry_date: values.expiry_date || null,
      notes: values.notes,
      terms: values.terms,
      lines: values.lines.map(toPricedLineInput),
    };
    if (quote) {
      mutation.mutate(header);
      return;
    }
    mutation.mutate({ ...header, customer_id: values.customer_id, currency: org.currency });
  }

  return (
    <FormProvider {...form}>
      <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
        <FormError message={formError.message} reference={formError.reference} />

        <Card>
          <CardHeader title="Quote" />
          <CardBody className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <FormField
              label="Customer"
              required
              error={errors.customer_id?.message ?? null}
              {...(isEdit ? { hint: "Fixed once the draft is saved." } : {})}
            >
              <Controller
                control={control}
                name="customer_id"
                render={({ field }) => (
                  <CustomerPicker
                    value={field.value || null}
                    onChange={(value) => field.onChange(value ?? "")}
                    disabled={isEdit}
                  />
                )}
              />
            </FormField>

            <FormField label="Quote date" required error={errors.issue_date?.message ?? null}>
              <Input type="date" {...register("issue_date")} />
            </FormField>

            <FormField
              label="Expiry date"
              error={errors.expiry_date?.message ?? null}
              hint="Optional. Informational only — nothing expires a quote automatically."
            >
              <Input type="date" {...register("expiry_date")} />
            </FormField>
          </CardBody>
        </Card>

        <PricedLinesEditor
          usage="sales"
          description="Enter the tax rate that applies to each line. The tax treatment decided here carries forward when the quote is converted."
        />

        <div className="grid gap-4 lg:grid-cols-5">
          <Card className="lg:col-span-3">
            <CardHeader title="Notes and terms" description="Copied to the order or invoice on conversion." />
            <CardBody className="flex flex-col gap-4">
              <FormField label="Notes" error={errors.notes?.message ?? null}>
                <Textarea rows={3} {...register("notes")} />
              </FormField>
              <FormField label="Terms" error={errors.terms?.message ?? null}>
                <Textarea rows={3} {...register("terms")} />
              </FormField>
            </CardBody>
          </Card>
          <div className="lg:col-span-2">
            <EstimatedTotals currency={org.currency} />
          </div>
        </div>

        <div className="flex items-center justify-end gap-2">
          <Button variant="secondary" onClick={() => router.back()} disabled={mutation.isPending}>
            Cancel
          </Button>
          <Button type="submit" variant="primary" loading={mutation.isPending} loadingLabel="Saving">
            {isEdit ? "Save draft" : "Save as draft"}
          </Button>
        </div>
      </form>
    </FormProvider>
  );
}
