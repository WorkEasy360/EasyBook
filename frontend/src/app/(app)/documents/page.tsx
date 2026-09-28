import type { Metadata } from "next";
import { PageHeader } from "@/components/ui/page-header";
import { PageBody } from "@/components/ui/detail";
import { DataTable, type Column } from "@/components/ui/data-table";
import { FilterBar, SortNote } from "@/components/ui/filter-bar";
import {
  DOCUMENT_TYPE_LABELS,
  DOCUMENT_UPLOAD_STATUS,
  OCR_STATUS,
  StatusBadge,
  labelOptions,
  statusOptions,
} from "@/components/ui/status-badge";
import { ForbiddenState } from "@/components/ui/states";
import { DocumentSearchForm } from "@/features/documents-app/document-search-form";
import { UploadDocumentButton } from "@/features/documents-app/upload-document-dialog";
import { serverApi, tryServer } from "@/lib/api/server";
import { capabilitiesFor } from "@/lib/api/capabilities";
import { DEFAULT_PAGE_SIZE, paramOf, parseListQuery, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { formatDateTime } from "@/lib/datetime";
import type { ApiError } from "@/lib/api/errors";
import { formatFileSize, type Document } from "@/types/api/documents";

export const metadata: Metadata = { title: "Documents" };

/**
 * Two modes on one page:
 *  - LIST (no `q`): GET documents/ with the two filters it supports
 *    (document_type, upload_status), shown as filter chips.
 *  - SEARCH (`q` present): GET documents/search/?q&document_type — the one
 *    real search endpoint in the product. It has no upload_status filter and
 *    never returns quarantined documents, so the chips are not shown there.
 * Both are ordered newest first (Document.Meta.ordering).
 */
export default async function DocumentsPage({ searchParams }: { searchParams: Promise<RawSearchParams> }) {
  const session = await requireSession();
  const params = await searchParams;

  if (!roleHasPermission(session.role, PERMISSIONS.VIEW_DOCUMENTS)) {
    return (
      <>
        <PageHeader title="Documents" />
        <ForbiddenState resource="documents" />
      </>
    );
  }

  const canUpload = roleHasPermission(session.role, PERMISSIONS.UPLOAD_DOCUMENT);
  const q = paramOf(params, "q")?.trim() ?? "";
  const searching = q.length > 0;

  const query = parseListQuery("/documents", "documents", params);
  const documentType = query.filters["document_type"] ?? "";

  // Search mode keeps q and document_type in every paging link; the list
  // query helper only knows the list endpoint's filters.
  function searchHref(page: number): string {
    const next = new URLSearchParams({ q });
    if (documentType) next.set("document_type", documentType);
    if (query.pageSize !== DEFAULT_PAGE_SIZE) next.set("page_size", String(query.pageSize));
    if (page > 1) next.set("page", String(page));
    return `/documents?${next.toString()}`;
  }

  const result = await tryServer(() =>
    searching
      ? serverApi.list<Document>("documents/search", {
          query: {
            q,
            document_type: documentType || undefined,
            page: query.page > 1 ? query.page : undefined,
            page_size: query.pageSize !== DEFAULT_PAGE_SIZE ? query.pageSize : undefined,
          },
        })
      : serverApi.list<Document>("documents", { query: query.apiParams }),
  );

  const zone = { timeZone: session.timeZone };
  const hasHeldBack =
    result.ok && result.data.results.some((row) => row.upload_status === "scanning" || row.upload_status === "quarantined");

  const columns: Column<Document>[] = [
    { key: "title", header: "Title", cell: (row) => row.title },
    {
      key: "original_filename",
      header: "File",
      hideBelow: "md",
      cell: (row) => <span className="text-ink-600">{row.original_filename}</span>,
    },
    {
      key: "document_type",
      header: "Type",
      hideBelow: "sm",
      cell: (row) => DOCUMENT_TYPE_LABELS[row.document_type] ?? row.document_type,
    },
    {
      key: "upload_status",
      header: "File status",
      cell: (row) => <StatusBadge status={row.upload_status} map={DOCUMENT_UPLOAD_STATUS} size="sm" />,
    },
    {
      key: "ocr_status",
      header: "OCR",
      hideBelow: "lg",
      cell: (row) => <StatusBadge status={row.ocr_status} map={OCR_STATUS} size="sm" />,
    },
    {
      key: "file_size",
      header: "Size",
      numeric: true,
      hideBelow: "md",
      cell: (row) => formatFileSize(row.file_size),
    },
    {
      key: "created_at",
      header: "Uploaded",
      hideBelow: "sm",
      cell: (row) => <span className="tabular whitespace-nowrap">{formatDateTime(row.created_at, zone)}</span>,
    },
  ];

  return (
    <>
      <PageHeader
        title="Documents"
        description="Receipts, bills, statements and contracts. Files are scanned for malware on upload and can be linked to the records they support."
        actions={canUpload ? <UploadDocumentButton /> : null}
      />
      <PageBody>
        <DocumentSearchForm q={q} documentType={documentType} autoFocus={paramOf(params, "focus") === "search"} />

        {searching ? (
          <p className="text-sm text-ink-600" role="status">
            {result.ok
              ? `${result.data.count} ${result.data.count === 1 ? "document matches" : "documents match"} “${q}”.`
              : `Searching for “${q}”.`}
          </p>
        ) : (
          <FilterBar
            groups={[
              {
                key: "upload_status",
                label: "File status",
                value: query.filters["upload_status"] ?? "",
                options: statusOptions(DOCUMENT_UPLOAD_STATUS),
              },
              {
                key: "document_type",
                label: "Type",
                value: documentType,
                options: labelOptions(DOCUMENT_TYPE_LABELS),
              },
            ]}
            buildFilterHref={query.buildFilterHref}
            clearHref={query.clearHref}
            activeFilterCount={query.activeFilterCount}
          />
        )}

        <DataTable
          caption={searching ? `Documents matching ${q}` : "Documents"}
          columns={columns}
          data={result.ok ? result.data : undefined}
          error={result.ok ? null : (result.error as ApiError)}
          getRowId={(row) => row.id}
          getRowHref={(row) => `/documents/${row.id}`}
          emptyTitle={
            searching
              ? "No documents match this search"
              : query.activeFilterCount > 0
                ? "No documents match these filters"
                : "No documents yet"
          }
          emptyDescription={
            searching
              ? "Search looks at titles, file names and OCR text. Quarantined files are never included."
              : canUpload
                ? "Use Upload document to add a receipt, bill or statement."
                : "Documents uploaded by your team appear here."
          }
          page={query.page}
          pageSize={query.pageSize}
          buildPageHref={searching ? searchHref : query.buildPageHref}
          note={
            <>
              <SortNote description={capabilitiesFor("documents").defaultOrder ?? "newest first"} />
              {hasHeldBack
                ? " Scanning files are still being checked for malware and cannot be downloaded yet; quarantined files were flagged by the scan and can never be downloaded."
                : null}
            </>
          }
        />
      </PageBody>
    </>
  );
}
