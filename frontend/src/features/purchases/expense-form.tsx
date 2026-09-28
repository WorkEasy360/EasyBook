"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Checkbox, FormError, FormField, Input, Textarea } from "@/components/ui/field";
import { NumericInput, PercentInput } from "@/components/ui/numeric-input";
import { TotalsPanel } from "@/components/ui/detail";
import { Money } from "@/components/ui/money";
import { useToast } from "@/components/ui/toast";
import { useCan, useOrg } from "@/components/providers/org-provider";
import { AccountPicker, CustomerPicker, ProjectPicker, VendorPicker } from "@/features/shared/pickers";
import { ExpenseAccountSuggestion } from "@/features/ai/expense-account-suggestion";
import { PERMISSIONS } from "@/lib/authz/permissions";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { formErrorOf, referenceOf } from "@/lib/api/errors";
import { isValidDecimal, money } from "@/lib/money";
import { todayInZone } from "@/lib/datetime";
import { serverFieldErrors } from "./form-errors";
import { estimateExpense } from "./quantities";
import type { Expense, ExpenseInput, ExpenseUpdateInput } from "@/types/api/purchases";

/**
 * Create or edit a DRAFT expense — a single-amount cost with no item and no
 * stock (purchases/services/expenses.py).
 *
 * Saving posts nothing. Posting, from the expense page, debits the expense
 * account and recoverable tax and credits the paid-through account: an asset
 * (bank/cash, paid now) or a liability (payable later) — the only two types
 * the service accepts.
 *
 * Tax and total are computed by the server from amount and rate; the panel
 * here is an estimate and is labelled as one. After save, the expense page
 * shows the server's figures.
 *
 * ExpenseUpdateSerializer accepts only the date, amount, tax rate, reference,
 * description, billable flag and notes. Accounts, vendor, customer and
 * project are fixed at creation and shown read-only when editing.
 */

const DATE = /^\d{4}-\d{2}-\d{2}$/;

const schema = z
  .object({
    expense_date: z.string().regex(DATE, "Enter the expense date."),
    description: z.string().max(255, "At most 255 characters."),
    amount: z.string().refine((value) => isValidDecimal(value) && money(value).gt(0), "Enter an amount above zero."),
    tax_rate: z
      .string()
      .refine((value) => value.trim() === "" || (isValidDecimal(value) && money(value).gte(0)), "The tax rate cannot be negative."),
    expense_account_id: z.string().min(1, "Choose the expense account."),
    paid_through_account_id: z.string().min(1, "Choose the account it was paid through."),
    tax_recoverable_account_id: z.string().nullable(),
    vendor_id: z.string().nullable(),
    reference: z.string().max(255, "At most 255 characters."),
    is_billable: z.boolean(),
    customer_id: z.string().nullable(),
    project_id: z.string().nullable(),
    notes: z.string().max(4000),
  })
  // services/expenses.py :: billable_customer_required.
  .refine((values) => !values.is_billable || Boolean(values.customer_id), {
    message: "A billable expense must name the customer it is billable to.",
    path: ["customer_id"],
  });

type Values = z.infer<typeof schema>;

const FIELDS = Object.keys(schema.shape);

function toValues(expense: Expense | undefined, today: string, vendorId?: string): Values {
  if (expense) {
    return {
      expense_date: expense.expense_date,
      description: expense.description,
      amount: expense.amount,
      tax_rate: expense.tax_rate,
      expense_account_id: expense.expense_account,
      paid_through_account_id: expense.paid_through_account,
      tax_recoverable_account_id: expense.tax_recoverable_account,
      vendor_id: expense.vendor,
      reference: expense.reference,
      is_billable: expense.is_billable,
      customer_id: expense.customer,
      project_id: expense.project,
      notes: expense.notes,
    };
  }
  return {
    expense_date: today,
    description: "",
    amount: "",
    tax_rate: "",
    expense_account_id: "",
    paid_through_account_id: "",
    tax_recoverable_account_id: null,
    vendor_id: vendorId ?? null,
    reference: "",
    is_billable: false,
    customer_id: null,
    project_id: null,
    notes: "",
  };
}

