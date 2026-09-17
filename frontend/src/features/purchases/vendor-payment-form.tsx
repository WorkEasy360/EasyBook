"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { FormError, FormField, Input, Select, Textarea } from "@/components/ui/field";
import { NumericInput } from "@/components/ui/numeric-input";
import { TotalsPanel } from "@/components/ui/detail";
import { Money } from "@/components/ui/money";
import { BILL_STATUS, PAYMENT_METHOD_LABELS, StatusBadge } from "@/components/ui/status-badge";
import { ErrorState } from "@/components/ui/states";
import { Spinner } from "@/components/ui/spinner";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { AccountPicker, VendorPicker } from "@/features/shared/pickers";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useIdempotentMutation, useList } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { formErrorOf, referenceOf } from "@/lib/api/errors";
import { compare, isValidDecimal, money, toDecimalString } from "@/lib/money";
import { formatDate, todayInZone } from "@/lib/datetime";
import { serverFieldErrors } from "./form-errors";
import { allocationSummary, isPositive } from "./quantities";
import type { Bill, PaymentMethod, Vendor, VendorPayment, VendorPaymentInput } from "@/types/api/purchases";

/**
 * Record a payment to a vendor and allocate it across their open bills.
 *
 * One atomic, append-only write (services/payments.py ::
 * record_vendor_payment): it posts the journal (Cr the source bank/cash
 * account, Dr each bill's payable account) and updates each bill's paid
 * status. There is no draft and no edit, so the request carries an
 * Idempotency-Key minted once per submission — a retried click replays the
 * first payment instead of paying twice.
 *
 * Only OPEN and PARTIALLY_PAID bills accept an allocation (bill_not_payable),
 * and never more than their balance due (over_allocation); the balance shown
 * per bill is the server's `amount_due`. Whatever is not allocated becomes a
 * vendor advance and needs an asset account to hold it
 * (vendor_advance_account_required). The split shown here is an estimate of
 * that; the payment page afterwards shows the allocations the server made.
 */

const DATE = /^\d{4}-\d{2}-\d{2}$/;
const METHODS = Object.keys(PAYMENT_METHOD_LABELS) as PaymentMethod[];

const schema = z.object({
  vendor_id: z.string().min(1, "Choose a vendor."),
  payment_date: z.string().regex(DATE, "Enter the payment date."),
  amount: z.string().refine((value) => isValidDecimal(value) && money(value).gt(0), "Enter an amount above zero."),
  payment_method: z.enum(METHODS as [PaymentMethod, ...PaymentMethod[]]),
  reference: z.string().max(255, "At most 255 characters."),
  source_account_id: z.string().min(1, "Choose the account the money was paid from."),
  vendor_advance_account_id: z.string().nullable(),
  notes: z.string().max(4000),
});

type Values = z.infer<typeof schema>;
const FIELDS = Object.keys(schema.shape);

export interface VendorPaymentFormInitial {
  vendor_id?: string;
  /** Pre-allocate to this bill (from a bill page). */
  bill?: { id: string; amount_due: string } | null;
}

