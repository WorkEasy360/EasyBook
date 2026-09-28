import { appHref } from "@/lib/routes";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Badge } from "@/components/ui/badge";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DataTable, type Column } from "@/components/ui/data-table";
import {
  DOCUMENT_TYPE_LABELS,
  DOCUMENT_UPLOAD_STATUS,
  OCR_STATUS,
  StatusBadge,
} from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { DownloadDocumentButton } from "@/features/documents-app/download-button";
import { EditDocumentButton } from "@/features/documents-app/edit-document-dialog";
import { LINKABLE_ENTITIES, entityLabel, pickableEntityTypes } from "@/features/documents-app/entity-links";
import { LinkRecordButton, UnlinkRecordButton } from "@/features/documents-app/link-record-dialog";
import { ReviewExtractionButton } from "@/features/documents-app/review-dialog";
import { serverApi, tryServer } from "@/lib/api/server";
import { recordsById } from "@/lib/api/lookups";
import { paramOf, wholeList, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, errorCodeOf, referenceOf } from "@/lib/api/errors";
import { formatDate, formatDateTime } from "@/lib/datetime";
import { displayName, type Membership } from "@/types/api/accounts";
import {
  formatFileSize,
  type Document,
  type DocumentLink,
  type OcrResult,
  type OcrStatus,
  type UploadStatus,
} from "@/types/api/documents";

export const metadata: Metadata = { title: "Document" };

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** What each file state means for the user — documents/services/{uploads,downloads}.py. */
const UPLOAD_STATUS_NOTES: Partial<Record<UploadStatus, string>> = {
  uploading: "The file is still being stored. It cannot be downloaded yet.",
  scanning: "The file is being checked for malware. It can be downloaded once the scan passes.",
  failed:
    "The file could not be stored. The record is kept for the audit trail, but there is nothing to download — upload the file again.",
  quarantined:
    "The malware scan flagged this file. It is kept on record for the audit trail but can never be downloaded, is left out of search and is never read by Ask Books.",
  archived:
    "This document is archived. It stays on record, keeps its links and can still be downloaded, but Ask Books no longer reads it. Archiving cannot be undone.",
};

/** documents/services/ocr.py — what each extraction state means. */
const OCR_STATUS_NOTES: Record<OcrStatus, string> = {
  not_requested: "No text has been extracted from this file.",
  queued: "Waiting for a background worker to extract the text. Refresh this page to see the result once it has run.",
  processing: "Text extraction is running. Refresh this page to see the result.",
  needs_review:
    "Extraction finished below the confidence threshold. A reviewer must check it before anyone relies on it.",
  completed: "Extraction is complete — either confident enough on its own or approved by a reviewer.",
  failed: "Extraction failed. You can request it again.",
};

/** States in which an OCRResult row exists (process_ocr writes it before moving on). */
const HAS_RESULT: ReadonlySet<OcrStatus> = new Set(["needs_review", "completed", "failed"]);

