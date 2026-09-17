import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/errors";
import type { AiSource } from "@/types/api/ai";
import {
  AI_DISABLED_MESSAGE,
  fieldLabel,
  isAiDisabledError,
  sourceHref,
  sourceLocation,
  statusNote,
  toolLabel,
  warningText,
} from "./sources";

function source(overrides: Partial<AiSource>): AiSource {
  return {
    source_id: "s",
    type: "report",
    id: null,
    label: "Label",
    route: null,
    document_id: null,
    page: null,
    section: null,
    ...overrides,
  };
}

const INVOICE_ID = "c963842d-f02b-4320-9dff-7266637a52d6";

describe("isAiDisabledError", () => {
  it("treats the backend's disabled and unavailable envelopes as disabled", () => {
    expect(isAiDisabledError(new ApiError({ status: 403, code: "ai_disabled", message: "Ask Books is not enabled." }))).toBe(true);
    expect(isAiDisabledError(new ApiError({ status: 503, code: "ai_unavailable", message: "x" }))).toBe(true);
  });

  it("does not hide permission, rate-limit or validation errors behind it", () => {
    expect(isAiDisabledError(new ApiError({ status: 403, code: "forbidden", message: "x" }))).toBe(false);
    expect(isAiDisabledError(new ApiError({ status: 429, code: "rate_limit_exceeded", message: "x" }))).toBe(false);
    expect(isAiDisabledError(new ApiError({ status: 400, code: { question: ["required"] }, message: "x" }))).toBe(false);
    expect(isAiDisabledError(new Error("ai_disabled"))).toBe(false);
  });

  it("uses the exact product wording", () => {
    expect(AI_DISABLED_MESSAGE).toBe("AI Assistant is not currently enabled.");
  });
});

describe("sourceHref", () => {
  it("maps a report citation (live shape) to the app page, carrying only report params", () => {
    expect(
      sourceHref(
        source({
          id: "profit_and_loss",
          route: "/api/v1/reports/profit-loss/?from_date=2026-07-01&to_date=2026-09-17&evil=1",
        }),
      ),
    ).toBe("/reports/profit-loss?from_date=2026-07-01&to_date=2026-09-17");
    expect(sourceHref(source({ id: "trial_balance", route: "/api/v1/reports/trial-balance/?as_of_date=2026-09-01" }))).toBe(
      "/accounting/trial-balance?as_of_date=2026-09-01",
    );
    expect(sourceHref(source({ id: "gst_summary", route: "/api/v1/reports/tax/gst-summary/" }))).toBe("/tax/summary");
    expect(sourceHref(source({ id: "some_new_report" }))).toBe("/reports");
  });

  it("maps record and document citations by id, never by the API route", () => {
    expect(sourceHref(source({ type: "invoice", id: INVOICE_ID, route: `/api/v1/sales/invoices/${INVOICE_ID}/` }))).toBe(
      `/sales/invoices/${INVOICE_ID}`,
    );
    expect(sourceHref(source({ type: "bill", id: INVOICE_ID }))).toBe(`/purchases/bills/${INVOICE_ID}`);
    expect(sourceHref(source({ type: "document", id: INVOICE_ID, document_id: INVOICE_ID }))).toBe(
      `/documents/${INVOICE_ID}`,
    );
  });

  it("refuses ids that are not UUIDs and unknown source types", () => {
    expect(sourceHref(source({ type: "invoice", id: "../admin" }))).toBeNull();
    expect(sourceHref(source({ type: "chunk", id: INVOICE_ID }))).toBeNull();
  });
});

describe("labels", () => {
  it("describes locations, statuses, warnings, tools and fields", () => {
    expect(sourceLocation(source({ page: 3, section: "Terms" }))).toBe("page 3 · Terms");
    expect(sourceLocation(source({}))).toBeNull();
    expect(statusNote("answered")).toBeNull();
    expect(statusNote("partial")).toBe("Partial answer");
    expect(warningText("ungrounded_figures_replaced")).toContain("replaced with the report figures");
    expect(warningText("brand_new_code")).toBe("brand_new_code");
    expect(toolLabel("get_profit_and_loss")).toBe("Profit and loss");
    expect(fieldLabel("gross_profit")).toBe("Gross profit");
  });
});

describe("REPORT_ROUTES coverage", () => {
  it("links the ageing and inventory reports to the routes that exist", () => {
    expect(sourceHref(source({ id: "ar_ageing", route: "/api/v1/reports/receivables/ageing/?as_of_date=2026-09-01" }))).toBe(
      "/reports/receivables/ageing?as_of_date=2026-09-01",
    );
    expect(sourceHref(source({ id: "outstanding_bills" }))).toBe("/reports/payables/outstanding-bills");
    expect(sourceHref(source({ id: "low_stock" }))).toBe("/reports/inventory/low-stock");
    expect(sourceHref(source({ id: "project_profitability" }))).toBe("/reports/projects/profitability");
  });
});