export function VendorPaymentForm({ initial = {} }: { initial?: VendorPaymentFormInitial }) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const prefillBill = initial.bill ?? null;
  // Allocation inputs, keyed by bill id. Held outside the form values because
  // the set of bills changes with the vendor.
  const [allocations, setAllocations] = React.useState<Record<string, string>>(
    prefillBill ? { [prefillBill.id]: toDecimalString(prefillBill.amount_due) } : {},
  );
  const [allocationErrors, setAllocationErrors] = React.useState<Record<string, string>>({});

  const {
    control,
    register,
    handleSubmit,
    setError,
    formState: { errors, isDirty, isSubmitting },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      vendor_id: initial.vendor_id ?? "",
      payment_date: todayInZone(org.timeZone),
      amount: prefillBill ? toDecimalString(prefillBill.amount_due) : "",
      payment_method: "bank_transfer",
      reference: "",
      source_account_id: "",
      vendor_advance_account_id: null,
      notes: "",
    },
  });

  useUnsavedChanges(isDirty && !isSubmitting);

  const vendorId = useWatch({ control, name: "vendor_id" });
  const amount = useWatch({ control, name: "amount" });
  const advanceAccount = useWatch({ control, name: "vendor_advance_account_id" });

  // The list endpoint filters one status at a time, so the two payable
  // statuses are fetched side by side.
  const openBills = useList<Bill>("purchases/bills", { vendor: vendorId, status: "open", page_size: 200 }, { enabled: Boolean(vendorId) });
  const partialBills = useList<Bill>(
    "purchases/bills",
    { vendor: vendorId, status: "partially_paid", page_size: 200 },
    { enabled: Boolean(vendorId) },
  );
  const bills = React.useMemo(
    () =>
      [...(openBills.data?.results ?? []), ...(partialBills.data?.results ?? [])].sort((a, b) =>
        a.due_date === b.due_date ? a.bill_number.localeCompare(b.bill_number) : a.due_date < b.due_date ? -1 : 1,
      ),
    [openBills.data, partialBills.data],
  );
  const billsLoading = Boolean(vendorId) && (openBills.isLoading || partialBills.isLoading);
  const billsError = openBills.error ?? partialBills.error;

  const activeAllocations = bills.map((bill) => allocations[bill.id] ?? "");
  const summary = allocationSummary(amount, activeAllocations);

  function setAllocation(billId: string, value: string) {
    setAllocations((current) => ({ ...current, [billId]: value }));
    setAllocationErrors((current) => {
      if (!(billId in current)) return current;
      const next = { ...current };
      delete next[billId];
      return next;
    });
  }

  function onVendorChanged(next: Vendor | null) {
    // Allocations belong to the previous vendor's bills; start clean.
    if (next?.id !== vendorId) {
      setAllocations({});
      setAllocationErrors({});
    }
  }

  const mutation = useIdempotentMutation<VendorPayment, VendorPaymentInput>(
    "purchases/payments",
    (input, idempotencyKey) => api.post<VendorPayment>("purchases/payments", input, { idempotencyKey }),
    {
      // "purchases/payments" cascades to bills in query-keys.ts, so their
      // cached balances expire too.
      onSuccess: (payment) => {
        toast.push({ tone: "success", title: "Payment recorded", description: payment.payment_number });
        router.push(`/purchases/payments/${payment.id}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        const pairs = serverFieldErrors(error, FIELDS, {
          vendor_advance_account_required: "vendor_advance_account_id",
          payment_amount_invalid: "amount",
          allocation_exceeds_payment: "amount",
        });
        for (const [field, message] of pairs) {
          setError(field as keyof Values, { type: "server", message });
        }
      },
    },
  );

  function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });

    const problems: Record<string, string> = {};
    const payload: Array<{ bill_id: string; amount: string }> = [];
    for (const bill of bills) {
      const raw = (allocations[bill.id] ?? "").trim();
      if (raw === "") continue;
      if (!isValidDecimal(raw) || money(raw).lt(0)) {
        problems[bill.id] = "Enter an amount of zero or more.";
        continue;
      }
      if (!isPositive(raw)) continue;
      // over_allocation, against the server's own balance for the bill.
      if (compare(raw, toDecimalString(bill.amount_due)) > 0) {
        problems[bill.id] = "More than the balance due.";
        continue;
      }
      payload.push({ bill_id: bill.id, amount: raw });
    }
    setAllocationErrors(problems);
    if (Object.keys(problems).length > 0) return;

    const check = allocationSummary(values.amount, payload.map((entry) => entry.amount));
    if (check.overAllocated) {
      setError("amount", { type: "client", message: "The allocations add up to more than the payment." });
      return;
    }
    if (check.needsAdvanceAccount && !values.vendor_advance_account_id) {
      setError("vendor_advance_account_id", {
        type: "client",
        message: "Choose where to hold the unallocated amount, or allocate all of it to bills.",
      });
      return;
    }

    mutation.mutate({
      vendor_id: values.vendor_id,
      payment_date: values.payment_date,
      amount: values.amount,
      payment_method: values.payment_method,
      reference: values.reference,
      source_account_id: values.source_account_id,
      // Only sent when there is a remainder for it to hold.
      vendor_advance_account_id: check.needsAdvanceAccount ? values.vendor_advance_account_id : null,
      notes: values.notes,
      allocations: payload,
    });
  }

  return (
    <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
      <FormError message={formError.message} reference={formError.reference} />

      <Card>
        <CardHeader title="Payment" />
        <CardBody className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <FormField label="Vendor" required error={errors.vendor_id?.message ?? null} className="sm:col-span-2 lg:col-span-1">
            <Controller
              control={control}
              name="vendor_id"
              render={({ field }) => (
                <VendorPicker
                  value={field.value || null}
                  onChange={(value) => field.onChange(value ?? "")}
                  onSelectVendor={onVendorChanged}
                />
              )}
            />
          </FormField>

          <FormField label="Payment date" required error={errors.payment_date?.message ?? null} hint="The posting date.">
            <Input type="date" {...register("payment_date")} />
          </FormField>

          <FormField label="Amount paid" required error={errors.amount?.message ?? null}>
            <Controller
              control={control}
              name="amount"
              render={({ field }) => (
                <NumericInput nonNegative value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
              )}
            />
          </FormField>

          <FormField
            label="Paid from"
            required
            error={errors.source_account_id?.message ?? null}
            hint="The bank or cash account the money left. An asset account."
          >
            <Controller
              control={control}
              name="source_account_id"
              render={({ field }) => (
                <AccountPicker accountType="asset" value={field.value || null} onChange={(value) => field.onChange(value ?? "")} />
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
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="Apply to bills"
          description="Open and partially paid bills for this vendor, oldest due first. Balances are the server's."
        />
        <CardBody className="flex flex-col gap-3">
          {!vendorId ? (
            <p className="text-sm text-ink-500">Choose a vendor to see their unpaid bills.</p>
          ) : billsLoading ? (
            <p className="flex items-center gap-2 text-sm text-ink-500">
              <Spinner className="size-4" /> Loading unpaid bills…
            </p>
          ) : billsError ? (
            <ErrorState compact title="Could not load this vendor's bills" message={billsError.message} reference={referenceOf(billsError)} />
          ) : bills.length === 0 ? (
            <p className="text-sm text-ink-500">This vendor has no unpaid bills. The whole payment will be held as a vendor advance.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">Unpaid bills to allocate this payment to</caption>
                <thead>
                  <tr className="border-b border-ink-200 text-left text-2xs font-semibold tracking-wide text-ink-600 uppercase">
                    <th scope="col" className="px-2 py-2">
                      Bill
                    </th>
                    <th scope="col" className="hidden px-2 py-2 sm:table-cell">
                      Due
                    </th>
                    <th scope="col" className="hidden px-2 py-2 text-right md:table-cell">
                      Total
                    </th>
                    <th scope="col" className="px-2 py-2 text-right">
                      Balance due
                    </th>
                    <th scope="col" className="w-44 px-2 py-2 text-right">
                      Apply
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {bills.map((bill) => {
                    const label = bill.bill_number || "Bill";
                    return (
                      <tr key={bill.id} className="border-b border-ink-100 align-top">
                        <td className="px-2 py-2">
                          <span className="tabular font-medium text-ink-900">{label}</span>
                          <span className="ml-2 align-middle">
                            <StatusBadge status={bill.status} map={BILL_STATUS} size="sm" />
                          </span>
                          {bill.vendor_bill_number ? (
                            <span className="block text-xs text-ink-500">Vendor ref. {bill.vendor_bill_number}</span>
                          ) : null}
                        </td>
                        <td className="hidden px-2 py-2 tabular whitespace-nowrap sm:table-cell">{formatDate(bill.due_date)}</td>
                        <td className="hidden px-2 py-2 text-right md:table-cell">
                          <Money value={bill.total} currency={bill.currency} />
                        </td>
                        <td className="px-2 py-2 text-right">
                          <Money value={bill.amount_due} currency={bill.currency} strong />
                        </td>
                        <td className="px-2 py-2">
                          <FormField label={`Apply to ${label}`} labelHidden error={allocationErrors[bill.id] ?? null}>
                            <NumericInput
                              nonNegative
                              value={allocations[bill.id] ?? ""}
                              onValueChange={(value) => setAllocation(bill.id, value)}
                            />
                          </FormField>
                          <Button
                            variant="link"
                            size="sm"
                            onClick={() => setAllocation(bill.id, toDecimalString(bill.amount_due))}
                          >
                            Pay in full<span className="sr-only"> {label}</span>
                          </Button>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </CardBody>
      </Card>

      <div className="grid gap-4 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardHeader title="Advance and notes" />
          <CardBody className="flex flex-col gap-4">
            <FormField
              label="Vendor advance account"
              required={summary.needsAdvanceAccount}
              error={errors.vendor_advance_account_id?.message ?? null}
              hint={
                summary.needsAdvanceAccount
                  ? "Required: the unallocated part of this payment is held here as an advance. An asset account."
                  : "Only used when part of the payment is not applied to bills."
              }
            >
              <Controller
                control={control}
                name="vendor_advance_account_id"
                render={({ field }) => <AccountPicker accountType="asset" value={field.value} onChange={field.onChange} />}
              />
            </FormField>
            <FormField label="Notes" error={errors.notes?.message ?? null}>
              <Textarea rows={3} {...register("notes")} />
            </FormField>
          </CardBody>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader
            title="Split"
            description="Estimated from what you entered. The payment record shows the allocations the server made."
          />
          <CardBody className="flex flex-col items-end gap-2">
            <TotalsPanel
              rows={[
                { label: "Payment", value: <Money value={isValidDecimal(amount) ? toDecimalString(amount) : null} currency={org.currency} /> },
                { label: "Applied to bills", value: <Money value={summary.allocated} currency={org.currency} /> },
                {
                  label: summary.overAllocated ? "Over-allocated" : "Vendor advance (estimate)",
                  value: <Money value={summary.remainder} currency={org.currency} strong />,
                  emphasis: true,
                },
              ]}
            />
            {summary.overAllocated ? (
              <p role="alert" className="text-xs text-danger-600">
                The allocations add up to more than the payment.
              </p>
            ) : summary.needsAdvanceAccount && !advanceAccount ? (
              <p className="text-xs text-ink-500">Choose a vendor advance account, or apply the rest to bills.</p>
            ) : null}
          </CardBody>
        </Card>
      </div>

      <div className="flex items-center justify-end gap-2">
        <Button variant="secondary" onClick={() => router.back()} disabled={mutation.isPending}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={mutation.isPending} loadingLabel="Recording payment">
          Record payment
        </Button>
      </div>
    </form>
  );
}
