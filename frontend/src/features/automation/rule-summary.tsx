import type { ReactNode } from "react";
import { DataTable, type Column } from "@/components/ui/data-table";
import { DetailList } from "@/components/ui/detail";
import { wholeList } from "@/lib/list-query";
import type { ActionCatalogEntry, AutomationActionConfig, AutomationCondition } from "@/types/api/automation";
import {
  NO_VALUE_OPERATORS,
  OPERATOR_LABELS,
  conditionFieldsFor,
  configChoiceLabel,
  configFieldSpec,
  formatJson,
  isSecretConfigKey,
  redactConfig,
} from "./contract";

/**
 * Read-only presentation of a rule's definition and of execution payloads.
 * No "use client": rendered on the server, so a config never reaches the
 * browser except as the text shown here, with secrets already replaced.
 */

/** JSON shown as TEXT in a <pre> — React escapes it; nothing is ever parsed as HTML. */
export function JsonBlock({ value, label }: { value: unknown; label: string }) {
  const text = formatJson(value);
  const empty = text === "{}" || text === "[]" || text === "";
  return (
    <figure className="flex flex-col gap-1">
      <figcaption className="text-xs font-medium text-ink-600">{label}</figcaption>
      {empty ? (
        <p className="text-sm text-ink-400">Empty</p>
      ) : (
        <pre className="max-h-80 overflow-auto rounded-md border border-ink-200 bg-ink-50 p-3 font-mono text-xs whitespace-pre-wrap break-all text-ink-800">
          {text}
        </pre>
      )}
    </figure>
  );
}

export function ConditionsTable({
  conditions,
  triggerType,
  recordNames,
  caption,
  selfHref,
}: {
  conditions: AutomationCondition[];
  triggerType: string;
  /** Names for id-valued conditions (customer, vendor, item), when resolvable. */
  recordNames: Map<string, string>;
  caption: string;
  selfHref: string;
}) {
  const fields = conditionFieldsFor(triggerType);
  const rows = conditions.map((condition, index) => ({ ...condition, index }));

  const columns: Column<(typeof rows)[number]>[] = [
    { key: "field", header: "Field", cell: (row) => fields?.[row.field]?.label ?? row.field },
    { key: "operator", header: "Comparison", cell: (row) => OPERATOR_LABELS[row.operator] ?? row.operator },
    {
      key: "value",
      header: "Value",
      cell: (row) => {
        if (NO_VALUE_OPERATORS.has(row.operator)) return <span className="text-ink-400">—</span>;
        const spec = fields?.[row.field];
        if (spec?.picker) {
          const name = recordNames.get(row.value);
          if (name) return name;
          if (row.operator === "equals" || row.operator === "not_equals") {
            return <span className="text-ink-500">A selected {spec.picker}</span>;
          }
        }
        return <span className="whitespace-pre-wrap break-all tabular">{row.value}</span>;
      },
    },
  ];

  return (
    <DataTable
      caption={caption}
      columns={columns}
      data={wholeList(rows)}
      getRowId={(row) => String(row.index)}
      emptyTitle="No conditions"
      emptyDescription="The rule runs every time its trigger fires."
      page={1}
      pageSize={Math.max(rows.length, 1)}
      buildPageHref={() => selfHref}
    />
  );
}

/** One action's settings, labelled from the catalog schema; secrets never shown. */
export function ActionSettings({
  action,
  catalog,
  memberName,
}: {
  action: AutomationActionConfig;
  catalog: readonly ActionCatalogEntry[];
  memberName: (userId: string) => string;
}) {
  const entry = catalog.find((candidate) => candidate.id === action.action_id);
  const config = redactConfig(action.action_id, action.config);
  const keys = entry ? Object.keys(entry.config_schema) : Object.keys(config);

  if (keys.length === 0) return <p className="text-sm text-ink-500">No settings.</p>;

  return (
    <DetailList
      columns={1}
      items={keys.map((key) => {
        const spec = configFieldSpec(action.action_id, key, entry?.config_schema[key] ?? "string");
        // Read from the redacted copy, so even an unrecognised secret-like key is never printed.
        const raw = config[key];
        let value: ReactNode;
        if (isSecretConfigKey(action.action_id, key)) {
          // The API masks a saved webhook secret; show only whether one exists.
          value = raw ? "Configured" : "Not set";
        } else if (raw === undefined || raw === "") {
          value = spec.kind === "member" ? "Rule creator" : <span className="text-ink-400">—</span>;
        } else if (spec.kind === "member" && typeof raw === "string") {
          value = memberName(raw);
        } else if (spec.kind === "choice" && typeof raw === "string") {
          value = configChoiceLabel(action.action_id, key, raw);
        } else if (typeof raw === "string") {
          value = <span className="whitespace-pre-wrap break-words">{raw}</span>;
        } else {
          value = <pre className="font-mono text-xs whitespace-pre-wrap break-all">{formatJson(config[key])}</pre>;
        }
        return { label: spec.label, value };
      })}
    />
  );
}
