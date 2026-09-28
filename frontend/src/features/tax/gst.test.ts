import { describe, expect, it } from "vitest";
import type { GstSummary } from "@/types/api/reports";
import {
  GST_SUMMARY_KEYS,
  GSTR1_KEYS,
  GSTR3B_KEYS,
  gstTaxTotalsSection,
  gstr1Sections,
  gstr3bSections,
  humanizeGstKey,
  splitHsnKey,
  unknownKeys,
} from "./gst";

const zero = { taxable_value: "0", cgst: "0", sgst: "0", igst: "0", cess: "0", document_count: 0 };

/** Captured live from GET /reports/tax/gst-summary/ (Sept 2026), plus a b2b entry. */
const SUMMARY: GstSummary = {
  period: { from_date: "2026-09-01", to_date: "2026-09-30" },
  output_tax: { taxable_value: "8899.99", cgst: "0.00", sgst: "0.00", igst: "0.00", cess: "0.00" },
  input_tax: { taxable_value: "0", cgst: "0", sgst: "0", igst: "0", cess: "0" },
  gstr1: {
    period: { from: "2026-09-01", to: "2026-09-30" },
    b2b: {
      "29ABCDE1234F1Z5": { ...zero, taxable_value: "100.00", document_count: 1 },
      "27ABCDE1234F1Z5": { ...zero, taxable_value: "50.00", document_count: 1 },
    },
    b2c_large: zero,
    exports: {},
    b2c_small: { "": { ...zero, taxable_value: "8899.99", cgst: "0.00", document_count: 2 } },
    nil_exempt: zero,
    credit_debit_notes: {},
    hsn_summary: { b2b: {}, b2c: { "|18.00": { ...zero, taxable_value: "8899.99", document_count: 3 } } },
  },
  gstr3b: {
    period: { from: "2026-09-01", to: "2026-09-30" },
    outward: {
      taxable: { ...zero, taxable_value: "8899.99", document_count: 2 },
      zero_rated: zero,
      nil_exempt: zero,
      inward_reverse_charge: zero,
    },
    inter_state_unregistered: {},
    itc: { all_other: zero, reverse_charge: zero },
  },
};

describe("humanizeGstKey / splitHsnKey", () => {
  it("keeps the API's field name, readable", () => {
    expect(humanizeGstKey("b2c_large")).toBe("B2C large");
    expect(humanizeGstKey("inward_reverse_charge")).toBe("Inward reverse charge");
    expect(humanizeGstKey("itc")).toBe("ITC");
    expect(humanizeGstKey("")).toBe("Not set");
  });

  it("splits HSN keys on the last pipe, allowing an empty code", () => {
    expect(splitHsnKey("|18.00")).toEqual({ hsn: "", rate: "18.00" });
    expect(splitHsnKey("8471|12.00")).toEqual({ hsn: "8471", rate: "12.00" });
    expect(splitHsnKey("8471")).toEqual({ hsn: "8471", rate: "" });
  });
});

describe("gstr1Sections", () => {
  it("renders every section of the payload, keyed maps sorted, buckets untouched", () => {
    const sections = gstr1Sections(SUMMARY.gstr1);
    expect(sections.map((section) => section.path)).toEqual([
      "b2b",
      "b2c_large",
      "exports",
      "b2c_small",
      "nil_exempt",
      "credit_debit_notes",
      "hsn_summary.b2b",
      "hsn_summary.b2c",
    ]);
    const b2b = sections[0];
    expect(b2b?.rows.map((row) => row.label)).toEqual(["27ABCDE1234F1Z5", "29ABCDE1234F1Z5"]);
    expect(b2b?.rows[1]?.bucket).toBe(SUMMARY.gstr1.b2b["29ABCDE1234F1Z5"]);
    expect(sections[3]?.rows[0]).toMatchObject({ key: "", label: "Not set" });
    expect(sections[7]?.rows[0]).toMatchObject({ label: "Not set", rate: "18.00" });
    expect(sections[1]?.rows[0]).toMatchObject({ label: "B2C large", field: "b2c_large" });
  });

  it("covers every top-level key the API returns", () => {
    expect(unknownKeys(SUMMARY.gstr1, GSTR1_KEYS)).toEqual([]);
    expect(unknownKeys({ ...SUMMARY.gstr1, amendments: {} }, GSTR1_KEYS)).toEqual(["amendments"]);
  });
});

describe("gstr3bSections", () => {
  it("keeps fixed fields in API order with their full path", () => {
    const [outward, interState, itc] = gstr3bSections(SUMMARY.gstr3b);
    expect(outward?.rows.map((row) => row.field)).toEqual([
      "outward.taxable",
      "outward.zero_rated",
      "outward.nil_exempt",
      "outward.inward_reverse_charge",
    ]);
    expect(interState?.rows).toEqual([]);
    expect(itc?.rows.map((row) => row.label)).toEqual(["All other", "Reverse charge"]);
    expect(unknownKeys(SUMMARY.gstr3b, GSTR3B_KEYS)).toEqual([]);
  });
});

describe("gstTaxTotalsSection", () => {
  it("shows the register totals as returned, with no document count", () => {
    const section = gstTaxTotalsSection(SUMMARY);
    expect(section.rows.map((row) => [row.field, row.bucket.taxable_value])).toEqual([
      ["output_tax", "8899.99"],
      ["input_tax", "0"],
    ]);
    expect(section.rows.some((row) => "document_count" in row.bucket)).toBe(false);
    expect(unknownKeys(SUMMARY, GST_SUMMARY_KEYS)).toEqual([]);
  });
});