export function ExpenseForm({ expense, vendorId }: { expense?: Expense; vendorId?: string }) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const isEdit = Boolean(expense);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const {
    control,
    register,
    handleSubmit,
    setError,
    setValue,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: toValues(expense, todayInZone(org.timeZone), vendorId),
  });

  useUnsavedChanges(isDirty && !isSubmitting);

  // The AI suggestion needs both permissions — the backend refuses it without
  // VIEW_ACCOUNTING. It is offered only while the account can still be chosen.
  const { canAll } = useCan();
  const canSuggest = !isEdit && canAll([PERMISSIONS.USE_AI_ASSISTANT, PERMISSIONS.VIEW_ACCOUNTING]);
  const [vendorName, setVendorName] = React.useState<string | undefined>(undefined);
  const description = useWatch({ control, name: "description" });

  const amount = useWatch({ control, name: "amount" });
  const taxRate = useWatch({ control, name: "tax_rate" });
  const taxAccount = useWatch({ control, name: "tax_recoverable_account_id" });
  const customer = useWatch({ control, name: "customer_id" });
  const estimate = estimateExpense(amount, taxRate);
  const taxed = isValidDecimal(taxRate) && money(taxRate).gt(0);

  const mutation = useIdempotentMutation<Expense, ExpenseInput | ExpenseUpdateInput>(
    "purchases/expenses",
    (input, idempotencyKey) =>
      expense
        ? api.patch<Expense>(`purchases/expenses/${expense.id}`, input)
        : api.post<Expense>("purchases/expenses", input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: isEdit ? "Draft expense saved" : "Draft expense created" });
        router.push(`/purchases/expenses/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        const pairs = serverFieldErrors(error, FIELDS, {
          billable_customer_required: "customer_id",
          expense_amount_invalid: "amount",
          expense_tax_rate_invalid: "tax_rate",
          vendor_inactive: "vendor_id",
        });
        for (const [field, message] of pairs) {
          setError(field as keyof Values, { type: "server", message });
        }
      },
    },
  );

  function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });
    const rate = values.tax_rate.trim() === "" ? "0" : values.tax_rate;
    if (expense) {
      mutation.mutate({
        expense_date: values.expense_date,
        amount: values.amount,
        tax_rate: rate,
        reference: values.reference,
        description: values.description,
        is_billable: values.is_billable,
        notes: values.notes,
      });
      return;
    }
    mutation.mutate({
      expense_date: values.expense_date,
      description: values.description,
      amount: values.amount,
      tax_rate: rate,
      expense_account_id: values.expense_account_id,
      paid_through_account_id: values.paid_through_account_id,
      tax_recoverable_account_id: values.tax_recoverable_account_id,
      vendor_id: values.vendor_id,
      reference: values.reference,
      is_billable: values.is_billable,
      customer_id: values.customer_id,
      project_id: values.project_id,
      notes: values.notes,
    });
  }

  const lockedHint = isEdit ? "Fixed once the draft is saved." : undefined;

  return (
    <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
      <FormError message={formError.message} reference={formError.reference} />

      <Card>
        <CardHeader title="Expense" />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <FormField label="Expense date" required error={errors.expense_date?.message ?? null} hint="The posting date.">
            <Input type="date" {...register("expense_date")} />
          </FormField>

          <FormField label="Description" error={errors.description?.message ?? null}>
            <Input placeholder="What was bought" {...register("description")} />
          </FormField>

          <FormField label="Amount" required error={errors.amount?.message ?? null} hint="Before tax.">
            <Controller
              control={control}
              name="amount"
              render={({ field }) => (
                <NumericInput nonNegative value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
              )}
            />
          </FormField>

          <FormField label="Tax rate" error={errors.tax_rate?.message ?? null} hint="Leave blank for no tax.">
            <Controller
              control={control}
              name="tax_rate"
              render={({ field }) => (
                <PercentInput nonNegative value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
              )}
            />
          </FormField>

          <FormField label="Reference" error={errors.reference?.message ?? null} hint="A receipt or invoice number.">
            <Input {...register("reference")} />
          </FormField>

          <FormField
            label="Vendor"
            error={errors.vendor_id?.message ?? null}
            hint={lockedHint ?? "Optional. Who was paid."}
          >
            <Controller
              control={control}
              name="vendor_id"
              render={({ field }) => (
                <VendorPicker
                  value={field.value}
                  onChange={field.onChange}
                  onSelectVendor={(vendor) => setVendorName(vendor?.display_name)}
                  disabled={isEdit}
                />
              )}
            />
          </FormField>
        </CardBody>
      </Card>

      <div className="grid gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader title="Accounts" />
          <CardBody className="grid gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-2">
              <FormField
                label="Expense account"
                required
                error={errors.expense_account_id?.message ?? null}
                hint={lockedHint ?? "Debited with the amount before tax."}
              >
                <Controller
                  control={control}
                  name="expense_account_id"
                  render={({ field }) => (
                    <AccountPicker
                      accountType="expense"
                      value={field.value || null}
                      onChange={(value) => field.onChange(value ?? "")}
                      disabled={isEdit}
                    />
                  )}
                />
              </FormField>
              {canSuggest ? (
                <ExpenseAccountSuggestion
                  description={description}
                  {...(vendorName ? { vendorName } : {})}
                  onPick={(account) => setValue("expense_account_id", account.id, { shouldDirty: true, shouldValidate: true })}
                />
              ) : null}
            </div>

            <FormField
              label="Paid through"
              required
              error={errors.paid_through_account_id?.message ?? null}
              hint={lockedHint ?? "A bank or cash account if already paid, or a payable (liability) account if it is owed."}
            >
              <Controller
                control={control}
                name="paid_through_account_id"
                render={({ field }) => (
                  <AccountPicker value={field.value || null} onChange={(value) => field.onChange(value ?? "")} disabled={isEdit} />
                )}
              />
            </FormField>

            <FormField
              label="Tax recoverable account"
              error={errors.tax_recoverable_account_id?.message ?? null}
              hint={
                lockedHint ??
                (taxed && !taxAccount ? "Needed to post, because this expense carries tax." : "An asset account for input tax.")
              }
            >
              <Controller
                control={control}
                name="tax_recoverable_account_id"
                render={({ field }) => (
                  <AccountPicker accountType="asset" value={field.value} onChange={field.onChange} disabled={isEdit} />
                )}
              />
            </FormField>
          </CardBody>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader
            title="Totals"
            description="Estimated while you type. The saved expense shows the tax and total calculated by the server."
          />
          <CardBody className="flex flex-col items-end gap-2">
            <TotalsPanel
              rows={[
                { label: "Amount", value: <Money value={estimate?.amount ?? null} currency={org.currency} /> },
                { label: "Tax", value: <Money value={estimate?.tax ?? null} currency={org.currency} /> },
                {
                  label: "Total (estimate)",
                  value: <Money value={estimate?.total ?? null} currency={org.currency} strong />,
                  emphasis: true,
                },
              ]}
            />
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader title="Billing and projects" />
        <CardBody className="grid gap-4 sm:grid-cols-2">
          <FormField
            label="Customer"
            error={errors.customer_id?.message ?? null}
            hint={lockedHint ?? "Who this cost can be re-billed to."}
          >
            <Controller
              control={control}
              name="customer_id"
              render={({ field }) => <CustomerPicker value={field.value} onChange={field.onChange} disabled={isEdit} />}
            />
          </FormField>

          <FormField label="Project" error={errors.project_id?.message ?? null} hint={lockedHint ?? "Counts toward project profitability."}>
            <Controller
              control={control}
              name="project_id"
              render={({ field }) => <ProjectPicker value={field.value} onChange={field.onChange} disabled={isEdit} />}
            />
          </FormField>

          <Checkbox
            label="Billable to the customer"
            hint={
              isEdit && !customer
                ? "This expense was saved without a customer, so it cannot be made billable."
                : "Requires a customer."
            }
            disabled={isEdit && !customer}
            {...register("is_billable")}
          />

          <FormField label="Notes" className="sm:col-span-2" error={errors.notes?.message ?? null}>
            <Textarea rows={3} {...register("notes")} />
          </FormField>
        </CardBody>
      </Card>

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
