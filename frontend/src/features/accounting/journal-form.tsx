"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useFieldArray, useForm, useWatch, type Control } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button, IconButton } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { TotalsPanel } from "@/components/ui/detail";
import { FormError, FormField, Input, Textarea } from "@/components/ui/field";
import { Icons } from "@/components/ui/icons";
import { Money } from "@/components/ui/money";
import { NumericInput } from "@/components/ui/numeric-input";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { AccountPicker } from "@/features/shared/pickers";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { isValidDecimal, money } from "@/lib/money";
import { todayInZone } from "@/lib/datetime";
import { estimateJournalTotals, lineSide } from "./journal-math";
import { lineErrorsOf } from "./journal-errors";
import type {
  JournalEntry,
  JournalEntryInput,
  JournalEntryUpdateInput,
  JournalLineInput,
} from "@/types/api/accounting";

/**
 * Create or edit a DRAFT manual journal.
 *
 * Saving never touches the ledger. The backend accepts an unbalanced draft
 * (accounting/services/journals.py) and enforces debits == credits only when
 * the journal is POSTED (services/posting.py :: post_journal,
 * `journal_unbalanced` / `journal_zero_total`). This form is stricter on
 * purpose — it will not save a journal it can already see would be refused
 * at posting — but the totals it shows are an estimate, and the posting
 * engine is the one that decides.
 *
 * On an existing draft only the posting date, reference, memo and lines can
 * change (JournalEntryDetailView.update); the currency and exchange rate are
 * shown fixed.
 */

const DATE = /^\d{4}-\d{2}-\d{2}$/;

const amount = z
  .string()
  .refine((value) => value.trim() === "" || (isValidDecimal(value) && money(value).gte(0)), "Enter an amount of zero or more.");

const lineSchema = z
  .object({
    account_id: z.string().min(1, "Choose an account."),
    description: z.string().max(255),
    debit: amount,
    credit: amount,
  })
  // journals.py :: _validate_line_shape — `journal_line_both_sides` / `journal_line_zero`.
  .superRefine((line, ctx) => {
    const side = lineSide(line);
    if (side === "both") {
      ctx.addIssue({ code: "custom", path: ["credit"], message: "A line is a debit or a credit, not both." });
    } else if (side === "none") {
      ctx.addIssue({ code: "custom", path: ["debit"], message: "Enter a debit or a credit." });
    }
  });

const schema = z
  .object({
    posting_date: z.string().regex(DATE, "Enter the posting date."),
    reference: z.string().max(255),
    memo: z.string().max(4000),
    lines: z.array(lineSchema).min(2, "A journal needs at least two lines."),
  })
  .superRefine((values, ctx) => {
    if (values.lines.length < 2) return;
    if (!estimateJournalTotals(values.lines).balanced) {
      ctx.addIssue({
        code: "custom",
        path: ["lines"],
        message: "Debits and credits must be equal, and above zero, before the journal can be saved.",
      });
    }
  });

type Values = z.infer<typeof schema>;
type LineValues = Values["lines"][number];

const EMPTY_LINE: LineValues = { account_id: "", description: "", debit: "", credit: "" };

function toValues(journal: JournalEntry | undefined, today: string): Values {
  if (!journal) {
    return { posting_date: today, reference: "", memo: "", lines: [{ ...EMPTY_LINE }, { ...EMPTY_LINE }] };
  }
  return {
    posting_date: journal.posting_date,
    reference: journal.reference,
    memo: journal.memo,
    lines: journal.lines.map((line) => ({
      account_id: line.account,
      description: line.description,
      // The API returns "0.00" for the empty side; show it blank.
      debit: money(line.debit).eq(0) ? "" : line.debit,
      credit: money(line.credit).eq(0) ? "" : line.credit,
    })),
  };
}

function toLineInput(line: LineValues): JournalLineInput {
  return {
    account_id: line.account_id,
    description: line.description.trim(),
    debit: line.debit.trim() === "" ? "0" : line.debit,
    credit: line.credit.trim() === "" ? "0" : line.credit,
  };
}

