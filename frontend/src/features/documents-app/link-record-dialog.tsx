"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Combobox, type ComboboxOption } from "@/components/ui/combobox";
import { Dialog } from "@/components/ui/dialog";
import { FormError, FormField, Select } from "@/components/ui/field";
import { useToast } from "@/components/ui/toast";
import { useApiMutation, useLookup } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { ApiError, formErrorOf, referenceOf } from "@/lib/api/errors";
import type { DocumentLink, DocumentLinkCreateInput, LinkedEntityType } from "@/types/api/documents";
import { LINKABLE_ENTITIES } from "./entity-links";

/**
 * Link this document to a business record: POST documents/{id}/links/.
 *
 * The backend resolves the id through the target module under the caller's
 * organization and answers `entity_not_found` otherwise, so a pasted id from
 * elsewhere cannot be linked. Records are chosen from a picker, never typed.
 * Linking the same record twice returns the existing link — not an error.
 */

type LooseRecord = Record<string, unknown> & { id: string };

const PAGE_SIZE = 200;

function EntityRecordPicker({
  entityType,
  value,
  onChange,
}: {
  entityType: LinkedEntityType;
  value: string | null;
  onChange: (value: string | null) => void;
}) {
  const meta = LINKABLE_ENTITIES[entityType];
  const [search, setSearch] = React.useState("");
  const query = useLookup<LooseRecord>(meta.resource ?? "", `document-link:${entityType}`, "", {
    enabled: Boolean(meta.resource),
    extraParams: { page_size: PAGE_SIZE },
  });

  const describe = meta.describe ?? (() => meta.label);
  const rows = query.data?.results ?? [];
  const needle = search.trim().toLowerCase();
  // No list endpoint supports ?search= (capabilities.ts), so this narrows
  // the one page fetched — the same limitation the shared pickers state.
  const filtered = needle ? rows.filter((row) => describe(row).toLowerCase().includes(needle)) : rows;
  const selected = rows.find((row) => row.id === value);

  return (
    <Combobox
      value={value}
      onChange={(next) => onChange(next)}
      options={filtered.map<ComboboxOption>((row) => ({ value: row.id, label: describe(row) }))}
      search={search}
      onSearchChange={setSearch}
      isLoading={query.isLoading}
      placeholder={`Search ${meta.label.toLowerCase()}s…`}
      emptyMessage={
        query.isError
          ? "Could not load this list."
          : rows.length === 0
            ? `No ${meta.label.toLowerCase()}s yet`
            : "No match among the most recent 200. The API cannot search this list yet."
      }
      selectedLabel={selected ? describe(selected) : null}
    />
  );
}

