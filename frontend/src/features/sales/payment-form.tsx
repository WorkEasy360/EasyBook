"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Controller, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { FormError, FormField, Input, Select, Textarea } from "@/components/ui/field";
import { NumericInput } from "@/components/ui/numeric-input";
import { Money } from "@/components/ui/money";
import { TotalsPanel } from "@/components/ui/detail";
import { ErrorState } from "@/components/ui/states";
import { PAYMENT_METHOD_LABELS } from "@/components/ui/status-badge";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { AccountPicker, CustomerPicker } from "@/features/shared/pickers";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useIdempotentMutation, useList } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { formatDate, todayInZone } from "@/lib/datetime";
import { isValidDecimal, money } from "@/lib/money";
import { allocationProblem, allocationsInput, estimateAllocation } from "./payment-allocation";
import type { CustomerPayment, CustomerPaymentInput, Invoice, PaymentMethod } from "@/types/api/sales";

/**
 * Record a customer payment and apply it to that customer's open invoices.
 *
 * Recording is immediate and final: sales/services/payments.py ::
 * record_payment posts the journal (Dr destination account, Cr each invoice's
 * receivable, Cr unapplied credit for any remainder) and updates the
 * invoices' paid status in one transaction. There is no draft and no update
 * endpoint, so the submission carries an Idempotency-Key — the view replays
 * the stored response rather than recording the payment twice.
 *
 * Only SENT and PARTIALLY_PAID invoices accept an allocation
 * (invoice_not_payable), so only those are listed. Each row's balance due is
 * the server's `amount_due`; the allocated/unapplied split shown beside the
 * form is an estimate, and the backend re-validates over_allocation and
 * allocation_exceeds_payment under a row lock.
 */

const METHODS = Object.keys(PAYMENT_METHOD_LABELS) as [PaymentMethod, ...PaymentMethod[]];

// The allocation rules depend on which invoices are listed (only those are
// ever sent), which the schema cannot see — so they are checked in onSubmit.
const schema = z.object({
  customer_id: z.string().min(1, "Choose a customer."),
  payment_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Enter the payment date."),
  amount: z.string().refine((value) => isValidDecimal(value) && money(value).gt(0), "Enter an amount above zero."),
  payment_method: z.enum(METHODS),
  reference: z.string().max(100),
  destination_account_id: z.string().min(1, "Choose the account the money was received into."),
  unapplied_credit_account_id: z.string().nullable(),
  notes: z.string().max(4000),
  allocations: z.record(z.string(), z.string()),
});

type Values = z.infer<typeof schema>;

export interface PaymentFormInitial {
  customer_id?: string;
  amount?: string;
  allocations?: Record<string, string>;
  destination_account_id?: string | null;
  /** Payment number the destination account was taken from, for the hint. */
  rememberedFrom?: string | null;
}

const OPEN_STATUSES = ["sent", "partially_paid"] as const;

