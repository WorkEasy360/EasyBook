"use client";

import * as React from "react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormError, FormField, Input, Textarea } from "@/components/ui/field";
import { Money } from "@/components/ui/money";
import { useToast } from "@/components/ui/toast";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { formErrorOf, referenceOf } from "@/lib/api/errors";
import { formatDate } from "@/lib/datetime";
import type { PaymentReminderDraft } from "@/types/api/ai";
import { NonAuthoritativeNotice } from "./answer-view";
import { AI_DISABLED_MESSAGE, isAiDisabledError } from "./sources";

/**
 * "Draft reminder" for an outstanding invoice: POST ai/drafts/payment-reminder/.
 *
 * The backend returns text only (`sent: false`) — nothing is emailed,
 * scheduled or recorded, and this dialog has no send button. The draft is
 * checked server-side against the invoice's own facts (a figure not in them
 * is rejected as `draft_rejected`), and those facts are shown beside the
 * draft so the reader can compare. The editable text lives only in this
 * dialog; copying it is the one way out.
 *
 * Generation is explicit (a button in the dialog), not on open: each call
 * counts against the Ask Books rate limit. Draft/void or fully paid invoices
 * are refused by the backend (`invoice_not_outstanding`) and the message is
 * shown as-is.
 *
 * Mount on the invoice detail page for sent/partially paid invoices, gated
 * on PERMISSIONS.USE_AI_ASSISTANT.
 */
export function PaymentReminderDraftButton({ invoiceId, invoiceLabel }: { invoiceId: string; invoiceLabel?: string }) {
  const toast = useToast();
  const [open, setOpen] = React.useState(false);
  const [draft, setDraft] = React.useState<PaymentReminderDraft | null>(null);
  const [subject, setSubject] = React.useState("");
  const [body, setBody] = React.useState("");
  const [disabled, setDisabled] = React.useState(false);
  const [error, setError] = React.useState<{ message: string; reference: string | null } | null>(null);

  const mutation = useIdempotentMutation<PaymentReminderDraft, void>(
    "ai/drafts",
    (_input, idempotencyKey) =>
      api.post<PaymentReminderDraft>(
        "ai/drafts/payment-reminder",
        { invoice_id: invoiceId },
        { idempotencyKey, timeoutMs: 90_000 },
      ),
    {
      onSuccess: (result) => {
        setDraft(result);
        setSubject(result.subject);
        setBody(result.body);
      },
      onError: (failure) => {
        if (isAiDisabledError(failure)) {
          setDisabled(true);
          return;
        }
        setError({ message: formErrorOf(failure) ?? failure.message, reference: referenceOf(failure) });
      },
    },
  );

  async function copy() {
    try {
      await navigator.clipboard.writeText(`Subject: ${subject}\n\n${body}`);
      toast.push({ tone: "success", title: "Reminder copied", description: "Paste it into your own email. Nothing was sent." });
    } catch {
      toast.push({ tone: "warning", title: "Could not copy", description: "Select the text and copy it manually." });
    }
  }

  const close = () => {
    if (!mutation.isPending) setOpen(false);
  };

  return (
    <>
      <Button
        onClick={() => {
          setError(null);
          setDisabled(false);
          setOpen(true);
        }}
      >
        Draft reminder
      </Button>
      <Dialog
        open={open}
        onClose={close}
        size="lg"
        title={invoiceLabel ? `Draft a payment reminder for ${invoiceLabel}` : "Draft a payment reminder"}
        description="Ask Books writes a draft for you to check and copy. It never sends anything."
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={close} disabled={mutation.isPending}>
              Close
            </Button>
            {draft ? (
              <Button variant="primary" onClick={() => void copy()}>
                Copy text
              </Button>
            ) : null}
            <Button
              variant={draft ? "secondary" : "primary"}
              loading={mutation.isPending}
              loadingLabel="Drafting…"
              disabled={disabled}
              onClick={() => {
                setError(null);
                mutation.mutate();
              }}
            >
              {draft ? "Draft again" : "Write draft"}
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-4">
          {disabled ? (
            <div role="alert" className="rounded-md border border-ink-200 bg-ink-50 px-3 py-2 text-sm text-ink-800">
              {AI_DISABLED_MESSAGE}
            </div>
          ) : null}
          <FormError message={error?.message ?? null} reference={error?.reference ?? null} />

          {draft ? (
            <>
              <div className="rounded-md border border-ink-200 bg-ink-50 px-3 py-2 text-xs text-ink-700">
                <p className="font-medium text-ink-800">Invoice facts the draft was checked against</p>
                <p className="mt-1">
                  {draft.facts.invoice_number} for {draft.facts.customer_name}, dated {formatDate(draft.facts.invoice_date)},
                  due {formatDate(draft.facts.due_date)}. Amount due:{" "}
                  <Money value={draft.facts.amount_due} currency={draft.facts.currency} strong />
                </p>
              </div>
              <FormField label="Subject">
                <Input value={subject} onChange={(event) => setSubject(event.target.value)} />
              </FormField>
              <FormField label="Message" hint="Edit freely. Your edits stay in this dialog until you copy them.">
                <Textarea rows={10} value={body} onChange={(event) => setBody(event.target.value)} />
              </FormField>
              <NonAuthoritativeNotice />
            </>
          ) : !disabled ? (
            <p className="text-sm text-ink-600">
              The draft uses only this invoice&apos;s number, dates, customer name and amount due. Review it before you
              send it yourself.
            </p>
          ) : null}
        </div>
      </Dialog>
    </>
  );
}
