import type {
  ActionCatalogEntry,
  AutomationActionConfig,
  AutomationCondition,
  AutomationRule,
} from "@/types/api/automation";
import {
  MASKED_SECRET,
  NO_VALUE_OPERATORS,
  configFieldSpec,
  isSecretConfigKey,
} from "./contract";

/**
 * Pure conversion between the rule builder's form state and the API bodies.
 *
 * Kept out of the component so the two contract hazards below are unit
 * tested rather than trusted:
 *
 *  1. Sending `conditions` or `actions` on PATCH replaces the whole list and
 *     bumps the rule's `version` even when nothing changed
 *     (services/rules.py :: update_rule). An edit that only renames a rule
 *     must not claim a new definition, so each list is sent only when it
 *     actually differs from what the server returned.
 *  2. A saved webhook secret comes back as "********". Sending that back
 *     would overwrite the real secret with the mask, and omitting it while
 *     sending `actions` deletes it. There is no "keep the existing secret"
 *     input, so a changed action list with a configured secret must carry a
 *     re-entered secret or an explicit removal.
 *     BACKEND CONTRACT BLOCKER: PATCH automation/rules/{id}/ needs a way to
 *     keep an existing webhook secret when `actions` is sent (e.g. treat an
 *     omitted or masked `secret` as "unchanged").
 */

export interface ConditionRow {
  field: string;
  operator: string;
  value: string;
}

export interface ActionRow {
  action_id: string;
  /** Every declared config key as text; JSON text for a non-"string" type. */
  config: Record<string, string>;
  /** The server holds a secret for this action (it answered with the mask). */
  secretConfigured: boolean;
  /** The person chose to drop the configured secret. */
  removeSecret: boolean;
}

export const EMPTY_CONDITION: ConditionRow = { field: "", operator: "", value: "" };

export function emptyActionRow(actionId = ""): ActionRow {
  return { action_id: actionId, config: {}, secretConfigured: false, removeSecret: false };
}

function configValueToText(value: unknown): string {
  if (value === undefined || value === null) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

export function toConditionRows(conditions: readonly AutomationCondition[]): ConditionRow[] {
  return conditions.map((condition) => ({
    field: condition.field,
    operator: condition.operator,
    value: condition.value,
  }));
}

export function toActionRows(actions: readonly AutomationActionConfig[]): ActionRow[] {
  return actions.map((action) => {
    const config: Record<string, string> = {};
    let secretConfigured = false;
    for (const [key, value] of Object.entries(action.config)) {
      if (isSecretConfigKey(action.action_id, key)) {
        // Never seed an input with the mask: it would be submitted as the secret.
        secretConfigured = typeof value === "string" && value !== "";
        config[key] = "";
      } else {
        config[key] = configValueToText(value);
      }
    }
    return { action_id: action.action_id, config, secretConfigured, removeSecret: false };
  });
}

/** API conditions: trimmed, with no value for the operators that take none. */
export function buildConditions(rows: readonly ConditionRow[]): AutomationCondition[] {
  return rows.map((row) => ({
    field: row.field,
    operator: row.operator,
    value: NO_VALUE_OPERATORS.has(row.operator) ? "" : row.value.trim(),
  }));
}

export interface ActionBuildIssue {
  index: number;
  key: string | null;
  message: string;
}

/**
 * API actions from the rows, driven by each action's catalog `config_schema`.
 * Blank values are omitted rather than sent as "": the registry accepts ""
 * for a required key, which would only fail later, when the rule runs.
 *
 * `requireSecretDecision` is set when the action list will be sent on an
 * edit — see hazard 2 above.
 */
export function buildActions(
  rows: readonly ActionRow[],
  catalog: readonly ActionCatalogEntry[],
  { requireSecretDecision }: { requireSecretDecision: boolean },
): { actions: AutomationActionConfig[]; issues: ActionBuildIssue[] } {
  const issues: ActionBuildIssue[] = [];
  const actions = rows.map((row, index) => {
    const entry = catalog.find((action) => action.id === row.action_id);
    const config: Record<string, unknown> = {};
    if (!entry) {
      issues.push({ index, key: null, message: "Choose an action." });
      return { action_id: row.action_id, config };
    }

    for (const [key, schemaType] of Object.entries(entry.config_schema)) {
      const spec = configFieldSpec(entry.id, key, schemaType);
      const raw = row.config[key] ?? "";
      const text = spec.kind === "secret" || spec.kind === "textarea" ? raw : raw.trim();

      if (spec.kind === "secret") {
        if (text !== "") {
          if (text === MASKED_SECRET) {
            issues.push({ index, key, message: "Enter the real secret, not the mask." });
          } else {
            config[key] = text;
          }
        } else if (row.secretConfigured && !row.removeSecret && requireSecretDecision) {
          issues.push({
            index,
            key,
            message:
              "Re-enter the signing secret, or choose to remove it. Saved secrets cannot be read back, so saving these actions without it would delete it.",
          });
        }
        continue;
      }

      if (text.trim() === "") {
        if (spec.required) issues.push({ index, key, message: `${spec.label} is required.` });
        continue;
      }

      if (spec.json) {
        try {
          config[key] = JSON.parse(text) as unknown;
        } catch {
          issues.push({ index, key, message: "Enter valid JSON." });
        }
        continue;
      }

      config[key] = text;
    }
    return { action_id: entry.id, config };
  });
  return { actions, issues };
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, entry]) => entry !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
    return `{${entries.map(([key, entry]) => `${JSON.stringify(key)}:${stableJson(entry)}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function conditionsChanged(
  original: readonly AutomationCondition[],
  next: readonly AutomationCondition[],
): boolean {
  return stableJson(original.map((c) => ({ ...c, value: c.value ?? "" }))) !== stableJson(next);
}

