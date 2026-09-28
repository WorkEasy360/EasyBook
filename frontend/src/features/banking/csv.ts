import type { AmountMode, CsvColumnMapping } from "@/types/api/banking";

/**
 * Client-side helpers for the statement import wizard.
 *
 * The browser reads the file only to SHOW it: the header row, so a person can
 * say which column is which, and a few raw rows, so they can check that
 * choice. It never parses a date or an amount — backend/banking/services/
 * parsers.py does that on import, with Decimal, and its row-numbered error is
 * the one that counts. Interpreting cells here as well would be a second
 * parser that could disagree with the real one.
 */

/** backend: parsers.py :: MAX_FILE_BYTES. Checked early only to save an upload. */
export const MAX_STATEMENT_BYTES = 10 * 1024 * 1024;

/**
 * backend: parsers.py :: DEFAULT_DATE_FORMATS, in the order the server tries
 * them. Order matters: "05/09/2026" matches %d/%m/%Y before %m/%d/%Y, so an
 * American file needs its format chosen explicitly.
 */
export const DATE_FORMAT_OPTIONS: ReadonlyArray<{ value: string; label: string; example: string }> = [
  { value: "%Y-%m-%d", label: "YYYY-MM-DD", example: "2026-09-05" },
  { value: "%d/%m/%Y", label: "DD/MM/YYYY", example: "05/09/2026" },
  { value: "%d-%m-%Y", label: "DD-MM-YYYY", example: "05-09-2026" },
  { value: "%m/%d/%Y", label: "MM/DD/YYYY", example: "09/05/2026" },
  { value: "%d-%b-%Y", label: "DD-Mon-YYYY", example: "05-Sep-2026" },
  { value: "%d %b %Y", label: "DD Mon YYYY", example: "05 Sep 2026" },
  { value: "%d/%m/%y", label: "DD/MM/YY", example: "05/09/26" },
  { value: "%d-%m-%y", label: "DD-MM-YY", example: "05-09-26" },
];

export interface ParsedCsvPreview {
  headers: string[];
  /** First data rows, cells as raw text, blank lines skipped. */
  rows: string[][];
  /** Non-blank data rows in the whole file. */
  rowCount: number;
}

/**
 * RFC 4180-style reader matching Python's csv module defaults (comma, double
 * quote, "" as an escaped quote, CRLF or LF). Only used for the preview.
 */
export function readCsv(text: string, previewRows = 5): ParsedCsvPreview {
  const records: string[][] = [];
  let field = "";
  let record: string[] = [];
  let inQuotes = false;

  function endRecord() {
    record.push(field);
    records.push(record);
    record = [];
    field = "";
  }

  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (inQuotes) {
      if (char === '"') {
        if (text[index + 1] === '"') {
          field += '"';
          index += 1;
        } else {
          inQuotes = false;
        }
      } else {
        field += char;
      }
      continue;
    }
    if (char === '"') inQuotes = true;
    else if (char === ",") {
      record.push(field);
      field = "";
    } else if (char === "\n") endRecord();
    else if (char === "\r") {
      if (text[index + 1] === "\n") index += 1;
      endRecord();
    } else field += char;
  }
  if (field !== "" || record.length > 0) endRecord();

  const isBlank = (row: string[]) => row.every((cell) => cell.trim() === "");
  const [headerRow, ...dataRows] = records;
  const headers = (headerRow ?? []).map((cell) => cell.trim());
  const nonBlank = dataRows.filter((row) => !isBlank(row));
  return { headers, rows: nonBlank.slice(0, previewRows), rowCount: nonBlank.length };
}

/** backend: parsers.py :: _normalize_header — how the server compares a mapping to the file. */
export function normalizeHeader(value: string): string {
  return value.trim().toLowerCase().replaceAll("_", " ").replaceAll(".", "").trim();
}

type ColumnField =
  | "date_column"
  | "amount_column"
  | "debit_column"
  | "credit_column"
  | "description_column"
  | "reference_column";

/** The header words backend/banking/services/parsers.py :: _SUGGESTIONS recognises. */
const SUGGESTIONS: Record<ColumnField, readonly string[]> = {
  date_column: ["date", "transaction date", "txn date", "value date", "posting date"],
  amount_column: ["amount", "transaction amount", "amt"],
  debit_column: ["debit", "withdrawal", "withdrawal amt", "debit amount", "paid out", "money out"],
  credit_column: ["credit", "deposit", "deposit amt", "credit amount", "paid in", "money in"],
  description_column: ["description", "narration", "particulars", "details", "transaction remarks"],
  reference_column: ["reference", "ref", "cheque no", "chq no", "transaction id", "utr"],
};

