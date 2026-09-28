"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { FormError, FormField, Input, Select, useFieldContext } from "@/components/ui/field";
import { DOCUMENT_TYPE_LABELS } from "@/components/ui/status-badge";
import { useToast } from "@/components/ui/toast";
import { useApiMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { fieldErrorsOf, formErrorOf, referenceOf } from "@/lib/api/errors";
import { formatFileSize, type DocumentType, type DocumentUploadResponse } from "@/types/api/documents";
import { ALLOWED_UPLOAD_DESCRIPTION, UPLOAD_ACCEPT, precheckUpload } from "./upload-rules";

/**
 * Upload one file: POST documents/upload/ as multipart.
 *
 * The FormData goes through the browser API client untouched (it lets the
 * browser set the multipart boundary) and the BFF forwards the buffered body.
 * The pre-check refuses obviously wrong files before any bytes are sent; the
 * backend still validates extension, declared type, magic bytes and size, and
 * its message is what the user sees when it disagrees.
 *
 * Scanning happens inside the upload request, so the response already says
 * `ready` or `quarantined`. The user lands on the new document either way —
 * a quarantined file is explained there rather than silently dropped.
 *
 * No folder field: folders exist in the model but have no REST resource to
 * list them (documents/CLAUDE.md), so there is nothing to choose from.
 */

function FileInput({ onFile }: { onFile: (file: File | null) => void }) {
  const field = useFieldContext();
  return (
    <input
      id={field?.id}
      type="file"
      name="file"
      accept={UPLOAD_ACCEPT}
      aria-describedby={field?.describedBy}
      aria-invalid={field?.invalid || undefined}
      aria-required
      onChange={(event) => onFile(event.target.files?.[0] ?? null)}
      className="block w-full text-sm text-ink-700 file:mr-3 file:rounded-md file:border file:border-ink-300 file:bg-white file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-ink-800 hover:file:bg-ink-50"
    />
  );
}

export function UploadDocumentButton({ label = "Upload document" }: { label?: string }) {
  const router = useRouter();
  const toast = useToast();
  const formId = React.useId();
  const [open, setOpen] = React.useState(false);
  // Remounts the form (and clears the native file input) on each opening.
  const [formKey, setFormKey] = React.useState(0);
  const [file, setFile] = React.useState<File | null>(null);
  const [title, setTitle] = React.useState("");
  const [documentType, setDocumentType] = React.useState<DocumentType>("general");
  const [fileError, setFileError] = React.useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = React.useState<Record<string, string>>({});
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });

  const mutation = useApiMutation<DocumentUploadResponse, FormData>(
    "documents",
    (body) =>
      // Up to 25 MB crosses two hops (browser → BFF → Django); the default
      // 30 s budget is too tight for that on a slow connection.
      api.post<DocumentUploadResponse>("documents/upload", body, { timeoutMs: 120_000 }),
    {
      onSuccess: (document) => {
        setOpen(false);
        if (document.upload_status === "quarantined") {
          toast.push({
            tone: "warning",
            title: "File quarantined",
            description: "The malware scan flagged this file. It is kept on record but can never be downloaded.",
          });
        } else {
          toast.push({ tone: "success", title: "Document uploaded", description: document.title });
        }
        const duplicates = document.possible_duplicate_ids;
        router.push(
          duplicates.length > 0
            ? `/documents/${document.id}?duplicates=${duplicates.map(encodeURIComponent).join(",")}`
            : `/documents/${document.id}`,
        );
      },
      onError: (error) => {
        setFormError({ message: formErrorOf(error), reference: referenceOf(error) });
        const next: Record<string, string> = {};
        for (const [field, messages] of Object.entries(fieldErrorsOf(error))) {
          if (messages[0]) next[field] = messages[0];
        }
        setFieldErrors(next);
      },
    },
  );

  function reset() {
    setFile(null);
    setTitle("");
    setDocumentType("general");
    setFileError(null);
    setFieldErrors({});
    setFormError({ message: null, reference: null });
    setFormKey((key) => key + 1);
  }

  function onFile(next: File | null) {
    setFile(next);
    setFormError({ message: null, reference: null });
    if (!next) {
      setFileError(null);
      return;
    }
    const check = precheckUpload({ name: next.name, size: next.size, type: next.type });
    setFileError(check.ok ? null : check.message);
  }

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormError({ message: null, reference: null });
    setFieldErrors({});
    if (!file) {
      setFileError("Choose a file to upload.");
      return;
    }
    const check = precheckUpload({ name: file.name, size: file.size, type: file.type });
    if (!check.ok) {
      setFileError(check.message);
      return;
    }
    const body = new FormData();
    body.append("file", file);
    body.append("document_type", documentType);
    // Blank is allowed: the backend titles the document with the sanitized file name.
    if (title.trim()) body.append("title", title.trim());
    mutation.mutate(body);
  }

  return (
    <>
      <Button
        variant="primary"
        onClick={() => {
          reset();
          setOpen(true);
        }}
      >
        {label}
      </Button>

      <Dialog
        open={open}
        onClose={() => {
          if (!mutation.isPending) setOpen(false);
        }}
        title="Upload a document"
        description={`${ALLOWED_UPLOAD_DESCRIPTION}. Every file is scanned for malware before it can be downloaded.`}
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={() => setOpen(false)} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button
              type="submit"
              form={formId}
              variant="primary"
              loading={mutation.isPending}
              loadingLabel="Uploading and scanning…"
              disabled={Boolean(fileError)}
            >
              Upload
            </Button>
          </>
        }
      >
        <form
          key={formKey}
          id={formId}
          method="post"
          encType="multipart/form-data"
          noValidate
          onSubmit={onSubmit}
          className="flex flex-col gap-4"
        >
          <FormError message={formError.message} reference={formError.reference} />
          <FormField
            label="File"
            required
            error={fileError ?? fieldErrors["file"] ?? null}
            hint={
              file && !fileError
                ? `${file.name} · ${formatFileSize(file.size)}`
                : "Up to 25 MB for PDF, 15 MB for images, 10 MB for CSV and text."
            }
          >
            <FileInput onFile={onFile} />
          </FormField>
          <FormField
            label="Title"
            error={fieldErrors["title"] ?? null}
            hint="Leave blank to use the file name."
          >
            <Input value={title} maxLength={255} onChange={(event) => setTitle(event.target.value)} />
          </FormField>
          <FormField label="Document type" error={fieldErrors["document_type"] ?? null}>
            <Select value={documentType} onChange={(event) => setDocumentType(event.target.value as DocumentType)}>
              {Object.entries(DOCUMENT_TYPE_LABELS).map(([value, text]) => (
                <option key={value} value={value}>
                  {text}
                </option>
              ))}
            </Select>
          </FormField>
        </form>
      </Dialog>
    </>
  );
}
