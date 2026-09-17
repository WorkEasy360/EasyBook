"use client";

import * as React from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { DetailList } from "@/components/ui/detail";
import { Checkbox, FormError, FormField, Input, Select } from "@/components/ui/field";
import { LinkButton } from "@/components/ui/link-button";
import { STATEMENT_IMPORT_STATUS, StatusBadge } from "@/components/ui/status-badge";
import { useToast } from "@/components/ui/toast";
import { useUnsavedChanges } from "@/lib/hooks/use-unsaved-changes";
import { useIdempotentMutation } from "@/lib/hooks/use-api";
import { api } from "@/lib/api/browser";
import { formErrorOf, referenceOf } from "@/lib/api/errors";
import { formatDate } from "@/lib/datetime";
import {
  DATE_FORMAT_OPTIONS,
  EMPTY_MAPPING,
  MAX_STATEMENT_BYTES,
  cellFor,
  mappingErrors,
  readCsv,
  suggestMapping,
  toMappingPayload,
  type MappingFormValues,
  type ParsedCsvPreview,
} from "./csv";
import {
  AMOUNT_MODE_LABELS,
  type AmountMode,
  type StatementImport,
  type StatementImportInput,
} from "@/types/api/banking";

/**
 * CSV statement import, in three steps: choose the file, say which column is
 * which, import.
 *
 * The mapping is REQUIRED and confirmed by a person, never inferred on the
 * server (banking/services/parsers.py): bank CSV headers are not standard, and
 * a wrongly guessed amount column books every withdrawal as a deposit. The
 * form is pre-filled from recognisable header names only as a convenience.
 *
 * The browser reads the file to show its header and a few raw rows. It does
 * not parse dates or amounts — the server does that with Decimal on import and
 * rejects the whole file with a row-numbered message if any row is unreadable.
 * Importing posts nothing to the ledger: a statement line is evidence, and it
 * is matched or categorized afterwards.
 */

const PREVIEW_ROWS = 5;

interface LoadedFile {
  name: string;
  content: string;
  preview: ParsedCsvPreview;
}

