import type { DateTimeString, UUID } from "@/lib/api/types";

/**
 * Automation API contract.
 *
 * VERIFIED against backend/automation/api/serializers.py + views.py (serializer
 * dump) and live responses captured from the dev tenant on 2026-09-17:
 * GET/POST automation/rules/, PATCH rules/{id}/, POST rules/{id}/activate|pause|run/,
 * GET automation/executions/ and executions/{id}/, POST executions/{id}/retry/,
 * GET automation/catalog/triggers/ and catalog/actions/.
 *
 * The earlier hand-written version described catalog fields that do not exist
 * (trigger `fields`/`operators`/`description`, action `required_permission`)
 * and invented failure-category labels. The trigger catalog carries ONLY
 * id/label/category — see features/automation/contract.ts for the condition
 * allowlist the catalog does not expose.
 */

/** automation/models/rule.py :: RuleStatus */
export type RuleStatus = "draft" | "active" | "paused" | "archived";

/** automation.AutomationConditionSerializer — `value` is always a string, "" for is_empty/is_not_empty. */
export interface AutomationCondition {
  field: string;
  operator: string;
  value: string;
}

/**
 * automation.AutomationActionConfigSerializer.
 *
 * `config` is free-form per action (validated against the action's
 * `config_schema` on save). For `call_webhook` a set `secret` is returned as
 * the literal mask "********" — never the value, and never to be sent back.
 */
export interface AutomationActionConfig {
  action_id: string;
  config: Record<string, unknown>;
}

/** automation.AutomationRuleSerializer. Ordered `-priority, name`. */
export interface AutomationRule {
  id: UUID;
  name: string;
  description: string;
  trigger_type: string;
  status: RuleStatus;
  /** Incremented only by a structural edit (conditions or actions replaced). */
  version: number;
  priority: number;
  stop_on_failure: boolean;
  /** Rolling-24h cap on executions; null = unlimited. */
  max_runs_per_period: number | null;
  /** Scan-based triggers only; null = AUTOMATION_DEFAULT_COOLDOWN_DAYS. */
  cooldown_days: number | null;
  conditions: AutomationCondition[];
  actions: AutomationActionConfig[];
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** POST automation/rules/ — automation.AutomationRuleCreateSerializer. */
export interface AutomationRuleInput {
  name: string;
  description?: string;
  trigger_type: string;
  priority?: number;
  stop_on_failure?: boolean;
  max_runs_per_period?: number | null;
  cooldown_days?: number | null;
  conditions?: AutomationCondition[];
  /** At least one (automation_rule_no_actions). */
  actions: AutomationActionConfig[];
}

/**
 * PATCH automation/rules/{id}/ — automation.AutomationRuleUpdateSerializer.
 *
 * `trigger_type` is NOT accepted (silently ignored), so a rule's trigger is
 * fixed at creation. Sending `conditions` or `actions` REPLACES that whole
 * list and bumps `version` even when nothing changed. Archived rules reject
 * every edit (automation_rule_archived); draft, active and paused accept it.
 */
export interface AutomationRuleUpdateInput {
  name?: string;
  description?: string;
  priority?: number;
  stop_on_failure?: boolean;
  max_runs_per_period?: number | null;
  cooldown_days?: number | null;
  conditions?: AutomationCondition[];
  actions?: AutomationActionConfig[];
}

/** POST automation/rules/{id}/run/ — only ACTIVE rules (automation_rule_not_active). Body is optional. */
export interface RunRuleInput {
  entity_id?: string;
}

/** automation/models/execution.py :: ExecutionStatus */
export type ExecutionStatus = "pending" | "running" | "succeeded" | "partial" | "failed" | "cancelled";

/** automation/models/execution.py :: TriggerSource */
export type TriggerSource = "event" | "schedule" | "manual";

/** automation/models/step_execution.py :: StepStatus */
export type StepStatus = "pending" | "running" | "succeeded" | "failed" | "retrying" | "skipped" | "cancelled";

/** automation/models/step_execution.py :: FailureCategory — "" when the step has not failed. */
export type FailureCategory =
  | ""
  | "validation_error"
  | "permission_error"
  | "target_missing"
  | "transient_external_error"
  | "timeout"
  | "rate_limited"
  | "domain_conflict"
  | "safety_limit"
  | "unexpected_error";

/** automation.AutomationStepExecutionSerializer */
export interface AutomationStepExecution {
  id: UUID;
  order: number;
  action_id: string;
  /**
   * The action's config AS IT RAN. WARNING: returned unmasked by the API — a
   * `call_webhook` step carries its raw `secret` here (verified live). Always
   * pass through redactConfig() before rendering.
   */
  config_snapshot: Record<string, unknown>;
  status: StepStatus;
  attempt: number;
  failure_category: FailureCategory;
  error_message: string;
  result: Record<string, unknown>;
  started_at: DateTimeString | null;
  finished_at: DateTimeString | null;
}

/** automation.AutomationExecutionSerializer. Ordered newest first. */
export interface AutomationExecution {
  id: UUID;
  rule: UUID;
  rule_version: number;
  trigger_source: TriggerSource;
  /** "" for a run with no target record. */
  entity_id: string;
  status: ExecutionStatus;
  causation_id: UUID | null;
  correlation_id: UUID;
  depth: number;
  /** User id; null for event/schedule runs. */
  initiated_by: UUID | null;
  /** User id whose CURRENT permissions gate each step (the rule's creator). */
  executed_as: UUID | null;
  started_at: DateTimeString | null;
  finished_at: DateTimeString | null;
  attempt_count: number;
  error_summary: string;
  /** Empty until the background worker runs the execution. */
  steps: AutomationStepExecution[];
  created_at: DateTimeString;
}

/** automation/triggers/registry.py :: CATEGORY_* */
export type TriggerCategory = "event" | "schedule" | "manual";

/** GET automation/catalog/triggers/ — a bare array, sorted by id. */
export interface TriggerCatalogEntry {
  id: string;
  label: string;
  category: TriggerCategory;
}

/**
 * GET automation/catalog/actions/ — a bare array, sorted by id.
 *
 * `config_schema` maps each accepted config key to a type name; today every
 * declared type is "string". Which keys are REQUIRED is not exposed.
 * `allowed_trigger_types` empty means any trigger may use the action.
 */
export interface ActionCatalogEntry {
  id: string;
  label: string;
  safety_level: number;
  config_schema: Record<string, string>;
  allowed_trigger_types: string[];
}

/** automation/models/step_execution.py :: FailureCategory labels, verbatim. */
export const FAILURE_CATEGORY_LABELS: Record<string, string> = {
  validation_error: "Validation error",
  permission_error: "Permission error",
  target_missing: "Target missing",
  transient_external_error: "Transient external error",
  timeout: "Timeout",
  rate_limited: "Rate limited",
  domain_conflict: "Domain conflict",
  safety_limit: "Safety limit",
  unexpected_error: "Unexpected error",
};

/** automation/models/execution.py :: TriggerSource labels. */
export const TRIGGER_SOURCE_LABELS: Record<string, string> = {
  event: "Event",
  schedule: "Schedule",
  manual: "Manual",
};

/** automation/triggers/registry.py :: category, as shown to people. */
export const TRIGGER_CATEGORY_LABELS: Record<string, string> = {
  event: "When something happens",
  schedule: "On a schedule",
  manual: "Run by hand",
};