/**
 * Whether the edited actions differ from the saved ones. Secrets are compared
 * by intent (replaced or removed), since the saved value is never known.
 */
export function actionsChanged(
  original: readonly AutomationActionConfig[],
  rows: readonly ActionRow[],
  catalog: readonly ActionCatalogEntry[],
): boolean {
  if (original.length !== rows.length) return true;
  const { actions } = buildActions(rows, catalog, { requireSecretDecision: false });

  return rows.some((row, index) => {
    const saved = original[index];
    const next = actions[index];
    if (!saved || !next || saved.action_id !== next.action_id) return true;
    if (row.removeSecret || Object.entries(row.config).some(([key, value]) => isSecretConfigKey(row.action_id, key) && value !== "")) {
      return true;
    }
    // Blank values are never sent, so a saved "" is the same as an absent key.
    const strip = (config: Record<string, unknown>) =>
      Object.fromEntries(
        Object.entries(config).filter(([key, value]) => !isSecretConfigKey(saved.action_id, key) && value !== ""),
      );
    return stableJson(strip(saved.config)) !== stableJson(strip(next.config));
  });
}

/** Form-level values shared by create and edit. Numbers are typed as text. */
export interface RuleSettingsValues {
  priority: string;
  cooldown_days: string;
  max_runs_per_period: string;
}

/** "" -> null for the nullable limits; the schema has already checked the digits. */
export function toOptionalCount(value: string): number | null {
  const trimmed = value.trim();
  return trimmed === "" ? null : Number(trimmed);
}

export function ruleSettingsFrom(rule: AutomationRule | undefined): RuleSettingsValues {
  return {
    priority: String(rule?.priority ?? 0),
    cooldown_days: rule?.cooldown_days === null || rule?.cooldown_days === undefined ? "" : String(rule.cooldown_days),
    max_runs_per_period:
      rule?.max_runs_per_period === null || rule?.max_runs_per_period === undefined
        ? ""
        : String(rule.max_runs_per_period),
  };
}
