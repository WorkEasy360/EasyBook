import { describe, expect, it } from "vitest";
import type { ActionCatalogEntry, AutomationActionConfig } from "@/types/api/automation";
import {
  actionsChanged,
  buildActions,
  buildConditions,
  conditionsChanged,
  toActionRows,
  toOptionalCount,
} from "./rule-payload";

/** GET automation/catalog/actions/ as captured live on 2026-09-17. */
const CATALOG: ActionCatalogEntry[] = [
  {
    id: "call_webhook",
    label: "Call outbound webhook",
    safety_level: 1,
    config_schema: { url: "string", secret: "string" },
    allowed_trigger_types: [],
  },
  {
    id: "draft_payment_reminder",
    label: "Draft AI payment reminder (never sent automatically)",
    safety_level: 1,
    config_schema: {},
    allowed_trigger_types: ["invoice.overdue"],
  },
  {
    id: "generate_report",
    label: "Generate report",
    safety_level: 1,
    config_schema: { report_type: "string" },
    allowed_trigger_types: [],
  },
  {
    id: "send_notification",
    label: "Send in-app notification",
    safety_level: 1,
    config_schema: { message: "string", recipient_id: "string" },
    allowed_trigger_types: [],
  },
];

/** A webhook action as the API returns it once a secret is saved. */
const SAVED_WEBHOOK: AutomationActionConfig[] = [
  { action_id: "call_webhook", config: { url: "https://example.com/hook", secret: "********" } },
];

describe("toActionRows", () => {
  it("never seeds an input with the secret mask", () => {
    const [row] = toActionRows(SAVED_WEBHOOK);
    expect(row).toEqual({
      action_id: "call_webhook",
      config: { url: "https://example.com/hook", secret: "" },
      secretConfigured: true,
      removeSecret: false,
    });
  });
});

describe("buildActions", () => {
  it("omits blank optional keys and reports blank required ones", () => {
    const { actions, issues } = buildActions(
      [{ action_id: "send_notification", config: { message: "  ", recipient_id: "" }, secretConfigured: false, removeSecret: false }],
      CATALOG,
      { requireSecretDecision: false },
    );
    expect(actions).toEqual([{ action_id: "send_notification", config: {} }]);
    expect(issues).toEqual([{ index: 0, key: "message", message: "Message is required." }]);
  });

  it("keeps a message's own whitespace but trims plain inputs", () => {
    const { actions } = buildActions(
      [
        { action_id: "send_notification", config: { message: "Line one\nLine two" }, secretConfigured: false, removeSecret: false },
        { action_id: "call_webhook", config: { url: " https://example.com/hook " }, secretConfigured: false, removeSecret: false },
      ],
      CATALOG,
      { requireSecretDecision: false },
    );
    expect(actions).toEqual([
      { action_id: "send_notification", config: { message: "Line one\nLine two" } },
      { action_id: "call_webhook", config: { url: "https://example.com/hook" } },
    ]);
  });

  it("refuses to send the mask as a secret", () => {
    const { issues } = buildActions(
      [{ action_id: "call_webhook", config: { url: "https://example.com/hook", secret: "********" }, secretConfigured: true, removeSecret: false }],
      CATALOG,
      { requireSecretDecision: true },
    );
    expect(issues.map((issue) => issue.key)).toEqual(["secret"]);
  });

  it("demands a decision about a saved secret when the actions will be replaced", () => {
    const rows = toActionRows(SAVED_WEBHOOK);
    expect(buildActions(rows, CATALOG, { requireSecretDecision: true }).issues).toHaveLength(1);
    // Removing it or entering a new one is an explicit decision.
    expect(buildActions([{ ...rows[0]!, removeSecret: true }], CATALOG, { requireSecretDecision: true })).toEqual({
      actions: [{ action_id: "call_webhook", config: { url: "https://example.com/hook" } }],
      issues: [],
    });
    expect(
      buildActions([{ ...rows[0]!, config: { url: "https://example.com/hook", secret: "new-secret" } }], CATALOG, {
        requireSecretDecision: true,
      }).actions,
    ).toEqual([{ action_id: "call_webhook", config: { url: "https://example.com/hook", secret: "new-secret" } }]);
  });

  it("reports an action missing from the catalog", () => {
    const { issues } = buildActions(
      [{ action_id: "", config: {}, secretConfigured: false, removeSecret: false }],
      CATALOG,
      { requireSecretDecision: false },
    );
    expect(issues).toEqual([{ index: 0, key: null, message: "Choose an action." }]);
  });
});

describe("actionsChanged", () => {
  it("is false for an untouched saved webhook, so the secret is not lost", () => {
    expect(actionsChanged(SAVED_WEBHOOK, toActionRows(SAVED_WEBHOOK), CATALOG)).toBe(false);
  });

  it("is true when a setting, the order, a secret or the count changes", () => {
    const rows = toActionRows(SAVED_WEBHOOK);
    expect(actionsChanged(SAVED_WEBHOOK, [{ ...rows[0]!, config: { url: "https://example.org/hook", secret: "" } }], CATALOG)).toBe(true);
    expect(actionsChanged(SAVED_WEBHOOK, [{ ...rows[0]!, removeSecret: true }], CATALOG)).toBe(true);
    expect(actionsChanged(SAVED_WEBHOOK, [{ ...rows[0]!, config: { url: "https://example.com/hook", secret: "s" } }], CATALOG)).toBe(true);
    expect(actionsChanged(SAVED_WEBHOOK, [], CATALOG)).toBe(true);

    const two: AutomationActionConfig[] = [
      { action_id: "generate_report", config: { report_type: "overdue_invoices" } },
      { action_id: "send_notification", config: { message: "Hi" } },
    ];
    expect(actionsChanged(two, toActionRows(two), CATALOG)).toBe(false);
    expect(actionsChanged(two, toActionRows([...two].reverse()), CATALOG)).toBe(true);
  });

  it("treats a saved blank value like an absent key", () => {
    const saved: AutomationActionConfig[] = [{ action_id: "send_notification", config: { message: "Hi", recipient_id: "" } }];
    expect(actionsChanged(saved, toActionRows(saved), CATALOG)).toBe(false);
  });
});

describe("conditions", () => {
  it("drops the value for operators that take none and trims the rest", () => {
    expect(
      buildConditions([
        { field: "amount_due", operator: "greater_than", value: " 1000 " },
        { field: "customer_id", operator: "is_empty", value: "stale" },
      ]),
    ).toEqual([
      { field: "amount_due", operator: "greater_than", value: "1000" },
      { field: "customer_id", operator: "is_empty", value: "" },
    ]);
  });

  it("detects a change only when the list really differs (sending it bumps the version)", () => {
    const saved = [{ field: "amount_due", operator: "greater_than", value: "1000" }];
    expect(conditionsChanged(saved, buildConditions([{ field: "amount_due", operator: "greater_than", value: "1000 " }]))).toBe(false);
    expect(conditionsChanged(saved, buildConditions([{ field: "amount_due", operator: "greater_than", value: "999" }]))).toBe(true);
    expect(conditionsChanged(saved, [])).toBe(true);
  });
});

describe("toOptionalCount", () => {
  it("maps blank to null (no limit / default) and digits to a number", () => {
    expect(toOptionalCount("")).toBeNull();
    expect(toOptionalCount(" 5 ")).toBe(5);
  });
});