export function PaymentForm({ initial = {} }: { initial?: PaymentFormInitial }) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
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
    defaultValues: {
      customer_id: initial.customer_id ?? "",
      payment_date: todayInZone(org.timeZone),
      amount: initial.amount ?? "",
      payment_method: "bank_transfer",
      reference: "",
      destination_account_id: initial.destination_account_id ?? "",
      unapplied_credit_account_id: null,
      notes: "",
      allocations: initial.allocations ?? {},
    },
  });

  useUnsavedChanges(isDirty && !isSubmitting);

  const customerId = useWatch({ control, name: "customer_id" });
  const amount = useWatch({ control, name: "amount" });
  const allocations = useWatch({ control, name: "allocations" });
  const unappliedAccount = useWatch({ control, name: "unapplied_credit_account_id" });

  // The list endpoint filters one status at a time, so the two payable
  // statuses are fetched separately. 200 is the API's page-size ceiling.
  const sent = useList<Invoice>(
    "sales/invoices",
    { customer: customerId, status: OPEN_STATUSES[0], page_size: 200 },
    { enabled: Boolean(customerId) },
  );
  const partial = useList<Invoice>(
    "sales/invoices",
    { customer: customerId, status: OPEN_STATUSES[1], page_size: 200 },
    { enabled: Boolean(customerId) },
  );
  const invoicesError = sent.error ?? partial.error;
  const invoicesLoading = Boolean(customerId) && (sent.isLoading || partial.isLoading);
  const openInvoices = [...(sent.data?.results ?? []), ...(partial.data?.results ?? [])].sort((a, b) =>
    a.due_date === b.due_date ? a.invoice_number.localeCompare(b.invoice_number) : a.due_date < b.due_date ? -1 : 1,
  );
  const truncated = (sent.data?.next ?? null) !== null || (partial.data?.next ?? null) !== null;

  // Allocations to invoices not listed (a prefilled invoice that has since
  // been paid, or rows left from another customer) are never sent.
  const listedIds = new Set(openInvoices.map((invoice) => invoice.id));
  const visibleAllocations = Object.fromEntries(
    Object.entries(allocations ?? {}).filter(([invoiceId]) => listedIds.has(invoiceId)),
  );
  const estimate = estimateAllocation(amount ?? "", visibleAllocations);
  const prefilledMissing =
    !invoicesLoading &&
    !invoicesError &&
    Object.keys(initial.allocations ?? {}).some((invoiceId) => customerId === initial.customer_id && !listedIds.has(invoiceId));

  const mutation = useIdempotentMutation<CustomerPayment, CustomerPaymentInput>(
    "sales/payments",
    (input, idempotencyKey) => api.post<CustomerPayment>("sales/payments", input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: `Payment ${saved.payment_number} recorded` });
        router.push(`/sales/payments/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        for (const [field, messages] of Object.entries(fieldErrorsOf(error))) {
          if (field !== "allocations" && field in schema.shape && messages[0]) {
            setError(field as keyof Values, { type: "server", message: messages[0] });
          }
        }
      },
    },
  );

  function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });
    const rows = Object.fromEntries(Object.entries(values.allocations).filter(([invoiceId]) => listedIds.has(invoiceId)));
    const problem = openInvoices.find((invoice) => allocationProblem(rows[invoice.id], invoice.amount_due));
    if (problem) {
      setFormError({ message: `Check the amount applied to ${problem.invoice_number}.`, reference: null });
      return;
    }
    const split = estimateAllocation(values.amount, rows);
    if (split.exceedsPayment) {
      setFormError({ message: "The amounts applied add up to more than the payment.", reference: null });
      return;
    }
    if (split.needsUnappliedAccount && !values.unapplied_credit_account_id) {
      setError("unapplied_credit_account_id", {
        type: "validate",
        message: "Choose where the unapplied remainder is held.",
      });
      return;
    }
    mutation.mutate({
      customer_id: values.customer_id,
      currency: org.currency,
      payment_date: values.payment_date,
      amount: values.amount,
      payment_method: values.payment_method,
      reference: values.reference,
      destination_account_id: values.destination_account_id,
      unapplied_credit_account_id: split.needsUnappliedAccount ? values.unapplied_credit_account_id : null,
      notes: values.notes,
      allocations: allocationsInput(rows),
    });
  }

  function setAllocation(invoiceId: string, value: string) {
    setValue("allocations", { ...(allocations ?? {}), [invoiceId]: value }, { shouldDirty: true, shouldValidate: false });
  }

  return (
    <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
      <FormError message={formError.message} reference={formError.reference} />

      <Card>
        <CardHeader title="Payment" />
        <CardBody className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <FormField label="Customer" required error={errors.customer_id?.message ?? null} className="sm:col-span-2 lg:col-span-1">
            <Controller
              control={control}
              name="customer_id"
              render={({ field }) => (
                <CustomerPicker
                  value={field.value || null}
                  onChange={(value) => {
                    field.onChange(value ?? "");
                    // Allocations belong to one customer's invoices.
                    setValue("allocations", {}, { shouldDirty: true });
                  }}
                />
              )}
            />
          </FormField>

          <FormField label="Payment date" required error={errors.payment_date?.message ?? null} hint="The journal is posted on this date.">
            <Input type="date" {...register("payment_date")} />
          </FormField>

          <FormField label="Amount received" required error={errors.amount?.message ?? null}>
            <Controller
              control={control}
              name="amount"
              render={({ field }) => (
                <NumericInput nonNegative value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
              )}
            />
          </FormField>

          <FormField label="Method" required error={errors.payment_method?.message ?? null}>
            <Select {...register("payment_method")}>
              {METHODS.map((method) => (
                <option key={method} value={method}>
                  {PAYMENT_METHOD_LABELS[method]}
                </option>
              ))}
            </Select>
          </FormField>

          <FormField label="Reference" error={errors.reference?.message ?? null} hint="Cheque number, UTR or transaction id.">
            <Input {...register("reference")} />
          </FormField>

          <FormField
            label="Received into"
            required
            error={errors.destination_account_id?.message ?? null}
            hint={
              initial.rememberedFrom
                ? `Taken from payment ${initial.rememberedFrom}. An asset account — bank or cash.`
                : "An asset account — bank or cash. It is debited with the full amount."
            }
          >
            <Controller
              control={control}
              name="destination_account_id"
              render={({ field }) => (
                <AccountPicker accountType="asset" value={field.value || null} onChange={(value) => field.onChange(value ?? "")} />
              )}
            />
          </FormField>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Apply to invoices"
          description="Posted invoices of this customer with a balance still due. Leave an amount blank to skip an invoice."
        />
        <CardBody className="flex flex-col gap-3">
          {prefilledMissing ? (
            <p role="status" className="text-xs text-warning-700">
              The invoice this payment was opened from no longer has a balance due, so it is not listed.
            </p>
          ) : null}

          {!customerId ? (
            <p className="text-sm text-ink-500">Choose a customer to see their open invoices.</p>
          ) : invoicesError ? (
            <ErrorState
              compact
              title="Could not load open invoices"
              message={invoicesError.message}
              reference={referenceOf(invoicesError)}
            />
          ) : invoicesLoading ? (
            <p className="text-sm text-ink-500" role="status">
              Loading open invoices…
            </p>
          ) : openInvoices.length === 0 ? (
            <p className="text-sm text-ink-500">
              This customer has no open invoices. The whole amount will be held as unapplied credit.
            </p>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-ink-200 bg-white">
              <table className="w-full min-w-max border-collapse text-sm">
                <caption className="sr-only">Open invoices to apply this payment to</caption>
                <thead>
                  <tr className="border-b border-ink-200 bg-ink-50 text-2xs font-semibold tracking-wide text-ink-600 uppercase">
                    <th scope="col" className="px-3 py-2 text-left">Invoice</th>
                    <th scope="col" className="hidden px-3 py-2 text-left sm:table-cell">Due</th>
                    <th scope="col" className="hidden px-3 py-2 text-right md:table-cell">Total</th>
                    <th scope="col" className="px-3 py-2 text-right">Balance due</th>
                    <th scope="col" className="px-3 py-2 text-right">Apply</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-ink-100">
                  {openInvoices.map((invoice) => {
                    const value = allocations?.[invoice.id] ?? "";
                    const problem = allocationProblem(value, invoice.amount_due);
                    return (
                      <tr key={invoice.id}>
                        <td className="px-3 py-2.5 align-top">
                          <Link href={`/sales/invoices/${invoice.id}`} className="tabular font-medium text-brand-700 hover:underline">
                            {invoice.invoice_number}
                          </Link>
                          <p className="text-xs text-ink-500">{formatDate(invoice.invoice_date)}</p>
                        </td>
                        <td className="hidden px-3 py-2.5 align-top whitespace-nowrap sm:table-cell">{formatDate(invoice.due_date)}</td>
                        <td className="numeric hidden px-3 py-2.5 text-right align-top md:table-cell">
                          <Money value={invoice.total} currency={invoice.currency} />
                        </td>
                        <td className="numeric px-3 py-2.5 text-right align-top">
                          <Money value={invoice.amount_due} currency={invoice.currency} strong />
                        </td>
                        <td className="w-48 px-3 py-2 align-top">
                          <FormField label={`Apply to ${invoice.invoice_number}`} labelHidden error={problem}>
                            <NumericInput
                              nonNegative
                              value={value}
                              onValueChange={(next) => setAllocation(invoice.id, next)}
                            />
                          </FormField>
                          <Button
                            variant="link"
                            size="sm"
                            onClick={() => setAllocation(invoice.id, invoice.amount_due)}
                          >
                            Apply full balance<span className="sr-only"> of {invoice.invoice_number}</span>
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {truncated ? (
            <p className="text-xs text-ink-500">Showing the first 200 open invoices in each status.</p>
          ) : null}
        </CardBody>
      </Card>

      <div className="grid gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader title="Unapplied credit and notes" />
          <CardBody className="flex flex-col gap-4">
            <FormField
              label="Unapplied credit account"
              required={estimate.needsUnappliedAccount}
              error={errors.unapplied_credit_account_id?.message ?? null}
              hint={
                estimate.needsUnappliedAccount
                  ? "Required: part of this payment is not applied to an invoice. That remainder is credited to this liability account as customer credit."
                  : "Only used when the payment is more than the amounts applied."
              }
            >
              <Controller
                control={control}
                name="unapplied_credit_account_id"
                render={({ field }) => (
                  <AccountPicker accountType="liability" value={field.value} onChange={field.onChange} />
                )}
              />
            </FormField>
            <FormField label="Notes" error={errors.notes?.message ?? null}>
              <Textarea rows={3} {...register("notes")} />
            </FormField>
          </CardBody>
        </Card>
        <Card className="lg:col-span-2">
          <CardHeader
            title="Allocation"
            description="Estimated while you type. The recorded payment shows the allocations the server accepted."
          />
          <CardBody className="flex flex-col items-end gap-2">
            <TotalsPanel
              rows={[
                {
                  label: "Amount received",
                  value: isValidDecimal(amount ?? "") ? <Money value={amount} currency={org.currency} /> : <span className="text-ink-400">—</span>,
                },
                { label: "Applied to invoices (estimate)", value: <Money value={estimate.allocated} currency={org.currency} /> },
                {
                  label: "Unapplied credit (estimate)",
                  value:
                    estimate.unapplied === null ? (
                      <span className="text-ink-400">—</span>
                    ) : (
                      <Money value={estimate.unapplied} currency={org.currency} strong />
                    ),
                  emphasis: true,
                },
              ]}
            />
            {estimate.exceedsPayment ? (
              <p className="text-xs text-danger-600">Applied amounts exceed the payment.</p>
            ) : estimate.needsUnappliedAccount && !unappliedAccount ? (
              <p className="text-xs text-warning-700">Choose an unapplied credit account for the remainder.</p>
            ) : null}
          </CardBody>
        </Card>
      </div>

      <div className="flex items-center justify-end gap-2">
        <Button variant="secondary" onClick={() => router.back()} disabled={mutation.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={mutation.isPending} loadingLabel="Recording">
          Record payment
        </Button>
      </div>
    </form>
  );
}
