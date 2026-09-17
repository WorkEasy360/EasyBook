import Link from "next/link";
import { Button } from "@/components/ui/button";
import { FormField, Input, Select } from "@/components/ui/field";
import { DOCUMENT_TYPE_LABELS } from "@/components/ui/status-badge";

/**
 * The product's one genuine search: GET documents/search/?q&document_type.
 *
 * A plain GET form (no client state), so a search is a shareable URL and
 * works before hydration. The backend matches `q` case-insensitively against
 * the title, the original file name and OCR text, and never returns a
 * quarantined document (documents/search/__init__.py).
 *
 * `folder_id` and `tags` are also accepted by the endpoint but not offered.
 * BACKEND CONTRACT BLOCKER: folders and tags have no REST resource (no list,
 * no create, no tag assignment — documents/CLAUDE.md), so there is no way to
 * know or assign a value to filter by.
 */
export function DocumentSearchForm({
  q,
  documentType,
  autoFocus = false,
}: {
  q: string;
  documentType: string;
  /** Set when the shell's "Search documents…" link sends `?focus=search`. */
  autoFocus?: boolean;
}) {
  return (
    <form method="get" action="/documents" role="search" aria-label="Search documents" className="flex flex-wrap items-end gap-3">
      <FormField label="Search" hint="Title, file name or OCR text." className="min-w-56 flex-1">
        <Input type="search" name="q" defaultValue={q} maxLength={200} autoFocus={autoFocus} />
      </FormField>
      <FormField label="Document type" className="w-48">
        <Select name="document_type" defaultValue={documentType}>
          <option value="">All types</option>
          {Object.entries(DOCUMENT_TYPE_LABELS).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </Select>
      </FormField>
      <div className="flex items-center gap-3 pb-5">
        <Button type="submit">Search</Button>
        {q || documentType ? (
          <Link href="/documents" className="text-xs font-medium text-brand-700 hover:underline">
            Clear
          </Link>
        ) : null}
      </div>
    </form>
  );
}
