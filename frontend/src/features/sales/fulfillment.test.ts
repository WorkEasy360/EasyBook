import { describe, expect, it } from "vitest";
import { fulfillmentByOrderLine, trimQuantity } from "./fulfillment";

/**
 * Cases follow sales/selectors.py :: get_fulfilled_quantity — only DISPATCHED
 * and DELIVERED challans count; drafts and cancelled ones do not.
 */

const order = {
  id: "order-1",
  lines: [
    { id: "ol-1", quantity: "3.0000" },
    { id: "ol-2", quantity: "0.5000" },
  ],
} as Parameters<typeof fulfillmentByOrderLine>[0];

function challan(id: string, status: string, lines: Array<[string | null, string]>, orderId = "order-1") {
  return {
    id,
    status,
    source_sales_order: orderId,
    lines: lines.map(([source, quantity]) => ({ source_order_line: source, quantity })),
  } as unknown as Parameters<typeof fulfillmentByOrderLine>[1][number];
}

describe("fulfillmentByOrderLine", () => {
  it("counts dispatched and delivered challans only", () => {
    const result = fulfillmentByOrderLine(order, [
      challan("c1", "dispatched", [["ol-1", "1.0000"]]),
      challan("c2", "delivered", [["ol-1", "0.5"]]),
      challan("c3", "cancelled", [["ol-1", "1"]]),
      challan("c4", "draft", [["ol-1", "0.25"]]),
    ]);
    expect(result.get("ol-1")).toEqual({
      ordered: "3.0000",
      fulfilled: "1.5000",
      remaining: "1.5000",
      inDrafts: "0.2500",
    });
    expect(result.get("ol-2")?.remaining).toBe("0.5000");
  });

  it("ignores challans for other orders and unlinked lines", () => {
    const result = fulfillmentByOrderLine(order, [
      challan("c1", "dispatched", [["ol-1", "2"]], "order-2"),
      challan("c2", "dispatched", [[null, "2"]]),
    ]);
    expect(result.get("ol-1")?.fulfilled).toBe("0.0000");
  });

  it("floors remaining at zero and keeps decimal precision", () => {
    const result = fulfillmentByOrderLine(order, [
      challan("c1", "dispatched", [["ol-2", "0.1"]]),
      challan("c2", "dispatched", [["ol-2", "0.2"]]),
      challan("c3", "delivered", [["ol-1", "4"]]),
    ]);
    // 0.5 − (0.1 + 0.2) is exactly 0.2, not 0.19999999999999998.
    expect(result.get("ol-2")?.remaining).toBe("0.2000");
    expect(result.get("ol-1")?.remaining).toBe("0.0000");
  });

  it("excludes the draft being edited from its own draft count", () => {
    const result = fulfillmentByOrderLine(order, [challan("self", "draft", [["ol-1", "2"]])], "self");
    expect(result.get("ol-1")?.inDrafts).toBe("0.0000");
  });
});

describe("trimQuantity", () => {
  it("drops the serializer's trailing zeros for a form value", () => {
    expect(trimQuantity("3.0000")).toBe("3");
    expect(trimQuantity("2.5000")).toBe("2.5");
  });
});