export function StatementImportWizard({
  bankAccountId,
  bankAccountName,
}: {
  bankAccountId: string;
  bankAccountName: string;
}) {
  const toast = useToast();
  const [file, setFile] = React.useState<LoadedFile | null>(null);
  const [fileError, setFileError] = React.useState<string | null>(null);
  const [result, setResult] = React.useState<StatementImport | null>(null);
  const [formError, setFormError] = React.useState<{ message: string | null; reference: string | null }>({
    message: null,
    reference: null,
  });
  const fileInputRef = React.useRef<HTMLInputElement>(null);

  const { control, register, handleSubmit, reset } = useForm<MappingFormValues>({ defaultValues: EMPTY_MAPPING });
  const values = useWatch({ control }) as MappingFormValues;
  const [showErrors, setShowErrors] = React.useState(false);
  const errors = showErrors ? mappingErrors(values) : {};

  useUnsavedChanges(file !== null && result === null);

  const mutation = useIdempotentMutation<StatementImport, StatementImportInput>(
    "bank-transactions",
    (input, idempotencyKey) =>
      // Imports are slower than a CRUD call on a large file.
      api.post<StatementImport>(`bank-accounts/${bankAccountId}/statement-imports`, input, {
        idempotencyKey,
        timeoutMs: 120_000,
      }),
    {
      invalidates: "bank-accounts",
      onSuccess: (saved) => {
        setResult(saved);
        toast.push({ tone: "success", title: "Statement imported", description: `${saved.rows_imported} new lines` });
      },
      onError: (error) => setFormError({ message: formErrorOf(error), reference: referenceOf(error) }),
    },
  );

  async function onFileChosen(event: React.ChangeEvent<HTMLInputElement>) {
    setFileError(null);
    setFormError({ message: null, reference: null });
    setResult(null);
    const chosen = event.target.files?.[0];
    if (!chosen) {
      setFile(null);
      return;
    }
    if (chosen.size > MAX_STATEMENT_BYTES) {
      setFile(null);
      setFileError("This file is larger than 10 MB, the most one import accepts. Split it by date range.");
      return;
    }
    // Blob.text() decodes as UTF-8 and drops a byte-order mark, so the first
    // header is not silently prefixed with one.
    const content = await chosen.text();
    const preview = readCsv(content, PREVIEW_ROWS);
    if (preview.headers.length === 0 || preview.headers.every((header) => header === "")) {
      setFile(null);
      setFileError("This file has no header row. The first line must name the columns.");
      return;
    }
    setFile({ name: chosen.name, content, preview });
    setShowErrors(false);
    reset({ ...EMPTY_MAPPING, ...suggestMapping(preview.headers) });
  }

  function onSubmit(mapping: MappingFormValues) {
    if (!file) return;
    setShowErrors(true);
    if (Object.keys(mappingErrors(mapping)).length > 0) return;
    setFormError({ message: null, reference: null });
    mutation.mutate({ file_name: file.name, content: file.content, mapping: toMappingPayload(mapping) });
  }

  function startOver() {
    setFile(null);
    setResult(null);
    setShowErrors(false);
    setFormError({ message: null, reference: null });
    reset(EMPTY_MAPPING);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  if (result) {
    return <ImportResult result={result} bankAccountId={bankAccountId} bankAccountName={bankAccountName} onAgain={startOver} />;
  }

  const headers = file?.preview.headers ?? [];
  const mode = values.amount_mode;

  return (
    <form method="post" noValidate onSubmit={(event) => void handleSubmit(onSubmit)(event)} className="flex flex-col gap-4">
      <Card>
        <CardHeader title="1. Choose the statement file" description="A CSV export from your bank, with a header row." />
        <CardBody className="flex flex-col gap-3">
          <FormField
            label="Statement file (CSV)"
            required
            error={fileError}
            hint="Up to 10 MB. Re-importing a file already imported into this account is refused."
          >
            <Input ref={fileInputRef} type="file" accept=".csv,text/csv" onChange={(event) => void onFileChosen(event)} />
          </FormField>
          {file ? (
            <p className="text-sm text-ink-600">
              <span className="font-medium text-ink-900">{file.name}</span> · {file.preview.headers.length} columns ·{" "}
              {file.preview.rowCount} data {file.preview.rowCount === 1 ? "row" : "rows"}
            </p>
          ) : null}
        </CardBody>
      </Card>

      {file ? (
        <>
          <Card>
            <CardHeader
              title="2. Say which column is which"
              description="Pre-filled where a header name was recognisable. Check every column before importing — a wrong amount column books withdrawals as deposits."
            />
            <CardBody className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <FormField label="Date column" required error={errors.date_column ?? null}>
                <ColumnSelect headers={headers} required {...register("date_column")} />
              </FormField>

              <FormField
                label="Date format"
                hint="Automatic tries the formats in the order listed, so 05/09/2026 reads as 5 September. Choose MM/DD/YYYY for a US-style file."
              >
                <Select {...register("date_format")}>
                  <option value="">Automatic</option>
                  {DATE_FORMAT_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label} (e.g. {option.example})
                    </option>
                  ))}
                </Select>
              </FormField>

              <FormField label="How amounts are shown" required>
                <Select {...register("amount_mode")}>
                  {(Object.keys(AMOUNT_MODE_LABELS) as AmountMode[]).map((value) => (
                    <option key={value} value={value}>
                      {AMOUNT_MODE_LABELS[value]}
                    </option>
                  ))}
                </Select>
              </FormField>

              {mode === "signed" || mode === "indicator" ? (
                <FormField
                  label="Amount column"
                  required
                  error={errors.amount_column ?? null}
                  {...(mode === "signed" ? { hint: "Negative numbers are money out." } : { hint: "Unsigned; the Dr/Cr column gives the direction." })}
                >
                  <ColumnSelect headers={headers} required {...register("amount_column")} />
                </FormField>
              ) : null}

              {mode === "debit_credit" ? (
                <>
                  <FormField label="Withdrawal column (money out)" required error={errors.debit_column ?? null}>
                    <ColumnSelect headers={headers} required {...register("debit_column")} />
                  </FormField>
                  <FormField label="Deposit column (money in)" required error={errors.credit_column ?? null}>
                    <ColumnSelect headers={headers} required {...register("credit_column")} />
                  </FormField>
                </>
              ) : null}

              {mode === "indicator" ? (
                <FormField
                  label="Dr/Cr column"
                  required
                  error={errors.indicator_column ?? null}
                  hint="Recognised: Dr, Debit, D, W, Withdrawal for money out; Cr, Credit, C, Deposit for money in."
                >
                  <ColumnSelect headers={headers} required {...register("indicator_column")} />
                </FormField>
              ) : null}

              <FormField label="Description column">
                <ColumnSelect headers={headers} {...register("description_column")} />
              </FormField>
              <FormField label="Reference column">
                <ColumnSelect headers={headers} {...register("reference_column")} />
              </FormField>
              <FormField label="Counterparty column">
                <ColumnSelect headers={headers} {...register("counterparty_column")} />
              </FormField>
              <FormField
                label="Bank transaction id column"
                hint="If the bank gives each line its own id, it is the strongest duplicate guard."
              >
                <ColumnSelect headers={headers} {...register("external_id_column")} />
              </FormField>

              <div className="sm:col-span-2 lg:col-span-3">
                <Controller
                  control={control}
                  name="invert_sign"
                  render={({ field }) => (
                    <Checkbox
                      label="Flip the sign of every amount"
                      hint="Only for a file that shows money out as positive numbers. Compare with the preview below before choosing it."
                      checked={field.value}
                      onChange={(event) => field.onChange(event.target.checked)}
                      onBlur={field.onBlur}
                    />
                  )}
                />
              </div>
            </CardBody>
          </Card>

          <MappingPreview file={file} values={values} />

          <FormError message={formError.message} reference={formError.reference} />

          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button variant="secondary" onClick={startOver} disabled={mutation.isPending}>
              Choose another file
            </Button>
            <Button type="submit" variant="primary" loading={mutation.isPending} loadingLabel="Importing">
              Import statement
            </Button>
          </div>
        </>
      ) : null}
    </form>
  );
}