function payloadText(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value || "—";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

export default async function DocumentDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<RawSearchParams>;
}) {
  const session = await requireSession();
  const { id } = await params;
  const query = await searchParams;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_DOCUMENTS)) {
    return (
      <>
        <PageHeader title="Document" />
        <ForbiddenState resource="documents" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<Document>(`documents/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Document" breadcrumbs={[{ label: "Documents", href: "/documents" }, { label: "Document" }]} />
        <ErrorState title="Could not load this document" message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const document = result.data;
  const status = document.upload_status;

  // `?duplicates=` is set by the upload dialog from the upload response's
  // possible_duplicate_ids — the only moment the backend reports them.
  const duplicateIds = (paramOf(query, "duplicates") ?? "")
    .split(",")
    .filter((value) => UUID_PATTERN.test(value) && value !== document.id)
    .slice(0, 10);

  const [links, ocr, members, duplicates] = await Promise.all([
    tryServer(() => serverApi.get<DocumentLink[]>(`documents/${document.id}/links`)),
    HAS_RESULT.has(document.ocr_status)
      ? tryServer(() => serverApi.get<OcrResult>(`documents/${document.id}/ocr`))
      : Promise.resolve(null),
    tryServer(() => serverApi.get<Membership[]>("organizations/members")),
    recordsById<Document>("documents", duplicateIds),
  ]);

  // Each linked record is fetched from its own module, only where the role
  // may read it; otherwise the link is shown by type alone.
  const linkRows = links.ok ? links.data : [];
  const linkedRecords = await Promise.all(
    linkRows.map(async (link) => {
      const meta = LINKABLE_ENTITIES[link.entity_type];
      if (!meta?.resource || !meta.permission || !roleHasPermission(role, meta.permission)) return null;
      const record = await tryServer(() =>
        serverApi.get<Record<string, unknown>>(`${meta.resource}/${link.entity_id}`),
      );
      return record.ok ? record.data : null;
    }),
  );

  const memberNames = new Map<string, string>(
    members.ok ? members.data.map((member) => [member.user.id, displayName(member.user)]) : [],
  );
  function personName(userId: string | null): string {
    if (!userId) return "—";
    if (userId === session.user.id) return "You";
    return memberNames.get(userId) ?? "A former member";
  }

  const zone = { timeZone: session.timeZone };
  const canDownload =
    roleHasPermission(role, PERMISSIONS.DOWNLOAD_DOCUMENT) && (status === "ready" || status === "archived");
  const canManage = roleHasPermission(role, PERMISSIONS.MANAGE_DOCUMENTS);
  const canArchive = roleHasPermission(role, PERMISSIONS.DELETE_DOCUMENT) && status === "ready";
  const canRequestOcr =
    roleHasPermission(role, PERMISSIONS.UPLOAD_DOCUMENT) &&
    status === "ready" &&
    (document.ocr_status === "not_requested" || document.ocr_status === "failed");
  const canReview = roleHasPermission(role, PERMISSIONS.REVIEW_DOCUMENT_OCR) && document.ocr_status === "needs_review";
  const linkableTypes = pickableEntityTypes().filter((type) => {
    const permission = LINKABLE_ENTITIES[type].permission;
    return permission ? roleHasPermission(role, permission) : false;
  });

  // A missing result while the status says one exists is a 404 the backend
  // raises as `ocr_result_not_found`; anything else is a real failure.
  const ocrResult = ocr?.ok ? ocr.data : null;
  const ocrError = ocr && !ocr.ok && errorCodeOf(ocr.error) !== "ocr_result_not_found" ? ocr.error : null;

  const statusNote = UPLOAD_STATUS_NOTES[status];

  type LinkRow = { link: DocumentLink; record: Record<string, unknown> | null };
  const linkTableRows: LinkRow[] = linkRows.map((link, index) => ({ link, record: linkedRecords[index] ?? null }));

  const linkColumns: Column<LinkRow>[] = [
    {
      key: "record",
      header: "Record",
      cell: ({ link, record }) => {
        const meta = LINKABLE_ENTITIES[link.entity_type];
        const label = record && meta?.describe ? meta.describe(record) : null;
        if (label && meta?.href) {
          return (
            <Link href={appHref(meta.href(link.entity_id))} className="font-medium text-brand-700 hover:underline">
              {label}
            </Link>
          );
        }
        if (meta?.resource) {
          // No read permission, or the record is gone: say which, neutrally.
          return <span className="text-ink-500">{record === null ? `${meta.label} (not available to you)` : meta.label}</span>;
        }
        return <span className="text-ink-600">{meta?.label ?? link.entity_type} — no screen in EasyBook yet</span>;
      },
    },
    { key: "type", header: "Type", hideBelow: "sm", cell: ({ link }) => entityLabel(link.entity_type) },
    { key: "created_by", header: "Linked by", hideBelow: "md", cell: ({ link }) => personName(link.created_by) },
    {
      key: "created_at",
      header: "Linked",
      hideBelow: "md",
      cell: ({ link }) => <span className="tabular whitespace-nowrap">{formatDateTime(link.created_at, zone)}</span>,
    },
    ...(canManage
      ? [
          {
            key: "actions",
            header: "",
            headerLabel: "Actions",
            numeric: true,
            cell: ({ link, record }: LinkRow) => {
              const meta = LINKABLE_ENTITIES[link.entity_type];
              const label = record && meta?.describe ? meta.describe(record) : `this ${entityLabel(link.entity_type).toLowerCase()}`;
              return <UnlinkRecordButton documentId={document.id} linkId={link.id} recordLabel={label} />;
            },
          } satisfies Column<LinkRow>,
        ]
      : []),
  ];

  return (
    <>
      <PageHeader
        title={document.title}
        breadcrumbs={[{ label: "Documents", href: "/documents" }, { label: document.title }]}
        meta={
          <>
            <StatusBadge status={status} map={DOCUMENT_UPLOAD_STATUS} />
            {document.legal_hold ? (
              <Badge tone="warning" marker>
                Legal hold
              </Badge>
            ) : null}
          </>
        }
        description={`${DOCUMENT_TYPE_LABELS[document.document_type] ?? document.document_type} · ${document.original_filename}`}
        actions={
          <>
            {canManage ? <EditDocumentButton document={document} /> : null}
            {canArchive ? (
              <DocumentAction
                resource="documents"
                id={document.id}
                action="archive"
                label="Archive"
                variant="danger"
                confirmTitle={`Archive ${document.title}?`}
                confirmMessage={
                  <>
                    <p>
                      Archiving keeps the document and its links on record — nothing is deleted, and it can still be
                      downloaded. Ask Books stops reading it.
                    </p>
                    {document.legal_hold ? (
                      <p className="mt-2">This document is on legal hold; archiving does not release or remove it.</p>
                    ) : null}
                    <p className="mt-2">Archiving cannot be undone.</p>
                  </>
                }
                successTitle="Document archived"
              />
            ) : null}
            {canDownload ? (
              <DownloadDocumentButton documentId={document.id} filename={document.original_filename} variant="primary" />
            ) : null}
          </>
        }
      />

      <PageBody>
        {duplicates.size > 0 ? (
          <div role="status" className="rounded-md border border-warning-100 bg-warning-50 px-3 py-2 text-sm text-warning-700">
            <p className="font-medium">A file with identical content was uploaded before.</p>
            <p className="mt-1">
              This upload was kept. Compare it with{" "}
              {[...duplicates.values()].map((duplicate, index) => (
                <span key={duplicate.id}>
                  {index > 0 ? ", " : null}
                  <Link href={`/documents/${duplicate.id}`} className="font-medium underline">
                    {duplicate.title}
                  </Link>
                </span>
              ))}{" "}
              and archive whichever is not needed.
            </p>
          </div>
        ) : null}

        {statusNote ? (
          <div
            role="note"
            className={
              status === "quarantined" || status === "failed"
                ? "rounded-md border border-danger-100 bg-danger-50 px-3 py-2 text-sm text-danger-700"
                : "rounded-md border border-ink-200 bg-ink-50 px-3 py-2 text-sm text-ink-700"
            }
          >
            {statusNote}
          </div>
        ) : null}

        <Card>
          <CardHeader title="Details" />
          <CardBody>
            <DetailList
              columns={3}
              items={[
                { label: "Type", value: DOCUMENT_TYPE_LABELS[document.document_type] ?? document.document_type },
                { label: "Original file name", value: <span className="break-all">{document.original_filename}</span> },
                { label: "File type", value: document.mime_type },
                { label: "Size", value: formatFileSize(document.file_size) },
                { label: "Uploaded by", value: personName(document.uploaded_by) },
                { label: "Uploaded", value: formatDateTime(document.created_at, zone) },
                { label: "Last changed", value: formatDateTime(document.updated_at, zone) },
                {
                  label: "Retain until",
                  value: document.retention_until ? formatDate(document.retention_until) : "No retention date set",
                },
                { label: "Legal hold", value: document.legal_hold ? "Yes" : "No" },
                ...(document.archived_at ? [{ label: "Archived", value: formatDateTime(document.archived_at, zone) }] : []),
                ...(document.folder ? [{ label: "Folder", value: "Filed in a folder" }] : []),
                {
                  label: "SHA-256 checksum",
                  value: <span className="font-mono text-xs break-all text-ink-700">{document.checksum_sha256}</span>,
                  span: true,
                },
              ]}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            title="Text extraction (OCR)"
            description={OCR_STATUS_NOTES[document.ocr_status]}
            actions={
              <>
                <StatusBadge status={document.ocr_status} map={OCR_STATUS} />
                {canRequestOcr ? (
                  <DocumentAction
                    resource="documents"
                    id={document.id}
                    action="ocr"
                    label={document.ocr_status === "failed" ? "Retry OCR" : "Request OCR"}
                    size="sm"
                    confirmTitle="Extract text from this document?"
                    confirmMessage={
                      <p>
                        Extraction runs in the background and its result must be reviewed before anyone relies on it.
                        Nothing in your books changes — no bill, expense or journal is created from the text.
                      </p>
                    }
                    successTitle="OCR requested"
                  />
                ) : null}
                {canReview ? (
                  <ReviewExtractionButton documentId={document.id} structuredPayload={ocrResult?.structured_payload ?? null} />
                ) : null}
              </>
            }
          />
          <CardBody>
            {ocrError ? (
              <ErrorState
                compact
                title="Could not load the extraction"
                message={ocrError.message}
                reference={referenceOf(ocrError)}
              />
            ) : ocrResult ? (
              <div className="flex flex-col gap-4">
                <div>
                  <Badge tone="warning" marker>
                    Extracted by OCR — verify before use
                  </Badge>
                </div>
                <DetailList
                  columns={3}
                  items={[
                    {
                      label: "Provider",
                      value: `${ocrResult.provider}${ocrResult.provider_version ? ` v${ocrResult.provider_version}` : ""}`,
                    },
                    {
                      label: "Confidence (0 to 1)",
                      value: <span className="tabular">{ocrResult.confidence ?? "Not reported"}</span>,
                    },
                    { label: "Processed", value: formatDateTime(ocrResult.processed_at, zone) },
                    ...(ocrResult.error_code
                      ? [
                          {
                            label: "Error",
                            value: `${ocrResult.error_message || "Extraction failed."} (${ocrResult.error_code})`,
                            span: true,
                          },
                        ]
                      : []),
                  ]}
                />
                {ocrResult.provider === "manual" ? (
                  <p className="text-xs text-ink-500">
                    The manual provider does not read text from files. It records an empty extraction so a reviewer can
                    key in the fields by hand.
                  </p>
                ) : null}
                <div>
                  <h3 className="text-2xs font-medium tracking-wide text-ink-500 uppercase">Extracted text</h3>
                  {ocrResult.raw_text ? (
                    // Untrusted third-party text: rendered as text in a <pre>,
                    // never as HTML or Markdown.
                    <pre className="mt-1 max-h-80 overflow-auto rounded-md border border-ink-200 bg-ink-50 p-3 font-mono text-xs break-words whitespace-pre-wrap text-ink-800">
                      {ocrResult.raw_text}
                    </pre>
                  ) : (
                    <p className="mt-1 text-sm text-ink-500">No text was extracted.</p>
                  )}
                </div>
                <div>
                  <h3 className="text-2xs font-medium tracking-wide text-ink-500 uppercase">Extracted fields</h3>
                  {Object.keys(ocrResult.structured_payload ?? {}).length > 0 ? (
                    <DetailList
                      className="mt-1"
                      items={Object.entries(ocrResult.structured_payload).map(([key, value]) => ({
                        label: key,
                        value: <span className="break-words whitespace-pre-wrap">{payloadText(value)}</span>,
                      }))}
                    />
                  ) : (
                    <p className="mt-1 text-sm text-ink-500">No fields were extracted.</p>
                  )}
                </div>
                {document.ocr_status === "completed" ? (
                  // BACKEND CONTRACT BLOCKER: no `GET documents/{id}/review/`
                  // exists — DocumentReview (status, reviewer, notes,
                  // corrected_fields) is only returned by the POST that creates
                  // it, so a finished review cannot be displayed here.
                  <p className="text-xs text-ink-500">
                    A reviewer&apos;s corrections are kept with the review, which the API does not return for display.
                  </p>
                ) : null}
              </div>
            ) : (
              <p className="text-sm text-ink-500">
                {status === "ready" || document.ocr_status !== "not_requested"
                  ? "There is no extraction result to show yet."
                  : "Text can be extracted once the file is ready."}
              </p>
            )}
          </CardBody>
        </Card>

        <Section
          title="Linked records"
          description="Transactions and contacts this document supports."
          actions={canManage ? <LinkRecordButton documentId={document.id} entityTypes={linkableTypes} /> : null}
        >
          <DataTable
            caption={`Records linked to ${document.title}`}
            columns={linkColumns}
            data={links.ok ? wholeList(linkTableRows) : undefined}
            error={links.ok ? null : (links.error as ApiError)}
            getRowId={({ link }) => link.id}
            emptyTitle="Not linked to any record"
            emptyDescription={
              canManage ? "Use Link a record to attach this document to an invoice, bill, expense or other record." : undefined
            }
            page={1}
            pageSize={Math.max(linkTableRows.length, 1)}
            buildPageHref={() => `/documents/${document.id}`}
          />
        </Section>
      </PageBody>
    </>
  );
}
