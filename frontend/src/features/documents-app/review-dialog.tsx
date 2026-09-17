"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormError, FormField, Input, Textarea } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { formErrorOf, referenceOf } from "@/lib/api/errors";
import type { DocumentReview, ReviewActionInput } from "@/types/api/documents";
import { rowsToFields, seedRows, type FieldRow } from "./corrected-fields";

/**
 * Human review of an OCR extraction: POST documents/{id}/review/.
 *
 * This is a state transition (an approval moves ocr_status to `completed`),
 * so it follows DocumentAction's contract — explicit confirm button, one
 * Idempotency-Key per submission, the rejection kept in the dialog,
 * router.refresh() after — but it needs a form DocumentAction cannot host.
 *
 * What the backend does with it (documents/services/review.py): records the
 * decision and the corrected fields, nothing else. No bill, expense or journal
 * is created from them. A review is final: the opposite decision later fails
 * with `review_already_finalized`, and that message is shown here.
 */

/** A stable React key per row, so removing one does not shift focus into its neighbour. */
type EditorRow = FieldRow & { uid: number };

export function ReviewExtractionButton({
  documentId,
  structuredPayload,
}: {
  documentId: string;
  structuredPayload: Record<string, unknown> | null;
}) {
  const router = useRouter();
  const toast = useToast();
  const formId = React.useId();
  const [open, setOpen] = React.useState(false);
  const [decision, setDecision] = React.useState<"approved" | "rejected">("approved");
  const [notes, setNotes] = React.useState("");
  const [rows, setRows] = React.useState<EditorRow[]>([]);
  const nextUid = React.useRef(0);
  const [error, setError] = React.useState<{ message: string; reference: string | null } | null>(null);

  const mutation = useIdempotentMutation<DocumentReview, ReviewActionInput>(
    "documents",
    (input, idempotencyKey) => api.post<DocumentReview>(`documents/${documentId}/review`, input, { idempotencyKey }),
    {
      onSuccess: (review) => {
        setOpen(false);
        toast.push({
          tone: "success",
          title: review.status === "approved" ? "Extraction approved" : "Extraction rejected",
        });
        router.refresh();
      },
      onError: (failure) => setError({ message: formErrorOf(failure) ?? failure.message, reference: referenceOf(failure) }),
    },
  );

  function start() {
    setRows(seedRows(structuredPayload).map((row) => ({ ...row, uid: nextUid.current++ })));
    setDecision("approved");
    setNotes("");
    setError(null);
    setOpen(true);
  }

  function updateRow(index: number, patch: Partial<FieldRow>) {
    setRows((current) => current.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  function addRow() {
    setRows((current) => [...current, { key: "", value: "", uid: nextUid.current++ }]);
  }

  function removeRow(index: number) {
    setRows((current) => current.filter((_, i) => i !== index));
  }

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    if (decision === "rejected") {
      // reject_review stores no corrected fields, so none are sent.
      mutation.mutate({ status: "rejected", notes: notes.trim() });
      return;
    }
    const built = rowsToFields(rows);
    if (!built.ok) {
      setError({ message: built.message, reference: null });
      return;
    }
    mutation.mutate({ status: "approved", notes: notes.trim(), corrected_fields: built.fields });
  }

  const close = () => {
    if (!mutation.isPending) setOpen(false);
  };

  return (
    <>
      <Button variant="primary" onClick={start}>
        Review extraction
      </Button>
      <Dialog
        open={open}
        onClose={close}
        size="lg"
        title="Review OCR extraction"
        description="Approving marks the extraction as checked. It does not create or change any accounting record."
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={close} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button
              type="submit"
              form={formId}
              variant={decision === "rejected" ? "danger" : "primary"}
              loading={mutation.isPending}
            >
              {decision === "rejected" ? "Reject extraction" : "Approve extraction"}
            </Button>
          </>
        }
      >
        <form id={formId} method="post" noValidate onSubmit={onSubmit} className="flex flex-col gap-4">
          <fieldset className="flex flex-col gap-2">
            <legend className="mb-1 text-xs font-medium text-ink-700">Decision</legend>
            {(
              [
                ["approved", "Approve", "The extraction, with any corrections below, is accurate."],
                ["rejected", "Reject", "The extraction is unusable. Corrections are not kept."],
              ] as const
            ).map(([value, label, hint]) => (
              <label key={value} className="flex items-start gap-2 text-sm text-ink-800">
                <input
                  type="radio"
                  name="decision"
                  value={value}
                  checked={decision === value}
                  onChange={() => setDecision(value)}
                  className="mt-1"
                />
                <span>
                  <span className="font-medium">{label}</span>
                  <span className="block text-xs text-ink-500">{hint}</span>
                </span>
              </label>
            ))}
          </fieldset>

          {decision === "approved" ? (
            <fieldset className="flex flex-col gap-3 rounded-md border border-warning-100 bg-warning-50/40 p-3">
              <legend className="px-1 text-xs font-medium text-warning-700">
                Extracted by OCR — verify before use
              </legend>
              <p className="text-xs text-ink-600">
                Each value below came from automated extraction and may be wrong. Correct it against the document
                itself. Values are saved as text exactly as typed.
              </p>
              {rows.length === 0 ? (
                <p className="text-xs text-ink-500">The provider extracted no fields. Add any you want to record.</p>
              ) : null}
              {rows.map((row, index) => (
                <div key={row.uid} role="group" aria-label={`Field ${index + 1}`} className="grid gap-2 sm:grid-cols-[1fr_2fr_auto] sm:items-end">
                  <FormField label={`Field ${index + 1} name`}>
                    <Input value={row.key} onChange={(event) => updateRow(index, { key: event.target.value })} />
                  </FormField>
                  <FormField label={`Field ${index + 1} value`}>
                    <Input value={row.value} onChange={(event) => updateRow(index, { value: event.target.value })} />
                  </FormField>
                  <Button variant="ghost" size="sm" onClick={() => removeRow(index)}>
                    Remove<span className="sr-only"> field {index + 1}</span>
                  </Button>
                </div>
              ))}
              <div>
                <Button size="sm" onClick={addRow}>
                  Add field
                </Button>
              </div>
            </fieldset>
          ) : null}

          <FormField label="Notes" hint="Kept with the review for the audit trail.">
            <Textarea rows={3} value={notes} onChange={(event) => setNotes(event.target.value)} />
          </FormField>

          <FormError message={error?.message ?? null} reference={error?.reference ?? null} />
        </form>
      </Dialog>
    </>
  );
}
