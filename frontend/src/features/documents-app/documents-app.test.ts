import { describe, expect, it } from "vitest";
import { precheckUpload } from "./upload-rules";
import { rowsToFields, seedRows } from "./corrected-fields";
import { grantTarget } from "./download";
import { LINKABLE_ENTITIES, entityLabel, pickableEntityTypes } from "./entity-links";

describe("precheckUpload (mirrors documents/services/validation.py)", () => {
  it("accepts an allowed file whose declared type matches its extension", () => {
    expect(precheckUpload({ name: "receipt.PDF", size: 1200, type: "application/pdf" })).toEqual({
      ok: true,
      extension: ".pdf",
      mime: "application/pdf",
    });
    expect(precheckUpload({ name: "scan.jpeg", size: 10, type: "image/jpeg" }).ok).toBe(true);
  });

  it("refuses an extension outside the allowlist", () => {
    const result = precheckUpload({ name: "payload.exe", size: 10, type: "application/x-msdownload" });
    expect(result).toMatchObject({ ok: false });
    expect(!result.ok && result.message).toContain(".exe is not allowed");
    expect(precheckUpload({ name: "noextension", size: 10, type: "" })).toMatchObject({ ok: false });
  });

  it("refuses an empty file", () => {
    expect(precheckUpload({ name: "a.txt", size: 0, type: "text/plain" })).toEqual({
      ok: false,
      message: "This file is empty.",
    });
  });

  it("applies the per-category size ceilings (pdf 25 MB, image 15 MB, other 10 MB)", () => {
    const mb = 1024 * 1024;
    expect(precheckUpload({ name: "a.pdf", size: 25 * mb, type: "application/pdf" }).ok).toBe(true);
    expect(precheckUpload({ name: "a.pdf", size: 25 * mb + 1, type: "application/pdf" }).ok).toBe(false);
    expect(precheckUpload({ name: "a.png", size: 15 * mb + 1, type: "image/png" }).ok).toBe(false);
    expect(precheckUpload({ name: "a.csv", size: 10 * mb + 1, type: "text/csv" }).ok).toBe(false);
  });

  it("refuses a declared type the server would reject as mime_mismatch, including an empty one", () => {
    const excel = precheckUpload({ name: "bank.csv", size: 10, type: "application/vnd.ms-excel" });
    expect(!excel.ok && excel.message).toContain("must be text/csv");
    // An empty File.type goes over the wire as application/octet-stream.
    const unknown = precheckUpload({ name: "notes.txt", size: 10, type: "" });
    expect(!unknown.ok && unknown.message).toContain("application/octet-stream");
  });
});

describe("corrected fields editor", () => {
  it("seeds text rows from a provider payload without numeric conversion", () => {
    expect(seedRows({ total: "1180.50", pages: 2, paid: false, vendor: null, lines: [{ a: 1 }] })).toEqual([
      { key: "total", value: "1180.50" },
      { key: "pages", value: "2" },
      { key: "paid", value: "false" },
      { key: "vendor", value: "" },
      { key: "lines", value: '[{"a":1}]' },
    ]);
    expect(seedRows(null)).toEqual([]);
  });

  it("builds corrected_fields, skipping blank rows and trimming names", () => {
    expect(
      rowsToFields([
        { key: " invoice_number ", value: "INV-9" },
        { key: "", value: "  " },
        { key: "total", value: "0.10" },
      ]),
    ).toEqual({ ok: true, fields: { invoice_number: "INV-9", total: "0.10" } });
  });

  it("rejects a value without a name and duplicate names", () => {
    expect(rowsToFields([{ key: "", value: "x" }])).toEqual({ ok: false, message: "Field 1 has a value but no name." });
    expect(
      rowsToFields([
        { key: "total", value: "1" },
        { key: "total ", value: "2" },
      ]),
    ).toEqual({ ok: false, message: 'The field "total" appears more than once.' });
  });
});

describe("grantTarget", () => {
  it("routes a local-storage Django path through the BFF without the trailing slash", () => {
    expect(grantTarget("/api/v1/documents/local-storage/eyJr:1x77:abc/")).toEqual({
      kind: "same-origin",
      href: "/api/bff/documents/local-storage/eyJr:1x77:abc",
    });
  });

  it("opens an absolute presigned URL as-is", () => {
    expect(grantTarget("https://bucket.s3.amazonaws.com/k?X-Amz-Signature=1")).toEqual({
      kind: "external",
      href: "https://bucket.s3.amazonaws.com/k?X-Amz-Signature=1",
    });
  });

  it("refuses anything else", () => {
    expect(grantTarget("javascript:alert(1)")).toBeNull();
    expect(grantTarget("/somewhere/else")).toBeNull();
    expect(grantTarget("/api/v1/documents/../../admin/")).toBeNull();
  });
});

describe("linkable entities", () => {
  it("offers every backend entity type that has an API, and not e-Invoice/e-Way Bill", () => {
    const types = pickableEntityTypes();
    expect(types).toHaveLength(10);
    expect(types).not.toContain("einvoice");
    expect(types).not.toContain("ewaybill");
  });

  it("links to the module routes and describes records by their number", () => {
    expect(LINKABLE_ENTITIES.journal_entry.href?.("j1")).toBe("/accounting/journals/j1");
    expect(LINKABLE_ENTITIES.bank_transaction.href?.("t1")).toBe("/banking/transactions/t1");
    expect(LINKABLE_ENTITIES.invoice.describe?.({ invoice_number: "INV-0001" })).toBe("INV-0001");
    expect(LINKABLE_ENTITIES.bill.describe?.({ bill_number: "", vendor_bill_number: "V-77" })).toBe("V-77");
    expect(entityLabel("ewaybill")).toBe("e-Way Bill");
    expect(entityLabel("unknown_kind")).toBe("unknown_kind");
  });
});
