import { describe, expect, it } from "vitest";
import {
  conditionValueError,
  configFieldSpec,
  entityLabelFor,
  isUuid,
  operatorAllowed,
  operatorsFor,
  redactConfig,
} from "./contract";

/**
 * Cases taken from backend/automation/conditions/schemas.py ::
 * validate_condition and the live API behaviour recorded in contract.ts, so a
 * drift between the form and the server's validation shows up here.
 */

describe("operatorAllowed", () => {
  it("allows ordered operators only on numbers and dates", () => {
    expect(operatorAllowed("decimal", "greater_than")).toBe(true);
    expect(operatorAllowed("int", "less_than_or_equal")).toBe(true);
    expect(operatorAllowed("date", "less_than")).toBe(true);
    // Live: "Operator 'greater_than' cannot be used on field 'customer_id'."
    expect(operatorAllowed("string", "greater_than")).toBe(false);
  });

  it("allows contains and in only on strings", () => {
    expect(operatorAllowed("string", "contains")).toBe(true);
    expect(operatorAllowed("string", "in")).toBe(true);
    // Live: "Operator 'contains' cannot be used on field 'amount_due'."
    expect(operatorAllowed("decimal", "contains")).toBe(false);
  });

  it("allows the empty checks and equality on every type, and nothing unknown", () => {
    for (const type of ["decimal", "string", "int", "date"] as const) {
      expect(operatorAllowed(type, "is_empty")).toBe(true);
      expect(operatorAllowed(type, "not_equals")).toBe(true);
    }
    expect(operatorAllowed("string", "matches_regex")).toBe(false);
    expect(operatorAllowed(undefined, "greater_than")).toBe(true);
  });

  it("lists operators per type", () => {
    expect(operatorsFor("string")).toEqual(["equals", "not_equals", "contains", "in", "is_empty", "is_not_empty"]);
    expect(operatorsFor("decimal")).not.toContain("in");
  });
});

describe("conditionValueError", () => {
  it("requires a value unless the operator takes none", () => {
    expect(conditionValueError("decimal", "equals", "  ")).toBe("Enter a value.");
    expect(conditionValueError("decimal", "is_empty", "")).toBeNull();
  });

  it("type-checks decimals, integers and dates", () => {
    expect(conditionValueError("decimal", "greater_than", "1500.50")).toBeNull();
    expect(conditionValueError("decimal", "greater_than", "-.5")).toBeNull();
    expect(conditionValueError("decimal", "greater_than", "1,500")).toBe("Enter a number.");
    expect(conditionValueError("int", "greater_than_or_equal", "30")).toBeNull();
    expect(conditionValueError("int", "greater_than_or_equal", "3.5")).toBe("Enter a whole number.");
    expect(conditionValueError("date", "less_than", "2026-03-31")).toBeNull();
    expect(conditionValueError("date", "less_than", "31/03/2026")).toBe("Enter a date as YYYY-MM-DD.");
  });

  it("does not type-check text-only operators or unknown fields", () => {
    expect(conditionValueError("string", "in", "sent, draft")).toBeNull();
    expect(conditionValueError(undefined, "equals", "anything")).toBeNull();
  });
});

describe("configFieldSpec", () => {
  it("presents known keys and marks the registry's required ones", () => {
    expect(configFieldSpec("send_notification", "message", "string")).toMatchObject({ kind: "textarea", required: true });
    expect(configFieldSpec("send_notification", "recipient_id", "string")).toMatchObject({ kind: "member", required: false });
    expect(configFieldSpec("call_webhook", "secret", "string")).toMatchObject({ kind: "secret" });
    expect(configFieldSpec("generate_report", "report_type", "string").options?.map((o) => o.value)).toEqual([
      "overdue_invoices",
      "low_stock_items",
    ]);
  });

  it("falls back to text for an unknown string key and JSON for any other declared type", () => {
    expect(configFieldSpec("future_action", "channel_name", "string")).toMatchObject({
      kind: "text",
      label: "Channel name",
      json: false,
    });
    expect(configFieldSpec("future_action", "headers", "object")).toMatchObject({ kind: "textarea", json: true });
  });
});

describe("redactConfig", () => {
  it("never passes a webhook secret through, masked or raw", () => {
    // Live GET automation/executions/{id}/ returned the raw secret in config_snapshot.
    expect(redactConfig("call_webhook", { url: "https://example.com/hook", secret: "probe-secret-value" })).toEqual({
      url: "https://example.com/hook",
      secret: "(configured, not shown)",
    });
    expect(redactConfig("call_webhook", { url: "https://example.com/hook", secret: "" })).toEqual({
      url: "https://example.com/hook",
      secret: "",
    });
  });

  it("also hides any secret-like key on an action this build does not know", () => {
    expect(redactConfig("future_action", { api_secret: "x", label: "y" })).toEqual({
      api_secret: "(configured, not shown)",
      label: "y",
    });
  });
});

describe("entityLabelFor / isUuid", () => {
  it("asks for a record only on record-based triggers", () => {
    expect(entityLabelFor("invoice.posted")).toBe("Invoice id");
    expect(entityLabelFor("stock.low")).toBe("Item id");
    expect(entityLabelFor("manual")).toBeNull();
    expect(entityLabelFor("schedule.daily")).toBeNull();
    expect(entityLabelFor("not.a.trigger")).toBeNull();
  });

  it("accepts only well-formed UUIDs", () => {
    expect(isUuid("c963842d-f02b-4320-9dff-7266637a52d6")).toBe(true);
    expect(isUuid("")).toBe(false);
    expect(isUuid("bogus")).toBe(false);
    expect(isUuid(undefined)).toBe(false);
  });
});