export function LinkRecordButton({
  documentId,
  entityTypes,
}: {
  documentId: string;
  /** Pickable types the role can read — computed on the server. */
  entityTypes: LinkedEntityType[];
}) {
  const router = useRouter();
  const toast = useToast();
  const formId = React.useId();
  const [open, setOpen] = React.useState(false);
  const [entityType, setEntityType] = React.useState<LinkedEntityType | "">("");
  const [entityId, setEntityId] = React.useState<string | null>(null);
  const [error, setError] = React.useState<{ message: string; reference: string | null } | null>(null);

  const mutation = useApiMutation<DocumentLink, DocumentLinkCreateInput>(
    "documents",
    (input) => api.post<DocumentLink>(`documents/${documentId}/links`, input),
    {
      onSuccess: (link) => {
        setOpen(false);
        toast.push({ tone: "success", title: `Linked to ${LINKABLE_ENTITIES[link.entity_type].label.toLowerCase()}` });
        router.refresh();
      },
      onError: (failure) => setError({ message: formErrorOf(failure) ?? failure.message, reference: referenceOf(failure) }),
    },
  );

  if (entityTypes.length === 0) return null;

  const close = () => {
    if (!mutation.isPending) setOpen(false);
  };

  return (
    <>
      <Button
        size="sm"
        onClick={() => {
          setEntityType("");
          setEntityId(null);
          setError(null);
          setOpen(true);
        }}
      >
        Link a record
      </Button>
      <Dialog
        open={open}
        onClose={close}
        title="Link a record"
        description="The document stays one file; linking only records which transaction it supports."
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={close} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button
              type="submit"
              form={formId}
              variant="primary"
              loading={mutation.isPending}
              disabled={!entityType || !entityId}
            >
              Link record
            </Button>
          </>
        }
      >
        <form
          id={formId}
          method="post"
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            if (!entityType || !entityId) return;
            setError(null);
            mutation.mutate({ entity_type: entityType, entity_id: entityId });
          }}
          className="flex flex-col gap-4"
        >
          <FormField label="Record type" required>
            <Select
              value={entityType}
              placeholder="Choose a record type"
              onChange={(event) => {
                setEntityType(event.target.value as LinkedEntityType);
                setEntityId(null);
              }}
            >
              {entityTypes.map((type) => (
                <option key={type} value={type}>
                  {LINKABLE_ENTITIES[type].label}
                </option>
              ))}
            </Select>
          </FormField>
          {entityType ? (
            <FormField label={LINKABLE_ENTITIES[entityType].label} required>
              <EntityRecordPicker key={entityType} entityType={entityType} value={entityId} onChange={setEntityId} />
            </FormField>
          ) : null}
          <FormError message={error?.message ?? null} reference={error?.reference ?? null} />
        </form>
      </Dialog>
    </>
  );
}

export function UnlinkRecordButton({
  documentId,
  linkId,
  recordLabel,
}: {
  documentId: string;
  linkId: string;
  recordLabel: string;
}) {
  const router = useRouter();
  const toast = useToast();
  const [open, setOpen] = React.useState(false);
  const [error, setError] = React.useState<{ message: string; reference: string | null } | null>(null);

  const mutation = useApiMutation<null, void>(
    "documents",
    async () => {
      try {
        return await api.delete<null>(`documents/${documentId}/links/${linkId}`);
      } catch (failure) {
        // A 5xx after a DELETE is ambiguous: the removal may have committed
        // before the failure (today the BFF itself answers 500 for every 204 —
        // see the shared change request in the documents report). Re-read the
        // server's links and report what is actually true rather than guess.
        if (failure instanceof ApiError && failure.isServer) {
          const remaining = await api.get<DocumentLink[]>(`documents/${documentId}/links`).catch(() => null);
          if (remaining && !remaining.some((link) => link.id === linkId)) return null;
        }
        throw failure;
      }
    },
    {
      onSuccess: () => {
        setOpen(false);
        toast.push({ tone: "success", title: "Link removed" });
        router.refresh();
      },
      onError: (failure) => setError({ message: formErrorOf(failure) ?? failure.message, reference: referenceOf(failure) }),
    },
  );

  const close = () => {
    if (!mutation.isPending) setOpen(false);
  };

  return (
    <>
      <Button
        variant="link"
        size="sm"
        onClick={() => {
          setError(null);
          setOpen(true);
        }}
      >
        Unlink<span className="sr-only"> {recordLabel}</span>
      </Button>
      <Dialog
        open={open}
        onClose={close}
        size="sm"
        title="Remove this link?"
        dismissible={!mutation.isPending}
        footer={
          <>
            <Button variant="secondary" onClick={close} disabled={mutation.isPending}>
              Cancel
            </Button>
            <Button variant="danger" loading={mutation.isPending} onClick={() => mutation.mutate()}>
              Remove link
            </Button>
          </>
        }
      >
        <div className="flex flex-col gap-3 text-sm text-ink-700">
          <p>
            The document will no longer be linked to {recordLabel}. Neither the document nor the record is changed or
            deleted, and the removal is kept in the audit log.
          </p>
          <FormError message={error?.message ?? null} reference={error?.reference ?? null} />
        </div>
      </Dialog>
    </>
  );
}
