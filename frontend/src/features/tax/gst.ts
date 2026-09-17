import type { GstBucket, GstSummary, GstTaxTotals, Gstr1Summary, Gstr3bSummary } from "@/types/api/reports";

/**
 * Normalises the GST summaries (compliance.selectors, passed through by the
 * reports API) into table sections.
 *
 * Layout only: every amount is a bucket the API returned, and nothing is
 * totalled here — the keyed maps (by GSTIN, place of supply, HSN|rate) come
 * with no total from the API, so none is shown.
 *
 * Labels are the API's own field names made readable ("b2c_large" →
 * "B2C large"), always shown alongside the raw path. The UI deliberately adds
 * no GSTR table numbering or compliance wording the API does not return.
 */

export interface GstRow {
  key: string;
  label: string;
  /** The raw field path, for fixed-field rows, e.g. "outward.taxable". */
  field?: string;
  /** Tax rate, for HSN rows (the part after "|"). */
  rate?: string;
  bucket: GstBucket | GstTaxTotals;
}

export type GstSectionKind =
  /** Rows are fixed field names of the payload (outward.taxable, itc.all_other…). */
  | "fields"
  /** Rows are data keys: a GSTIN, a place-of-supply state code, an export type. */
  | "keyed"
  /** Rows are "<hsn>|<rate>" keys. */
  | "hsn";

export interface GstSection {
  /** Dotted path in the payload, e.g. "hsn_summary.b2c". */
  path: string;
  title: string;
  kind: GstSectionKind;
  /** Header for the first column of a keyed section. */
  keyLabel: string;
  rows: GstRow[];
}

const ACRONYMS: Record<string, string> = { b2b: "B2B", b2c: "B2C", itc: "ITC", hsn: "HSN", gst: "GST", gstin: "GSTIN" };

/** "b2c_large" → "B2C large", "inward_reverse_charge" → "Inward reverse charge". */
export function humanizeGstKey(key: string): string {
  if (!key) return "Not set";
  const words = key.split("_").filter(Boolean).map((word) => ACRONYMS[word] ?? word);
  const [first = "", ...rest] = words;
  return [first.charAt(0).toUpperCase() + first.slice(1), ...rest].join(" ");
}

/** "8471|18.00" → {hsn: "8471", rate: "18.00"}; the HSN part may be empty. */
export function splitHsnKey(key: string): { hsn: string; rate: string } {
  const index = key.lastIndexOf("|");
  if (index === -1) return { hsn: key, rate: "" };
  return { hsn: key.slice(0, index), rate: key.slice(index + 1) };
}

function byKey<T extends { key: string }>(rows: T[]): T[] {
  return rows.sort((a, b) => a.key.localeCompare(b.key));
}

function fieldRows(record: Record<string, GstBucket | GstTaxTotals>, prefix = ""): GstRow[] {
  // Fixed fields keep the API's order — it is meaningful (taxable before zero-rated…).
  return Object.entries(record).map(([key, bucket]) => ({
    key,
    label: humanizeGstKey(key),
    field: prefix ? `${prefix}.${key}` : key,
    bucket,
  }));
}

function keyedRows(record: Record<string, GstBucket>, label: (key: string) => string): GstRow[] {
  return byKey(Object.entries(record).map(([key, bucket]) => ({ key, label: label(key), bucket })));
}

function hsnRows(record: Record<string, GstBucket>): GstRow[] {
  return byKey(
    Object.entries(record).map(([key, bucket]) => {
      const { hsn, rate } = splitHsnKey(key);
      return { key, label: hsn || "Not set", rate, bucket };
    }),
  );
}

const placeOfSupply = (key: string) => (key ? `State code ${key}` : "Not set");

export const GSTR1_KEYS = [
  "period",
  "b2b",
  "b2c_large",
  "exports",
  "b2c_small",
  "nil_exempt",
  "credit_debit_notes",
  "hsn_summary",
] as const;

export function gstr1Sections(summary: Gstr1Summary): GstSection[] {
  return [
    {
      path: "b2b",
      title: humanizeGstKey("b2b"),
      kind: "keyed",
      keyLabel: "Customer GSTIN",
      rows: keyedRows(summary.b2b, (key) => key || "Not set"),
    },
    {
      path: "b2c_large",
      title: humanizeGstKey("b2c_large"),
      kind: "fields",
      keyLabel: "Field",
      rows: fieldRows({ b2c_large: summary.b2c_large }),
    },
    {
      path: "exports",
      title: humanizeGstKey("exports"),
      kind: "keyed",
      keyLabel: "Export type",
      rows: keyedRows(summary.exports, humanizeGstKey),
    },
    {
      path: "b2c_small",
      title: humanizeGstKey("b2c_small"),
      kind: "keyed",
      keyLabel: "Place of supply",
      rows: keyedRows(summary.b2c_small, placeOfSupply),
    },
    {
      path: "nil_exempt",
      title: humanizeGstKey("nil_exempt"),
      kind: "fields",
      keyLabel: "Field",
      rows: fieldRows({ nil_exempt: summary.nil_exempt }),
    },
    {
      path: "credit_debit_notes",
      title: humanizeGstKey("credit_debit_notes"),
      kind: "keyed",
      keyLabel: "Customer",
      rows: keyedRows(summary.credit_debit_notes, humanizeGstKey),
    },
    {
      path: "hsn_summary.b2b",
      title: `${humanizeGstKey("hsn_summary")} · B2B`,
      kind: "hsn",
      keyLabel: "HSN/SAC",
      rows: hsnRows(summary.hsn_summary.b2b),
    },
    {
      path: "hsn_summary.b2c",
      title: `${humanizeGstKey("hsn_summary")} · B2C`,
      kind: "hsn",
      keyLabel: "HSN/SAC",
      rows: hsnRows(summary.hsn_summary.b2c),
    },
  ];
}

export const GSTR3B_KEYS = ["period", "outward", "inter_state_unregistered", "itc"] as const;

export function gstr3bSections(summary: Gstr3bSummary): GstSection[] {
  return [
    {
      path: "outward",
      title: humanizeGstKey("outward"),
      kind: "fields",
      keyLabel: "Field",
      rows: fieldRows(summary.outward, "outward"),
    },
    {
      path: "inter_state_unregistered",
      title: humanizeGstKey("inter_state_unregistered"),
      kind: "keyed",
      keyLabel: "Place of supply",
      rows: keyedRows(summary.inter_state_unregistered, placeOfSupply),
    },
    {
      path: "itc",
      title: humanizeGstKey("itc"),
      kind: "fields",
      keyLabel: "Field",
      rows: fieldRows(summary.itc, "itc"),
    },
  ];
}

export const GST_SUMMARY_KEYS = ["period", "output_tax", "input_tax", "gstr1", "gstr3b"] as const;

/** The GST summary's register totals: one row each for output and input tax. */
export function gstTaxTotalsSection(summary: GstSummary): GstSection {
  return {
    path: "output_tax, input_tax",
    title: "Output and input tax",
    kind: "fields",
    keyLabel: "Field",
    rows: fieldRows({ output_tax: summary.output_tax, input_tax: summary.input_tax }),
  };
}

/**
 * Top-level keys the API returned that this build does not render — so a
 * section added to the backend is announced on screen instead of silently
 * missing from a tax figure someone is relying on.
 */
export function unknownKeys(payload: object, known: readonly string[]): string[] {
  return Object.keys(payload).filter((key) => !known.includes(key));
}
