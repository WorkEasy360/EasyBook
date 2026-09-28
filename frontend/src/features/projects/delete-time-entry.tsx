"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormError } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { useApiMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { formErrorOf, referenceOf } from "@/lib/api/errors";

/**
 * DELETE /time-entries/{id}/ — refused for invoiced time
 * (`time_entry_invoiced`), so the button is only rendered for entries that
 * are not. Deleting loses the cost side of the hours as well; a rejected
 * entry is usually better corrected and resubmitted, and the dialog says so.
 */
export function DeleteTimeEntryButton({ entryId, summary }: { entryId: string; summary: string }) {
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = React.useState(false);
  const [error, setError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const mutation = useApiMutation<unknown, void>("time-entries", () => api.delete<unknown>(`time-entries/${entryId}`), {
    onSuccess: () => {
      setOpen(false);
      toast.push({ tone: "success", title: "Time entry deleted" });
      router.refresh();
    },
    onError: (failure) => setError({ message: formErrorOf(failure), reference: referenceOf(failure) }),
  });

  function close() {
    if (!mutation.isPending) setOpen(false);
  }

  return (
    <>
      <Button
        variant="link"
        size="sm"
        className="text-danger-600"
        onClick={() => {
          setError({ message: null, reference: null });
          setOpen(true);
        }}
      >
        Delete<span className="sr-only"> time entry {summary}</span>
      </Button>
      <Dialog
        open={open}
        onClose={close}
        title="Delete this time entry?"
        size="sm"
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={close} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button variant="danger" loading={mutation.isPending} onClick={() => mutation.mutate()}>
              Delete entry
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3 text-sm text-ink-700">
          <p>{summary} will be removed, including the cost it adds to the project. This cannot be undone.</p>
          <p>If the hours were really worked but recorded wrongly, edit the entry instead.</p>
          <FormError message={error.message} reference={error.reference} />
        </div>
      </Dialog>
    </>
  );
}
