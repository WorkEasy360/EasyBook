"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Controller, FormProvider, useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Checkbox, FormError, FormField, Input, Select, Textarea } from "@/components/ui/field";
import { NumericInput } from "@/components/ui/numeric-input";
import { Money } from "@/components/ui/money";
import { CREDIT_NOTE_REASON_LABELS } from "@/components/ui/status-badge";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { AccountPicker, CustomerPicker, WarehousePicker } from "@/features/shared/pickers";
import {
  EMPTY_PRICED_LINE,
  EstimatedTotals,
  PricedLinesEditor,
  fromPricedLine,
  pricedLineSchema,
  toPricedLineInput,
} from "@/features/documents/priced-lines-editor";
import { estimateTotals } from "@/features/documents/estimate";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useIdempotentMutation, useLookup } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { isValidDecimal, money } from "@/lib/money";
import { todayInZone } from "@/lib/datetime";
import type { DecimalString } from "@/lib/api/types";
import type { Item } from "@/types/api/items";
import type {
  CreditNote,
  CreditNoteInput,
  CreditNoteReason,
  CreditNoteUpdateInput,
} from "@/types/api/sales";

/**
 * Create or edit a DRAFT credit note.
 *
 * Saving posts nothing. Issuing (a confirmed action on the credit-note page)
 * posts one journal — revenue and output tax reversed, the source invoice's
 * remaining balance credited first, any excess to unapplied customer credit —
 * and returns restocked lines to inventory (sales/services/credit_notes.py ::
 * issue_credit_note).
 *
 * Rules mirrored here so the draft can actually be issued later — the
 * accounts and warehouse CANNOT be changed after the draft is saved
 * (CreditNoteDetailView.update accepts only date, reference, notes, lines):
 *  - a restocked line must be a tracked product with a unit cost
 *    (service_cannot_restock, item_not_tracked, restock_cost_required), and
 *    the note needs a warehouse to receive it (warehouse_required);
 *  - the part of the credit NOT absorbed by the source invoice's balance due
 *    goes to an unapplied credit account (unapplied_credit_account_required).
 *    Without a source invoice that is the whole credit.
 */

const REASONS = Object.keys(CREDIT_NOTE_REASON_LABELS) as [CreditNoteReason, ...CreditNoteReason[]];

const lineSchema = pricedLineSchema
  .extend({
    restock: z.boolean(),
    unit_cost: z.string(),
    source_invoice_line_id: z.string().nullable(),
  })
  .refine((line) => !line.restock || (isValidDecimal(line.unit_cost) && money(line.unit_cost).gte(0)), {
    message: "A restocked line needs the unit cost it returns to stock at.",
    path: ["unit_cost"],
  });

const schema = z
  .object({
    customer_id: z.string().min(1, "Choose a customer."),
    credit_note_date: z.string().regex(/^\d{4}-\d{2}-\d{2}$/, "Enter the credit note date."),
    reason: z.enum(REASONS),
    reference: z.string().max(100),
    receivable_account_id: z.string().nullable(),
    tax_payable_account_id: z.string().nullable(),
    unapplied_credit_account_id: z.string().nullable(),
    warehouse_id: z.string().nullable(),
    notes: z.string().max(4000),
    lines: z.array(lineSchema).min(1, "Add at least one line."),
  })
  .refine((values) => !values.lines.some((line) => line.restock) || Boolean(values.warehouse_id), {
    message: "Choose the warehouse restocked goods return to.",
    path: ["warehouse_id"],
  });

type Values = z.infer<typeof schema>;
type LineValues = Values["lines"][number];

const EMPTY_LINE: LineValues = { ...EMPTY_PRICED_LINE, restock: false, unit_cost: "", source_invoice_line_id: null };

export interface CreditNoteFormInitial {
  customer_id?: string;
  source_invoice_id?: string | null;
  receivable_account_id?: string | null;
  tax_payable_account_id?: string | null;
  warehouse_id?: string | null;
  lines?: LineValues[];
}

