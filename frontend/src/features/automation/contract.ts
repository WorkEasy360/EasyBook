import type { ActionCatalogEntry, TriggerCatalogEntry } from "@/types/api/automation";

/**
 * What the automation catalogs do NOT tell the UI, transcribed from the backend.
 *
 * GET automation/catalog/triggers/ returns only {id, label, category} and
 * GET automation/catalog/actions/ returns {id, label, safety_level,
 * config_schema, allowed_trigger_types}. The builder still needs:
 *   - which record fields a condition may reference per trigger, and their
 *     comparison type        -> automation/conditions/schemas.py :: TRIGGER_FIELDS
 *   - the operator set and which operators suit which field type
 *                            -> automation/conditions/operators.py
 *   - which action config keys are required
 *                            -> automation/actions/registry.py :: required_config_keys
 *   - the report types generate_report can build
 *                            -> automation/actions/handlers.py :: _REPORT_BUILDERS
 *
 * BACKEND CONTRACT BLOCKER: GET automation/catalog/triggers/ should expose each
 * trigger's condition fields ({name, type}) and the operator list, and
 * GET automation/catalog/actions/ should expose required config keys and enum
 * choices (report_type). Until then this file mirrors those allowlists. The
 * mirror only shapes the form: the backend re-validates every condition and
 * action on save and on activation, and its message is shown when it disagrees.
 * Everything the catalogs DO expose (triggers, actions, config keys, trigger
 * compatibility) is read from the live catalogs, never from here.
 */

// ---------------------------------------------------------------------------
// Conditions
// ---------------------------------------------------------------------------

/** automation/conditions/schemas.py :: FIELD_TYPE_* */
export type ConditionFieldType = "decimal" | "string" | "int" | "date";

export interface ConditionFieldSpec {
  type: ConditionFieldType;
  label: string;
  /** An amount in the organization's currency (shown with its symbol). */
  money?: boolean;
  /** The value is a record id that a picker can choose for equals/not_equals. */
  picker?: "customer" | "vendor" | "item";
  hint?: string;
}

const AMOUNT_DUE: ConditionFieldSpec = { type: "decimal", label: "Amount due", money: true };
const CUSTOMER: ConditionFieldSpec = { type: "string", label: "Customer", picker: "customer" };
const VENDOR: ConditionFieldSpec = { type: "string", label: "Vendor", picker: "vendor" };

/** automation/conditions/schemas.py :: TRIGGER_FIELDS (labels added here). */
export const TRIGGER_FIELDS: Record<string, Record<string, ConditionFieldSpec>> = {
  "invoice.posted": {
    amount_due: AMOUNT_DUE,
    status: { type: "string", label: "Invoice status", hint: "A status value such as sent." },
    customer_id: CUSTOMER,
  },
  "invoice.overdue": {
    amount_due: AMOUNT_DUE,
    days_overdue: { type: "int", label: "Days overdue" },
    customer_id: CUSTOMER,
  },
  "invoice.paid": { amount_due: AMOUNT_DUE, customer_id: CUSTOMER },
  "bill.posted": { amount_due: AMOUNT_DUE, vendor_id: VENDOR },
  "bill.overdue": {
    amount_due: AMOUNT_DUE,
    days_until_due: { type: "int", label: "Days until due" },
    vendor_id: VENDOR,
  },
  "payment.received": { amount: { type: "decimal", label: "Amount", money: true }, customer_id: CUSTOMER },
  "stock.low": {
    quantity_on_hand: { type: "decimal", label: "Quantity on hand" },
    reorder_level: { type: "decimal", label: "Reorder level" },
    item_id: { type: "string", label: "Item", picker: "item" },
  },
  "document.ocr_completed": {
    ocr_status: { type: "string", label: "OCR status" },
    document_id: { type: "string", label: "Document id" },
  },
  "schedule.daily": {},
  "schedule.weekly": {},
  "schedule.monthly": {},
  manual: {},
};

/** Undefined when this build does not know the trigger (a newer backend). */
export function conditionFieldsFor(triggerType: string): Record<string, ConditionFieldSpec> | undefined {
  return Object.hasOwn(TRIGGER_FIELDS, triggerType) ? TRIGGER_FIELDS[triggerType] : undefined;
}

/** automation/conditions/operators.py :: Operator, in the order people read them. */
export const OPERATOR_LABELS: Record<string, string> = {
  equals: "equals",
  not_equals: "does not equal",
  greater_than: "is greater than",
  greater_than_or_equal: "is at least",
  less_than: "is less than",
  less_than_or_equal: "is at most",
  contains: "contains",
  in: "is one of",
  is_empty: "is empty",
  is_not_empty: "is not empty",
};