const ColumnSelect = React.forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement> & { headers: readonly string[] }
>(function ColumnSelect({ headers, required, ...rest }, ref) {
  return (
    <Select ref={ref} {...rest}>
      <option value="">{required ? "Choose a column…" : "Not in this file"}</option>
      {headers.map((header, index) => (
        <option key={`${header}-${index}`} value={header}>
          {header || `(column ${index + 1}, unnamed)`}
        </option>
      ))}
    </Select>
  );
});

/**
 * The first rows exactly as the file has them, under the role each mapped
 * column will play. Nothing here is parsed: the server reads the cells.
 */
function MappingPreview({ file, values }: { file: LoadedFile; values: MappingFormValues }) {
  const { headers, rows } = file.preview;
  const columns: Array<{ label: string; column: string }> = [
    { label: "Date", column: values.date_column },
    ...(values.amount_mode === "debit_credit"
      ? [
          { label: "Money out", column: values.debit_column },
          { label: "Money in", column: values.credit_column },
        ]
      : [{ label: "Amount", column: values.amount_column }]),
    ...(values.amount_mode === "indicator" ? [{ label: "Dr/Cr", column: values.indicator_column }] : []),
    { label: "Description", column: values.description_column },
    { label: "Reference", column: values.reference_column },
    { label: "Counterparty", column: values.counterparty_column },
  ].filter((entry) => entry.column !== "" || entry.label === "Date");

  return (
    <Card>
      <CardHeader
        title="3. Check the first rows"
        description="Cells shown exactly as they appear in the file. Dates and amounts are read by the server when you import."
        actions={values.invert_sign ? <Badge tone="warning">Signs will be flipped</Badge> : null}
      />
      <CardBody className="overflow-x-auto p-0">
        <table className="w-full text-sm">
          <caption className="sr-only">First rows of {file.name} under the mapped columns</caption>
          <thead>
            <tr className="border-b border-ink-200 bg-ink-50 text-left text-2xs tracking-wide text-ink-500 uppercase">
              {columns.map((entry) => (
                <th key={entry.label} scope="col" className="px-3 py-2 font-medium">
                  {entry.label}
                  <span className="block font-normal normal-case text-ink-400">{entry.column || "not chosen"}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex} className="border-b border-ink-100 last:border-0">
                {columns.map((entry) => (
                  <td key={entry.label} className="px-3 py-2 tabular text-ink-800">
                    {cellFor(headers, row, entry.column) || <span className="text-ink-300">—</span>}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 ? <p className="px-3 py-4 text-sm text-ink-500">The file has a header but no data rows.</p> : null}
      </CardBody>
    </Card>
  );
}

function ImportResult({
  result,
  bankAccountId,
  bankAccountName,
  onAgain,
}: {
  result: StatementImport;
  bankAccountId: string;
  bankAccountName: string;
  onAgain: () => void;
}) {
  return (
    <Card>
      <CardHeader
        title="Import finished"
        description={`Into ${bankAccountName}. Nothing was posted to the ledger — match or categorize the new lines to explain them.`}
        actions={<StatusBadge status={result.status} map={STATEMENT_IMPORT_STATUS} />}
      />
      <CardBody className="flex flex-col gap-4">
        <DetailList
          columns={3}
          items={[
            { label: "File", value: result.file_name || "—" },
            { label: "Rows read", value: <span className="tabular">{result.rows_read}</span> },
            { label: "Lines imported", value: <span className="tabular">{result.rows_imported}</span> },
            {
              label: "Skipped as already held",
              value: <span className="tabular">{result.rows_skipped_duplicate}</span>,
            },
            {
              label: "Statement period",
              value:
                result.statement_start_date && result.statement_end_date
                  ? `${formatDate(result.statement_start_date)} – ${formatDate(result.statement_end_date)}`
                  : "No new lines",
            },
            ...(result.error_message ? [{ label: "Error", value: result.error_message, span: true }] : []),
          ]}
        />
        {result.rows_skipped_duplicate > 0 ? (
          <p className="text-xs text-ink-500">
            Skipped lines were already held for this account (same date, amount, narration and reference, or the same
            bank id). If one is a genuine repeat charge, add it by hand from the account page.
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <LinkButton href={`/banking/transactions?bank_account=${bankAccountId}&status=unmatched`} variant="primary">
            Review unmatched lines
          </LinkButton>
          <LinkButton href={`/banking/accounts/${bankAccountId}`}>Back to account</LinkButton>
          <Button variant="secondary" onClick={onAgain}>
            Import another file
          </Button>
        </div>
      </CardBody>
    </Card>
  );
}
