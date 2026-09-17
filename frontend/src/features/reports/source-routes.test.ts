import { describe, expect, it } from "vitest";
import { JOURNAL_SOURCE_TYPES } from "@/types/api/reports";
import { resolveSource } from "./source-routes";

describe("resolveSource", () => {
  it("links documents that have a page", () => {
    expect(resolveSource("sales.Invoice", "abc")).toEqual({ label: "Invoice", href: "/sales/invoices/abc" });
    expect(resolveSource("purchases.Bill.void", "b")).toEqual({ label: "Bill (void)", href: "/purchases/bills/b" });
    expect(resolveSource("stock_adjustment", "s")).toEqual({
      label: "Stock adjustment",
      href: "/inventory/adjustments/s",
    });
  });

  it("labels without linking where no page exists, and never guesses", () => {
    expect(resolveSource("opening_balance", "x")).toEqual({ label: "Opening balance", href: null });
    expect(resolveSource("something.New", "x")).toEqual({ label: "something.New", href: null });
    expect(resolveSource("", "")).toEqual({ label: "Manual", href: null });
  });

  it("has a human label for every journal source type offered as a filter", () => {
    for (const type of JOURNAL_SOURCE_TYPES) expect(resolveSource(type, null).label).not.toBe(type);
  });
});