/** operators.py :: NO_VALUE_OPERATORS */
export const NO_VALUE_OPERATORS: ReadonlySet<string> = new Set(["is_empty", "is_not_empty"]);
/** operators.py :: ORDERED_OPERATORS — numbers and dates only. */
export const ORDERED_OPERATORS: ReadonlySet<string> = new Set([
  "greater_than",
  "greater_than_or_equal",
  "less_than",
  "less_than_or_equal",
]);
/** operators.py :: TEXT_ONLY_OPERATORS — string fields only. */
export const TEXT_ONLY_OPERATORS: ReadonlySet<string> = new Set(["contains", "in"]);

/**
 * Operators schemas.py :: validate_condition accepts for a field type. An
 * unknown type (a field this build does not know) gets every operator and
 * the backend decides.
 */
export function operatorsFor(type: ConditionFieldType | undefined): string[] {
  return Object.keys(OPERATOR_LABELS).filter((operator) => operatorAllowed(type, operator));
}

export function operatorAllowed(type: ConditionFieldType | undefined, operator: string): boolean {
  if (!Object.hasOwn(OPERATOR_LABELS, operator)) return false;
  if (type === undefined || NO_VALUE_OPERATORS.has(operator)) return true;
  if (ORDERED_OPERATORS.has(operator)) return type === "decimal" || type === "int" || type === "date";
  if (TEXT_ONLY_OPERATORS.has(operator)) return type === "string";
  return true;
}

const DECIMAL = /^[+-]?(\d+(\.\d*)?|\.\d+)$/;
const INTEGER = /^[+-]?\d+$/;
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Mirrors schemas.py :: validate_condition's value checks (a value is required
 * unless the operator takes none; typed per field unless the operator is
 * text-only). Slightly stricter than Python's Decimal()/int() — no exponent,
 * NaN or digit separators — which only ever rejects input a person would not
 * mean as a threshold. Returns a message, or null when the value is fine.
 */
export function conditionValueError(
  type: ConditionFieldType | undefined,
  operator: string,
  value: string,
): string | null {
  if (NO_VALUE_OPERATORS.has(operator)) return null;
  const trimmed = value.trim();
  if (trimmed === "") return "Enter a value.";
  if (type === undefined || TEXT_ONLY_OPERATORS.has(operator)) return null;
  if (type === "decimal" && !DECIMAL.test(trimmed)) return "Enter a number.";
  if (type === "int" && !INTEGER.test(trimmed)) return "Enter a whole number.";
  if (type === "date" && !ISO_DATE.test(trimmed)) return "Enter a date as YYYY-MM-DD.";
  return null;
}

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

/** The literal automation/api/serializers.py returns in place of a set webhook secret. */
export const MASKED_SECRET = "********";

export type ConfigFieldKind = "text" | "textarea" | "url" | "secret" | "member" | "choice";

export interface ConfigFieldSpec {
  label: string;
  kind: ConfigFieldKind;
  required: boolean;
  hint?: string;
  options?: Array<{ value: string; label: string }>;
}

/**
 * Presentation for the config keys the default catalog declares today.
 * `required` mirrors registry.py :: required_config_keys; `options` mirrors
 * handlers.py :: _REPORT_BUILDERS (the registry only checks report_type is a
 * string, so a typo would otherwise fail only when the rule runs).
 */
const KNOWN_CONFIG_FIELDS: Record<string, Record<string, ConfigFieldSpec>> = {
  send_notification: {
    message: { label: "Message", kind: "textarea", required: true, hint: "Shown in the recipient's in-app notifications." },
    recipient_id: {
      label: "Recipient",
      kind: "member",
      required: false,
      hint: "Leave blank to notify the person who created the rule.",
    },
  },
  generate_report: {
    report_type: {
      label: "Report",
      kind: "choice",
      required: true,
      options: [
        { value: "overdue_invoices", label: "Overdue invoices" },
        { value: "low_stock_items", label: "Low stock items" },
      ],
    },
  },
  call_webhook: {
    url: { label: "URL", kind: "url", required: true, hint: "HTTPS only. Private and internal addresses are refused." },
    secret: {
      label: "Signing secret",
      kind: "secret",
      required: false,
      hint: "Signs each delivery with HMAC-SHA256. It is never shown again once saved.",
    },
  },
};

