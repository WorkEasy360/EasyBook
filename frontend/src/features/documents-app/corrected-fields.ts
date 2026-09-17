/**
 * The review dialog's key/value editor state.
 *
 * `structured_payload` is whatever the OCR provider extracted — untrusted,
 * provider-shaped, possibly nested. The reviewer edits it as flat text pairs
 * and the approved `corrected_fields` are stored as strings: nothing in the
 * backend turns them into an accounting record (documents/CLAUDE.md), so
 * there is no numeric type to preserve, and a money value must not be
 * round-tripped through a JS number anyway.
 */

export interface FieldRow {
  key: string;
  value: string;
}

function asText(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  // Nested extraction (line items, address blocks) is shown as JSON text for
  // the reviewer to correct by hand.
  return JSON.stringify(value);
}

export function seedRows(payload: Record<string, unknown> | null | undefined): FieldRow[] {
  if (!payload) return [];
  return Object.entries(payload).map(([key, value]) => ({ key, value: asText(value) }));
}

export type RowsResult = { ok: true; fields: Record<string, string> } | { ok: false; message: string };

export function rowsToFields(rows: readonly FieldRow[]): RowsResult {
  const fields: Record<string, string> = {};
  for (const [index, row] of rows.entries()) {
    const key = row.key.trim();
    if (!key && !row.value.trim()) continue; // a blank row the user never filled
    if (!key) return { ok: false, message: `Field ${index + 1} has a value but no name.` };
    if (Object.hasOwn(fields, key)) return { ok: false, message: `The field "${key}" appears more than once.` };
    fields[key] = row.value;
  }
  return { ok: true, fields };
}
