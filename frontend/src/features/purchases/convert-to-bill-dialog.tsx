"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormError, FormField, Input } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { AccountPicker } from "@/features/shared/pickers";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { formErrorOf, referenceOf } from "@/lib/api/errors";
import { addDays, todayInZone } from "@/lib/datetime";
import { serverFieldErrors } from "./form-errors";
import type { Bill, BillFromGoodsReceiptInput } from "@/types/api/purchases";

/**
 * "Convert to bill" for a RECEIVED goods receipt
 * (POST goods-receipts/{id}/convert-to-bill/, BillFromGoodsReceiptSerializer).
 *
 * The backend builds a DRAFT bill whose every line links back to the receipt
 * line it bills, priced from the purchase order line where there is one, else
 * from the receipt's recorded cost. Because the lines are linked, posting
 * that bill does not receive the stock a second time. Only the unbilled
 * remainder of each receipt line is included (goods_receipt_fully_billed when
 * nothing is left). Tax is NOT inferred — the draft comes back at 0% for the
 * user to correct against the vendor's invoice before posting.
 *
 * It creates a document, so the request carries an idempotency key minted
 * once per submission; a retried click replays instead of making two bills.
 */

const DATE = /^\d{4}-\d{2}-\d{2}$/;

const schema = z
  .object({
    bill_date: z.string().regex(DATE, "Enter the bill date."),
    due_date: z.string().regex(DATE, "Enter the due date."),
    payable_account_id: z.string().min(1, "Choose the payable account."),
    tax_recoverable_account_id: z.string().nullable(),
    price_variance_account_id: z.string().nullable(),
    vendor_bill_number: z.string().max(64, "At most 64 characters."),
  })
  .refine((values) => values.due_date >= values.bill_date, {
    message: "The due date cannot be before the bill date.",
    path: ["due_date"],
  });

type Values = z.infer<typeof schema>;
const FIELDS = Object.keys(schema.shape);

export function ConvertToBillButton({
  receiptId,
  receiptNumber,
  defaultPayableAccountId,
  paymentTermsDays,
}: {
  receiptId: string;
  receiptNumber: string;
  /** The vendor's default payable account, if it has one. */
  defaultPayableAccountId: string | null;
  paymentTermsDays: number | null;
}) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const formId = React.useId();
  const [open, setOpen] = React.useState(false);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const today = todayInZone(org.timeZone);
  const {
    control,
    register,
    handleSubmit,
    setError,
    formState: { errors },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      bill_date: today,
      due_date: (paymentTermsDays ? addDays(today, paymentTermsDays) : null) ?? today,
      payable_account_id: defaultPayableAccountId ?? "",
      tax_recoverable_account_id: null,
      price_variance_account_id: null,
      vendor_bill_number: "",
    },
  });

  const mutation = useIdempotentMutation<Bill, BillFromGoodsReceiptInput>(
    "purchases/bills",
    (input, idempotencyKey) =>
      api.post<Bill>(`purchases/goods-receipts/${receiptId}/convert-to-bill`, input, { idempotencyKey }),
    {
      onSuccess: (bill) => {
        setOpen(false);
        toast.push({ tone: "success", title: "Draft bill created", description: `From receipt ${receiptNumber}` });
        router.push(`/purchases/bills/${bill.id}`);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        const pairs = serverFieldErrors(error, FIELDS, {
          duplicate_vendor_bill_number: "vendor_bill_number",
          invalid_account_type: "payable_account_id",
        });
        for (const [field, message] of pairs) {
          setError(field as keyof Values, { type: "server", message });
        }
      },
    },
  );

  function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });
    mutation.mutate({
      bill_date: values.bill_date,
      due_date: values.due_date,
      payable_account_id: values.payable_account_id,
      tax_recoverable_account_id: values.tax_recoverable_account_id,
      price_variance_account_id: values.price_variance_account_id,
      vendor_bill_number: values.vendor_bill_number.trim(),
    });
  }

  function close() {
    if (mutation.isPending) return;
    setOpen(false);
  }

  return (
    <>
      <Button
        variant="primary"
        onClick={() => {
          setFormError({ message: null, reference: null });
          setOpen(true);
        }}
      >
        Convert to bill
      </Button>

      <Dialog
        open={open}
        onClose={close}
        title={`Bill receipt ${receiptNumber}`}
        description="Creates a draft bill for everything on this receipt not yet billed. Nothing is posted until you post the bill."
        size="lg"
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={close} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={mutation.isPending} loadingLabel="Creating bill">
              Create draft bill
            </Button>
          </>
        }
      >
        <form id={formId} method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
          <FormError message={formError.message} reference={formError.reference} />
          <p className="text-xs text-ink-600">
            Lines are priced from the purchase order where there is one, otherwise from the cost on the receipt. Tax
            starts at 0%: check each line against the vendor&apos;s invoice on the draft before posting.
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            <FormField label="Bill date" required error={errors.bill_date?.message ?? null}>
              <Input type="date" {...register("bill_date")} />
            </FormField>
            <FormField
              label="Due date"
              required
              error={errors.due_date?.message ?? null}
              {...(paymentTermsDays ? { hint: `Vendor terms: net ${paymentTermsDays} days.` } : {})}
            >
              <Input type="date" {...register("due_date")} />
            </FormField>
            <FormField
              label="Vendor bill number"
              error={errors.vendor_bill_number?.message ?? null}
              hint="The number on the vendor's invoice. Must be unique per vendor."
            >
              <Input {...register("vendor_bill_number")} />
            </FormField>
            <FormField
              label="Payable account"
              required
              error={errors.payable_account_id?.message ?? null}
              hint={defaultPayableAccountId ? "The vendor's default. A liability account." : "A liability account."}
            >
              <Controller
                control={control}
                name="payable_account_id"
                render={({ field }) => (
                  <AccountPicker
                    accountType="liability"
                    value={field.value || null}
                    onChange={(value) => field.onChange(value ?? "")}
                  />
                )}
              />
            </FormField>
            <FormField
              label="Tax recoverable account"
              error={errors.tax_recoverable_account_id?.message ?? null}
              hint="An asset account for input tax. Needed to post once any line carries tax."
            >
              <Controller
                control={control}
                name="tax_recoverable_account_id"
                render={({ field }) => <AccountPicker accountType="asset" value={field.value} onChange={field.onChange} />}
              />
            </FormField>
            <FormField
              label="Price variance account"
              error={errors.price_variance_account_id?.message ?? null}
              hint="An expense account. Needed to post only if a billed price differs from the cost the receipt recorded."
            >
              <Controller
                control={control}
                name="price_variance_account_id"
                render={({ field }) => <AccountPicker accountType="expense" value={field.value} onChange={field.onChange} />}
              />
            </FormField>
          </div>
        </form>
      </Dialog>
    </>
  );
}