function humanize(key: string): string {
  const words = key.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * How to render one declared config key. A key this build has no
 * presentation for is a plain text input when its declared type is "string"
 * — the only type the registry checks — and a JSON textarea for any other
 * declared type, so a newer backend's action is still configurable.
 */
export function configFieldSpec(
  actionId: string,
  key: string,
  schemaType: string,
): ConfigFieldSpec & { json: boolean } {
  const known = KNOWN_CONFIG_FIELDS[actionId]?.[key];
  if (known && schemaType === "string") return { ...known, json: false };
  if (schemaType === "string") return { label: humanize(key), kind: "text", required: false, json: false };
  return {
    label: humanize(key),
    kind: "textarea",
    required: false,
    json: true,
    hint: `Enter JSON (declared type: ${schemaType}).`,
  };
}

export function isSecretConfigKey(actionId: string, key: string): boolean {
  return KNOWN_CONFIG_FIELDS[actionId]?.[key]?.kind === "secret";
}

/** Choice label for a known enum-like config value, else the value itself. */
export function configChoiceLabel(actionId: string, key: string, value: string): string {
  return KNOWN_CONFIG_FIELDS[actionId]?.[key]?.options?.find((option) => option.value === value)?.label ?? value;
}

/**
 * A config (or an execution's config_snapshot) with every secret replaced by
 * a neutral marker. The execution API returns webhook secrets UNMASKED in
 * `steps[].config_snapshot`, so nothing that renders a config may skip this.
 */
export function redactConfig(actionId: string, config: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(config)) {
    out[key] = isSecretConfigKey(actionId, key) || key.toLowerCase().includes("secret")
      ? value
        ? "(configured, not shown)"
        : ""
      : value;
  }
  return out;
}

/** Actions the catalog allows for this trigger (`allowed_trigger_types` empty = any). */
export function actionAllowedForTrigger(action: ActionCatalogEntry, triggerType: string): boolean {
  return action.allowed_trigger_types.length === 0 || action.allowed_trigger_types.includes(triggerType);
}

/**
 * call_webhook needs automation.manage_webhooks on top of create/edit
 * (services/rules.py :: _assert_actions_authorized), so it is not offered to
 * a role that would be refused.
 */
export const WEBHOOK_ACTION_ID = "call_webhook";

// ---------------------------------------------------------------------------
// Catalog lookups
// ---------------------------------------------------------------------------

export function triggerLabel(triggers: readonly TriggerCatalogEntry[], id: string): string {
  return triggers.find((trigger) => trigger.id === id)?.label ?? id;
}

export function actionLabel(actions: readonly ActionCatalogEntry[], id: string): string {
  return actions.find((action) => action.id === id)?.label ?? id;
}

/**
 * Triggers the backend accepts on a rule but never fires yet: nothing emits
 * or scans for them (receivers.py wires only invoice.posted;
 * services/scheduling.py only the schedule.* and invoice.overdue/stock.low
 * scans — see automation/CLAUDE.md, DEFERRED). A rule on one of these can be
 * activated and run by hand, but will not run by itself, and saying so is
 * the honest thing to show.
 */
export const TRIGGERS_WITHOUT_DISPATCH: ReadonlySet<string> = new Set([
  "invoice.paid",
  "bill.posted",
  "bill.overdue",
  "payment.received",
  "document.ocr_completed",
]);

/**
 * Scan-based triggers, the only ones `cooldown_days` applies to
 * (automation/models/rule.py; services/scheduling.py).
 */
export const COOLDOWN_TRIGGERS: ReadonlySet<string> = new Set(["invoice.overdue", "stock.low"]);

/** Pretty JSON for read-only display. Rendered as TEXT, never as HTML. */
export function formatJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? "";
  } catch {
    return String(value);
  }
}

/**
 * What a manual run of a record-based trigger is evaluated against
 * (triggers/facts.py loads the record by `entity_id`). Null for triggers with
 * no record fields (schedules, manual), which ignore `entity_id`.
 *
 * The id must be a well-formed UUID: facts.py filters `pk=entity_id`, so a
 * blank or malformed id raises inside the background task and leaves the
 * execution pending forever (verified with a manual run of an invoice.posted
 * rule and no entity_id).
 */
export function entityLabelFor(triggerType: string): string | null {
  const fields = conditionFieldsFor(triggerType);
  if (!fields || Object.keys(fields).length === 0) return null;
  if (triggerType.startsWith("invoice.")) return "Invoice id";
  if (triggerType.startsWith("bill.")) return "Bill id";
  if (triggerType === "payment.received") return "Payment id";
  if (triggerType === "stock.low") return "Item id";
  if (triggerType.startsWith("document.")) return "Document id";
  return "Record id";
}

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * GET automation/executions/?rule=<not a uuid> answers 500 (the view passes
 * the raw string to a UUID filter), so only a well-formed id is forwarded.
 */
export function isUuid(value: string | undefined): value is string {
  return value !== undefined && UUID_PATTERN.test(value);
}
