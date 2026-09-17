import { appHref } from "@/lib/routes";
import Link from "next/link";
import { Badge } from "@/components/ui/badge";
import type { AiSource, AiStructuredData } from "@/types/api/ai";
import {
  fieldLabel,
  sourceHref,
  sourceLocation,
  sourceTypeLabel,
  statusNote,
  toolLabel,
  warningText,
} from "./sources";

/**
 * One Ask Books answer.
 *
 * Rendered as PLAIN TEXT: the backend strips HTML and javascript: links from
 * the model's prose (ai/orchestration/guards.py), and this component never
 * interprets Markdown or HTML on top — no dangerouslySetInnerHTML anywhere.
 *
 * The notice is always shown: the prose explains data, it is not a figure of
 * record. The sources are exactly the API's `sources`, and the figures under
 * "Report figures used" are echoed from `structured_data` — deterministic tool
 * output — with no arithmetic applied here.
 */

export function NonAuthoritativeNotice({ className }: { className?: string }) {
  return (
    <p className={className ?? "text-xs text-ink-500"}>
      <span className="font-medium text-ink-700">Explanation, not a figure of record.</span> Numbers come from the
      reports and records cited — open a source to see the authoritative figures before relying on them.
    </p>
  );
}

function scalarText(value: unknown): string | null {
  if (value === null) return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return null; // nested detail stays in the report itself
}

export function AnswerView({
  answer,
  status,
  sources,
  structuredData = null,
  warnings = [],
}: {
  answer: string;
  status: string;
  sources: AiSource[];
  structuredData?: AiStructuredData | null;
  warnings?: string[];
}) {
  const note = statusNote(status);
  const sourceById = new Map(sources.map((source) => [source.source_id, source]));
  const toolResults = structuredData?.tool_results ?? [];

  return (
    <div className="flex flex-col gap-3">
      {note ? (
        <div>
          <Badge tone={status === "partial" ? "warning" : "neutral"} marker={status === "partial"}>
            {note}
          </Badge>
        </div>
      ) : null}

      <p className="text-sm break-words whitespace-pre-wrap text-ink-900">{answer}</p>

      {warnings.length > 0 ? (
        <ul className="list-disc pl-5 text-xs text-warning-700">
          {warnings.map((code) => (
            <li key={code}>{warningText(code)}</li>
          ))}
        </ul>
      ) : null}

      <NonAuthoritativeNotice />

      <div>
        <h3 className="text-2xs font-medium tracking-wide text-ink-500 uppercase">Sources</h3>
        {sources.length === 0 ? (
          <p className="mt-1 text-xs text-ink-500">No sources were cited for this answer.</p>
        ) : (
          <ul className="mt-1 flex flex-col gap-1">
            {sources.map((source) => {
              const href = sourceHref(source);
              const location = sourceLocation(source);
              return (
                <li key={source.source_id} className="text-sm">
                  <span className="mr-1.5 text-2xs font-medium tracking-wide text-ink-500 uppercase">
                    {sourceTypeLabel(source.type)}
                  </span>
                  {href ? (
                    <Link href={appHref(href)} className="font-medium text-brand-700 hover:underline">
                      {source.label}
                    </Link>
                  ) : (
                    <span className="text-ink-800">{source.label}</span>
                  )}
                  {location ? <span className="ml-1.5 text-xs text-ink-500">({location})</span> : null}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {toolResults.length > 0 ? (
        <details className="rounded-md border border-ink-200 bg-ink-50 px-3 py-2">
          <summary className="cursor-pointer text-xs font-medium text-ink-700">Report figures used</summary>
          <p className="mt-2 text-xs text-ink-500">
            Values exactly as the backend&apos;s reports returned them, before the assistant wrote its explanation.
          </p>
          <div className="mt-2 flex flex-col gap-3">
            {toolResults.map((result, index) => {
              const cited = result.source_ids.map((id) => sourceById.get(id)?.label).filter(Boolean);
              const rows = Object.entries(result.summary ?? {})
                .map(([key, value]) => [key, scalarText(value)] as const)
                .filter((row): row is readonly [string, string] => row[1] !== null);
              return (
                <div key={`${result.tool}-${index}`}>
                  <p className="text-xs font-medium text-ink-800">
                    {toolLabel(result.tool)}
                    {cited.length > 0 ? <span className="font-normal text-ink-500"> — {cited.join(", ")}</span> : null}
                    {result.status !== "ok" ? (
                      <span className="font-normal text-warning-700"> ({result.error_code ?? result.status})</span>
                    ) : null}
                  </p>
                  {rows.length > 0 ? (
                    <dl className="mt-1 grid grid-cols-1 gap-x-4 gap-y-0.5 sm:grid-cols-2">
                      {rows.map(([key, value]) => (
                        <div key={key} className="flex justify-between gap-3 text-xs">
                          <dt className="text-ink-500">{fieldLabel(key)}</dt>
                          <dd className="tabular text-right text-ink-800">{value}</dd>
                        </div>
                      ))}
                    </dl>
                  ) : null}
                  {result.truncated ? <p className="mt-1 text-2xs text-ink-500">Detail truncated — see the report.</p> : null}
                </div>
              );
            })}
          </div>
        </details>
      ) : null}
    </div>
  );
}
