"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormError, FormField, Input, Textarea } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { useOrg } from "@/components/providers/org-provider";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { todayInZone } from "@/lib/datetime";
import type { JournalEntry, JournalReverseInput } from "@/types/api/accounting";

/**
 * Reverse a posted journal.
 *
 * Not a <DocumentAction>: that component posts a fixed body, and a reversal
 * takes a posting date and memo (JournalEntryReverseSerializer). The effect is
 * the same kind of irreversible ledger write, so the same guarantees apply —
 * explicit confirmation, one Idempotency-Key per submission, errors kept in
 * the dialog.
 *
 * accounting/services/posting.py :: reverse_journal creates a NEW journal
 * with every debit and credit swapped, POSTS it immediately, and marks the
 * original "reversed". The original is never edited. A journal can be
 * reversed once (`journal_already_reversed`).
 */
export function ReverseJournalDialog({ journal }: { journal: JournalEntry }) {
  const router = useRouter();
  const toast = useToast();
  const org = useOrg();
  const [open, setOpen] = React.useState(false);
  const [postingDate, setPostingDate] = React.useState(() => todayInZone(org.timeZone));
  const [memo, setMemo] = React.useState("");
  const [error, setError] = React.useState<{
    message: string | null;
    reference: string | null;
    fields: Record<string, string[]>;
  }>({ message: null, reference: null, fields: {} });

  const mutation = useIdempotentMutation<JournalEntry, JournalReverseInput>(
    "accounting/journals",
    (input, idempotencyKey) =>
      api.post<JournalEntry>(`accounting/journals/${journal.id}/reverse`, input, { idempotencyKey }),
    {
      onSuccess: (reversal) => {
        setOpen(false);
        toast.push({
          tone: "success",
          title: "Journal reversed",
          description: reversal.journal_number ? `Posted as ${reversal.journal_number}.` : undefined,
        });
        router.push(`/accounting/journals/${reversal.id}`);
        router.refresh();
      },
      onError: (failure) => {
        setError({ message: formErrorOf(failure), reference: referenceOf(failure), fields: fieldErrorsOf(failure) });
      },
    },
  );

  const formId = React.useId();
  const number = journal.journal_number || "this journal";

  function close() {
    if (mutation.isPending) return;
    setOpen(false);
  }

  return (
    <>
      <Button
        variant="danger"
        onClick={() => {
          setError({ message: null, reference: null, fields: {} });
          setOpen(true);
        }}
      >
        Reverse
      </Button>

      <Dialog
        open={open}
        onClose={close}
        title={`Reverse ${number}?`}
        size="md"
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={close} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button type="submit" form={formId} variant="danger" loading={mutation.isPending} disabled={!postingDate}>
              Reverse journal
            </Button>
          </>
        }
      >
        <form
          id={formId}
          method="post"
          noValidate
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            setError({ message: null, reference: null, fields: {} });
            mutation.mutate({ posting_date: postingDate, memo: memo.trim() });
          }}
        >
          <div className="flex flex-col gap-2 text-sm text-ink-700">
            <p>
              A new journal is posted on the date below with every debit and credit of {number} swapped, cancelling
              its effect on the ledger. {number} stays on record, marked reversed.
            </p>
            <p>This cannot be undone, and a journal can only be reversed once.</p>
          </div>
          <FormError message={error.message} reference={error.reference} />
          <FormField
            label="Reversal date"
            required
            error={error.fields["posting_date"]?.[0] ?? null}
            hint="Must fall in an open fiscal year."
          >
            <Input type="date" value={postingDate} onChange={(event) => setPostingDate(event.target.value)} />
          </FormField>
          <FormField
            label="Memo"
            error={error.fields["memo"]?.[0] ?? null}
            hint={`Left blank, the ledger records "Reversal of ${journal.journal_number || "the original"}".`}
          >
            <Textarea rows={2} value={memo} onChange={(event) => setMemo(event.target.value)} />
          </FormField>
        </form>
      </Dialog>
    </>
  );
}