/**
 * A PRE-FILL for the mapping form, never an import decision. Mirrors the
 * backend's advisory `suggest_column_mapping` (which has no endpoint), and the
 * wizard makes the person confirm every column before anything is sent: a
 * wrongly guessed amount column books every withdrawal as a deposit.
 */
export function suggestMapping(headers: readonly string[]): Partial<MappingFormValues> {
  const byNormalized = new Map(headers.map((header) => [normalizeHeader(header), header]));
  const suggestion: Partial<MappingFormValues> = {};
  for (const [field, candidates] of Object.entries(SUGGESTIONS) as Array<[ColumnField, readonly string[]]>) {
    const header = candidates.map((candidate) => byNormalized.get(candidate)).find((value) => value !== undefined);
    if (header !== undefined) suggestion[field] = header;
  }
  if (suggestion.debit_column && suggestion.credit_column) {
    suggestion.amount_mode = "debit_credit";
    delete suggestion.amount_column;
  } else if (suggestion.amount_column) {
    suggestion.amount_mode = "signed";
    delete suggestion.debit_column;
    delete suggestion.credit_column;
  }
  return suggestion;
}

/** The wizard's form state: every column is header text, "" for "not in this file". */
export interface MappingFormValues {
  date_column: string;
  date_format: string;
  amount_mode: AmountMode;
  amount_column: string;
  debit_column: string;
  credit_column: string;
  indicator_column: string;
  description_column: string;
  counterparty_column: string;
  reference_column: string;
  external_id_column: string;
  invert_sign: boolean;
}

export const EMPTY_MAPPING: MappingFormValues = {
  date_column: "",
  date_format: "",
  amount_mode: "signed",
  amount_column: "",
  debit_column: "",
  credit_column: "",
  indicator_column: "",
  description_column: "",
  counterparty_column: "",
  reference_column: "",
  external_id_column: "",
  invert_sign: false,
};

/**
 * The columns each amount mode needs — backend: parsers.py :: _validate_mapping.
 * Returned as field → message so the form can put each on its input.
 */
export function mappingErrors(values: MappingFormValues): Partial<Record<keyof MappingFormValues, string>> {
  const errors: Partial<Record<keyof MappingFormValues, string>> = {};
  if (!values.date_column) errors.date_column = "Choose the column holding the transaction date.";
  if (values.amount_mode === "signed" && !values.amount_column) {
    errors.amount_column = "Choose the amount column.";
  }
  if (values.amount_mode === "debit_credit") {
    if (!values.debit_column) errors.debit_column = "Choose the withdrawal (money out) column.";
    if (!values.credit_column) errors.credit_column = "Choose the deposit (money in) column.";
    if (values.debit_column && values.debit_column === values.credit_column) {
      errors.credit_column = "Withdrawals and deposits must be different columns.";
    }
  }
  if (values.amount_mode === "indicator") {
    if (!values.amount_column) errors.amount_column = "Choose the amount column.";
    if (!values.indicator_column) errors.indicator_column = "Choose the Dr/Cr column.";
  }
  return errors;
}

/** Only the columns the chosen mode uses are sent, so a stale choice from another mode cannot ride along. */
export function toMappingPayload(values: MappingFormValues): CsvColumnMapping {
  const mapping: CsvColumnMapping = {
    date_column: values.date_column,
    amount_mode: values.amount_mode,
    description_column: values.description_column,
    counterparty_column: values.counterparty_column,
    reference_column: values.reference_column,
    external_id_column: values.external_id_column,
    date_formats: values.date_format ? [values.date_format] : [],
    invert_sign: values.invert_sign,
  };
  if (values.amount_mode === "signed") mapping.amount_column = values.amount_column;
  if (values.amount_mode === "debit_credit") {
    mapping.debit_column = values.debit_column;
    mapping.credit_column = values.credit_column;
  }
  if (values.amount_mode === "indicator") {
    mapping.amount_column = values.amount_column;
    mapping.indicator_column = values.indicator_column;
  }
  return mapping;
}

/** Raw cell for a mapped column in a preview row, by header position. */
export function cellFor(headers: readonly string[], row: readonly string[], column: string): string {
  if (!column) return "";
  const index = headers.indexOf(column);
  return index === -1 ? "" : (row[index] ?? "");
}