export function CreditNoteForm({
  creditNote,
  initial = {},
  sourceInvoice,
}: {
  creditNote?: CreditNote;
  initial?: CreditNoteFormInitial;
  /** The invoice being credited, as the server last reported it. */
  sourceInvoice?: { number: string; amountDue: DecimalString; currency: string } | null;
}) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const isEdit = Boolean(creditNote);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const sourceInvoiceId = creditNote ? creditNote.source_invoice : (initial.source_invoice_id ?? null);

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: creditNote
      ? {
          customer_id: creditNote.customer,
          credit_note_date: creditNote.credit_note_date,
          reason: creditNote.reason,
          reference: creditNote.reference,
          receivable_account_id: creditNote.receivable_account,
          tax_payable_account_id: creditNote.tax_payable_account,
          unapplied_credit_account_id: creditNote.unapplied_credit_account,
          warehouse_id: creditNote.warehouse,
          notes: creditNote.notes,
          lines: creditNote.lines.map((line) => ({
            ...fromPricedLine(line),
            restock: line.restock,
            unit_cost: line.unit_cost ?? "",
            source_invoice_line_id: line.source_invoice_line,
          })),
        }
      : {
          customer_id: initial.customer_id ?? "",
          credit_note_date: todayInZone(org.timeZone),
          reason: sourceInvoiceId ? "return" : "other",
          reference: "",
          receivable_account_id: initial.receivable_account_id ?? null,
          tax_payable_account_id: initial.tax_payable_account_id ?? null,
          unapplied_credit_account_id: null,
          warehouse_id: initial.warehouse_id ?? null,
          notes: "",
          lines: initial.lines?.length ? initial.lines : [{ ...EMPTY_LINE }],
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

  const lines = useWatch({ control, name: "lines" });
  const unappliedAccount = useWatch({ control, name: "unapplied_credit_account_id" });

  // Same query key as ItemPicker's, so this reads the picker's cached page
  // rather than fetching again. Used only to offer "restock" where the
  // backend would accept it.
  const items = useLookup<Item>("items", "items", "", { extraParams: { page_size: 200, is_active: "true" } });
  const itemById = new Map((items.data?.results ?? []).map((item) => [item.id, item]));

  // Estimate of the credit the source invoice cannot absorb. Server figures
  // decide on issue; this only decides whether to ask for the account now,
  // while it can still be set.
  const estimate = estimateTotals(lines ?? []);
  const needsUnapplied = sourceInvoice
    ? money(estimate.total).gt(money(sourceInvoice.amountDue))
    : !sourceInvoiceId && money(estimate.total).gt(0);

  const mutation = useIdempotentMutation<CreditNote, CreditNoteInput | CreditNoteUpdateInput>(
    "sales/credit-notes",
    (input, idempotencyKey) =>
      creditNote
        ? api.patch<CreditNote>(`sales/credit-notes/${creditNote.id}`, input)
        : api.post<CreditNote>("sales/credit-notes", input, { idempotencyKey }),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: isEdit ? "Draft credit note saved" : "Draft credit note created" });
        router.push(`/sales/credit-notes/${saved.id}`);
        router.refresh();
      },
      onError: (error) => {
        const fieldErrors = fieldErrorsOf(error);
        const lineMessages = fieldErrors["lines"];
        setFormError({
          message: lineMessages?.length ? `Lines: ${lineMessages.join(" ")}` : formErrorOf(error),
          reference: referenceOf(error),
        });
        for (const [field, messages] of Object.entries(fieldErrors)) {
          if (field !== "lines" && field in schema.shape && messages[0]) {
            setError(field as keyof Values, { type: "server", message: messages[0] });
          }
        }
      },
    },
  );

  function onSubmit(values: Values) {
    setFormError({ message: null, reference: null });
    const lineInputs = values.lines.map((line) => ({
      ...toPricedLineInput(line),
      restock: line.restock,
      unit_cost: line.restock ? line.unit_cost : null,
      ...(line.source_invoice_line_id ? { source_invoice_line_id: line.source_invoice_line_id } : {}),
    }));

    if (creditNote) {
      mutation.mutate({
        credit_note_date: values.credit_note_date,
        reference: values.reference,
        notes: values.notes,
        lines: lineInputs,
      });
      return;
    }

    if (needsUnapplied && !values.unapplied_credit_account_id) {
      setError("unapplied_credit_account_id", {
        type: "validate",
        message: sourceInvoice
          ? "This credit (estimate) exceeds the invoice's balance due. Choose where the excess is held."
          : "Without a source invoice the whole credit is held as customer credit. Choose the account.",
      });
      return;
    }

    mutation.mutate({
      customer_id: values.customer_id,
      currency: sourceInvoice?.currency ?? org.currency,
      credit_note_date: values.credit_note_date,
      reason: values.reason,
      reference: values.reference,
      source_invoice_id: sourceInvoiceId,
      receivable_account_id: values.receivable_account_id,
      tax_payable_account_id: values.tax_payable_account_id,
      unapplied_credit_account_id: values.unapplied_credit_account_id,
      warehouse_id: values.warehouse_id,
      notes: values.notes,
      lines: lineInputs,
    });
  }

  const lockedHint = isEdit ? "Fixed once the draft is saved." : undefined;

  function renderRestock(index: number) {
    const line = lines?.[index];
    const item = line?.item_id ? itemById.get(line.item_id) : undefined;
    // An item beyond the cached page is left to the backend to judge.
    const notTracked = Boolean(item && !(item.item_type === "product" && item.track_inventory));
    const restock = Boolean(line?.restock);
    const lineErrors = errors.lines?.[index];

    return (
      <div className="grid gap-3 border-t border-ink-100 pt-3 sm:grid-cols-12">
        <div className="sm:col-span-5">
          {/* Controller, not register: a natively disabled checkbox would read as undefined. */}
          <Controller
            control={control}
            name={`lines.${index}.restock`}
            render={({ field }) => (
              <Checkbox
                label="Return to stock"
                hint={
                  notTracked
                    ? "Only products that track inventory can be restocked."
                    : "Puts this quantity back into the warehouse when the credit note is issued."
                }
                checked={field.value}
                onChange={(event) => field.onChange(event.target.checked)}
                onBlur={field.onBlur}
                disabled={!restock && (notTracked || !line?.item_id)}
              />
            )}
          />
          {line?.source_invoice_line_id ? (
            <p className="mt-1 text-xs text-ink-500">Linked to the invoice line; cannot credit more than was invoiced.</p>
          ) : null}
        </div>
        {restock ? (
          <FormField
            label="Unit cost"
            required
            className="sm:col-span-4"
            error={lineErrors?.unit_cost?.message ?? null}
            hint="Value per unit returned to inventory (moved from COGS back to inventory)."
          >
            <Controller
              control={control}
              name={`lines.${index}.unit_cost`}
              render={({ field }) => (
                <NumericInput scale={4} nonNegative value={field.value} onValueChange={field.onChange} onBlur={field.onBlur} />
              )}
            />
          </FormField>
        ) : null}
      </div>
    );
  }

  return (
    <FormProvider {...form}>
      <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
        <FormError message={formError.message} reference={formError.reference} />

        <Card>
          <CardHeader
            title="Credit note"
            {...(sourceInvoice
              ? {
                  description: `Against invoice ${sourceInvoice.number}. Tax treatment is inherited from that invoice.`,
                }
              : {})}
          />
          <CardBody className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <FormField
              label="Customer"
              required
              error={errors.customer_id?.message ?? null}
              {...(lockedHint ? { hint: lockedHint } : sourceInvoiceId ? { hint: "The invoice's customer." } : {})}
            >
              <Controller
                control={control}
                name="customer_id"
                render={({ field }) => (
                  <CustomerPicker
                    value={field.value || null}
                    onChange={(value) => field.onChange(value ?? "")}
                    disabled={isEdit || Boolean(sourceInvoiceId)}
                  />
                )}
              />
            </FormField>

            <FormField label="Credit note date" required error={errors.credit_note_date?.message ?? null} hint="The journal is posted on this date when issued.">
              <Input type="date" {...register("credit_note_date")} />
            </FormField>

            <FormField label="Reason" required error={errors.reason?.message ?? null} {...(lockedHint ? { hint: lockedHint } : {})}>
              {/*
                Not registered when locked: RHF reads a natively disabled
                input as undefined, which would fail the schema on save.
              */}
              {isEdit ? (
                <Select defaultValue={creditNote?.reason} disabled>
                  {REASONS.map((reason) => (
                    <option key={reason} value={reason}>
                      {CREDIT_NOTE_REASON_LABELS[reason]}
                    </option>
                  ))}
                </Select>
              ) : (
                <Select {...register("reason")}>
                  {REASONS.map((reason) => (
                    <option key={reason} value={reason}>
                      {CREDIT_NOTE_REASON_LABELS[reason]}
                    </option>
                  ))}
                </Select>
              )}
            </FormField>

            <FormField label="Reference" error={errors.reference?.message ?? null} hint="A return authorisation or your own reference.">
              <Input {...register("reference")} />
            </FormField>

            <FormField
              label="Receivable account"
              error={errors.receivable_account_id?.message ?? null}
              hint={lockedHint ?? (sourceInvoiceId ? "Defaults to the invoice's receivable account." : "Only needed when crediting an invoice.")}
            >
              <Controller
                control={control}
                name="receivable_account_id"
                render={({ field }) => (
                  <AccountPicker accountType="asset" value={field.value} onChange={field.onChange} disabled={isEdit} />
                )}
              />
            </FormField>

            <FormField
              label="Tax payable account"
              error={errors.tax_payable_account_id?.message ?? null}
              hint={lockedHint ?? "Output tax is reversed here. Falls back to the invoice's account."}
            >
              <Controller
                control={control}
                name="tax_payable_account_id"
                render={({ field }) => (
                  <AccountPicker accountType="liability" value={field.value} onChange={field.onChange} disabled={isEdit} />
                )}
              />
            </FormField>

            <FormField
              label="Unapplied credit account"
              required={!isEdit && needsUnapplied}
              error={errors.unapplied_credit_account_id?.message ?? null}
              hint={
                isEdit
                  ? needsUnapplied && !unappliedAccount
                    ? "Not set, and fixed once the draft is saved. Issuing is refused if the credit exceeds the invoice's balance due at that time."
                    : lockedHint
                  : sourceInvoice ? (
                      <>
                        Holds any credit beyond the invoice&apos;s balance due of{" "}
                        <Money value={sourceInvoice.amountDue} currency={sourceInvoice.currency} />.
                      </>
                    ) : (
                      "A liability account holding the credit for the customer."
                    )
              }
            >
              <Controller
                control={control}
                name="unapplied_credit_account_id"
                render={({ field }) => (
                  <AccountPicker accountType="liability" value={field.value} onChange={field.onChange} disabled={isEdit} />
                )}
              />
            </FormField>

            <FormField
              label="Warehouse"
              error={errors.warehouse_id?.message ?? null}
              hint={lockedHint ?? "Required when any line is returned to stock."}
            >
              <Controller
                control={control}
                name="warehouse_id"
                render={({ field }) => <WarehousePicker value={field.value} onChange={field.onChange} disabled={isEdit} />}
              />
            </FormField>
          </CardBody>
        </Card>

        <PricedLinesEditor
          usage="sales"
          emptyLine={EMPTY_LINE}
          renderExtra={renderRestock}
          description="Enter what is being credited. For a partial return, lower the quantity; for a price correction, credit the difference."
        />

        <div className="grid gap-4 lg:grid-cols-5">
          <Card className="lg:col-span-3">
            <CardHeader title="Notes" />
            <CardBody>
              <FormField label="Notes" error={errors.notes?.message ?? null}>
                <Textarea rows={3} {...register("notes")} />
              </FormField>
            </CardBody>
          </Card>
          <div className="lg:col-span-2">
            <EstimatedTotals currency={sourceInvoice?.currency ?? org.currency} />
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