export function JournalForm({ journal }: { journal?: JournalEntry }) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const isEdit = Boolean(journal);
  const currency = journal?.currency ?? org.currency;
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const {
    control,
    register,
    handleSubmit,
    setValue,
    setError,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: toValues(journal, todayInZone(org.timeZone)),
  });
  const { fields, append, remove } = useFieldArray({ control, name: "lines" });
  useUnsavedChanges(isDirty && !isSubmitting);

  const mutation = useIdempotentMutation<JournalEntry, JournalEntryInput | JournalEntryUpdateInput>(
    "accounting/journals",
    (input, idempotencyKey) =>
      journal
        ? api.patch<JournalEntry>(`accounting/journals/${journal.id}`, input)
        : api.post<JournalEntry>("accounting/journals", input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: isEdit ? "Draft journal saved" : "Draft journal created" });
        router.push(`/accounting/journals/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        for (const [field, messages] of Object.entries(fieldErrorsOf(error))) {
          if ((field === "posting_date" || field === "reference" || field === "memo") && messages[0]) {
            setError(field, { type: "server", message: messages[0] });
          }
        }
        lineErrorsOf(error).forEach((lineErrors, index) => {
          for (const [field, message] of Object.entries(lineErrors)) {
            if (field === "account_id" || field === "description" || field === "debit" || field === "credit") {
              setError(`lines.${index}.${field}`, { type: "server", message });
            }
          }
        });
      },
    },
  );

  function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });
    const lines = values.lines.map(toLineInput);
    if (journal) {
      mutation.mutate({
        posting_date: values.posting_date,
        reference: values.reference,
        memo: values.memo,
        lines,
      });
      return;
    }
    mutation.mutate({
      posting_date: values.posting_date,
      currency,
      // A manual journal is entered in the organization currency, where the
      // rate is 1 by definition. Base amounts are debit × rate, so any other
      // value would silently restate every line in the ledger.
      exchange_rate: "1",
      reference: values.reference,
      memo: values.memo,
      lines,
    });
  }

  const linesError = errors.lines?.message ?? errors.lines?.root?.message;

  return (
    <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
      <FormError message={formError.message} reference={formError.reference} />

      <Card>
        <CardHeader title="Journal" />
        <CardBody className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <FormField
            label="Posting date"
            required
            error={errors.posting_date?.message ?? null}
            hint="Must fall in an open fiscal year to post."
          >
            <Input type="date" {...register("posting_date")} />
          </FormField>
          <FormField label="Reference" error={errors.reference?.message ?? null} className="lg:col-span-1">
            <Input {...register("reference")} />
          </FormField>
          <FormField label="Currency" hint={isEdit ? "Fixed once the draft is saved." : "The organization currency."} disabled>
            <Input value={currency} readOnly />
          </FormField>
          <FormField
            label="Exchange rate"
            hint={isEdit ? "Fixed once the draft is saved." : "1 in the organization currency."}
            disabled
          >
            <Input className="tabular" value={journal?.exchange_rate ?? "1"} readOnly />
          </FormField>
          <FormField label="Memo" error={errors.memo?.message ?? null} className="sm:col-span-2 lg:col-span-4">
            <Textarea rows={2} {...register("memo")} />
          </FormField>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Lines"
          description="Each line is either a debit or a credit on one account."
          actions={
            <Button
              variant="secondary"
              size="sm"
              leadingIcon={<Icons.plus className="size-3.5" />}
              onClick={() => append({ ...EMPTY_LINE })}
            >
              Add line
            </Button>
          }
        />
        <CardBody className="flex flex-col gap-3">
          {fields.map((field, index) => {
            const lineErrors = errors.lines?.[index];
            return (
              <fieldset key={field.id} className="grid gap-3 rounded-md border border-ink-200 p-3 sm:grid-cols-12">
                <legend className="sr-only">Line {index + 1}</legend>

                <FormField label="Account" required error={lineErrors?.account_id?.message ?? null} className="sm:col-span-4">
                  <Controller
                    control={control}
                    name={`lines.${index}.account_id`}
                    render={({ field: account }) => (
                      <AccountPicker value={account.value || null} onChange={(value) => account.onChange(value ?? "")} />
                    )}
                  />
                </FormField>

                <FormField label="Description" error={lineErrors?.description?.message ?? null} className="sm:col-span-3">
                  <Input {...register(`lines.${index}.description`)} />
                </FormField>

                <FormField label="Debit" error={lineErrors?.debit?.message ?? null} className="sm:col-span-2">
                  <Controller
                    control={control}
                    name={`lines.${index}.debit`}
                    render={({ field: debit }) => (
                      <NumericInput
                        nonNegative
                        currency={currency}
                        value={debit.value}
                        onBlur={debit.onBlur}
                        onValueChange={(value) => {
                          debit.onChange(value);
                          // One side per line: typing a debit clears the credit.
                          if (value !== "" && isValidDecimal(value) && !money(value).eq(0)) {
                            setValue(`lines.${index}.credit`, "", { shouldDirty: true });
                          }
                        }}
                      />
                    )}
                  />
                </FormField>

                <FormField label="Credit" error={lineErrors?.credit?.message ?? null} className="sm:col-span-2">
                  <Controller
                    control={control}
                    name={`lines.${index}.credit`}
                    render={({ field: credit }) => (
                      <NumericInput
                        nonNegative
                        currency={currency}
                        value={credit.value}
                        onBlur={credit.onBlur}
                        onValueChange={(value) => {
                          credit.onChange(value);
                          if (value !== "" && isValidDecimal(value) && !money(value).eq(0)) {
                            setValue(`lines.${index}.debit`, "", { shouldDirty: true });
                          }
                        }}
                      />
                    )}
                  />
                </FormField>

                <div className="flex items-end justify-end sm:col-span-1">
                  <IconButton
                    label={`Remove line ${index + 1}`}
                    icon={<Icons.close className="size-4" />}
                    disabled={fields.length <= 2}
                    onClick={() => remove(index)}
                  />
                </div>
              </fieldset>
            );
          })}
        </CardBody>
      </Card>

      <div className="grid gap-4 lg:grid-cols-5">
        <div className="lg:col-span-3" />
        <div className="lg:col-span-2">
          <BalanceEstimate control={control} currency={currency} error={linesError ?? null} />
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
  );
}

/**
 * Live Debits / Credits / Difference while typing. Labelled an estimate: the
 * posting engine re-sums the saved lines and is the only authority on whether
 * the journal balances.
 */
function BalanceEstimate({
  control,
  currency,
  error,
}: {
  control: Control<Values>;
  currency: string;
  error: string | null;
}) {
  const lines = useWatch({ control, name: "lines" });
  const totals = estimateJournalTotals(lines ?? []);

  return (
    <Card>
      <CardHeader
        title="Balance (estimate)"
        description="Calculated while you type. The ledger re-checks debits = credits when the journal is posted and refuses it otherwise."
      />
      <CardBody className="flex flex-col items-end gap-2">
        <TotalsPanel
          rows={[
            { label: "Debits", value: <Money value={totals.debit} currency={currency} /> },
            { label: "Credits", value: <Money value={totals.credit} currency={currency} /> },
            {
              label: "Difference",
              value: <Money value={totals.difference} currency={currency} strong />,
              emphasis: true,
            },
          ]}
        />
        <p
          className={totals.balanced ? "text-xs font-medium text-success-700" : "text-xs font-medium text-warning-700"}
          aria-live="polite"
        >
          {totals.balanced ? "Balanced — ready to save." : "Not balanced yet."}
        </p>
        {!totals.complete ? <p className="text-xs text-ink-500">Lines with an invalid amount are not counted.</p> : null}
        {error ? (
          <p role="alert" className="text-xs text-danger-600">
            {error}
          </p>
        ) : null}
      </CardBody>
    </Card>
  );
}
