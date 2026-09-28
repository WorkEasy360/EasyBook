"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormError, FormField, Input, Select } from "@/components/ui/field";
import { DOCUMENT_TYPE_LABELS } from "@/components/ui/status-badge";
import { useToast } from "@/components/ui/toast";
import { useApiMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import type { Document, DocumentType, DocumentUpdateInput } from "@/types/api/documents";

/**
 * Metadata edit: PATCH documents/{id}/ with the only keys
 * DocumentUpdateSerializer accepts that the UI can supply — title and
 * document_type. The file itself, its scan and OCR state never change here
 * (documents/services/uploads.py :: update_document). folder_id is omitted:
 * there is no folder list to choose from.
 */

const DOCUMENT_TYPES = Object.keys(DOCUMENT_TYPE_LABELS) as [DocumentType, ...DocumentType[]];

const schema = z.object({
  title: z.string().trim().min(1, "A title is required.").max(255),
  document_type: z.enum(DOCUMENT_TYPES),
});

type Values = z.infer<typeof schema>;

export function EditDocumentButton({ document }: { document: Pick<Document, "id" | "title" | "document_type"> }) {
  const router = useRouter();
  const toast = useToast();
  const formId = React.useId();
  const [open, setOpen] = React.useState(false);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const {
    register,
    handleSubmit,
    reset,
    setError,
    formState: { errors },
  } = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { title: document.title, document_type: document.document_type },
  });

  const mutation = useApiMutation<Document, DocumentUpdateInput>(
    "documents",
    (input) => api.patch<Document>(`documents/${document.id}`, input),
    {
      onSuccess: (saved) => {
        toast.push({ tone: "success", title: "Document updated", description: saved.title });
        setOpen(false);
        router.refresh();
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        for (const [field, messages] of Object.entries(fieldErrorsOf(error))) {
          if (field in schema.shape && messages[0]) {
            setError(field as keyof Values, { type: "server", message: messages[0] });
          }
        }
      },
    },
  );

  return (
    <>
      <Button
        onClick={() => {
          reset({ title: document.title, document_type: document.document_type });
          setFormError({ message: null, reference: null });
          setOpen(true);
        }}
      >
        Edit details
      </Button>
      <Dialog
        open={open}
        onClose={() => {
          if (!mutation.isPending) setOpen(false);
        }}
        title="Edit document details"
        description="Only the title and type can change. The stored file is never replaced."
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={mutation.isPending}>
              Save changes
            </Button>
          </>
        }
      >
        <form
          id={formId}
          method="post"
          noValidate
          onSubmit={(event) =>
            void handleSubmit((values) => {
              setFormError({ message: null, reference: null });
              mutation.mutate({ title: values.title, document_type: values.document_type });
            })(event)
          }
          className="flex flex-col gap-4"
        >
          <FormError message={formError.message} reference={formError.reference} />
          <FormField label="Title" required error={errors.title?.message ?? null}>
            <Input maxLength={255} {...register("title")} />
          </FormField>
          <FormField label="Document type" required error={errors.document_type?.message ?? null}>
            <Select {...register("document_type")}>
              {DOCUMENT_TYPES.map((value) => (
                <option key={value} value={value}>
                  {DOCUMENT_TYPE_LABELS[value]}
                </option>
              ))}
            </Select>
          </FormField>
        </form>
      </Dialog>
    </>
  );
}
