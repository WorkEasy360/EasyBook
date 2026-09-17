import type { DateString, DateTimeString, DecimalString, UUID } from "@/lib/api/types";

/**
 * Projects and time-tracking contracts.
 *
 * VERIFIED, not transcribed from memory: rewritten from
 * `scripts/dump-serializers.py` (MODULES=projects), projects/api/views.py,
 * projects/selectors.py and live responses captured from the dev tenant.
 * Corrections against the previous hand-written file are noted inline.
 */

export type ProjectStatus = "draft" | "active" | "on_hold" | "completed" | "cancelled";
export type BillingMethod = "non_billable" | "hourly" | "fixed_fee";

/** projects.ProjectSerializer */
export interface Project {
  id: UUID;
  customer: UUID;
  project_code: string;
  name: string;
  description: string;
  status: ProjectStatus;
  billing_method: BillingMethod;
  default_hourly_rate: DecimalString | null;
  fixed_fee_amount: DecimalString | null;
  service_item: UUID | null;
  currency: string;
  budget_hours: DecimalString | null;
  budget_amount: DecimalString | null;
  start_date: DateString | null;
  end_date: DateString | null;
  notes: string;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** projects.ProjectCreateSerializer. `billing_method` defaults to hourly; currency to the org's. */
export interface ProjectInput {
  customer_id: UUID;
  project_code: string;
  name: string;
  description?: string;
  billing_method?: BillingMethod;
  default_hourly_rate?: DecimalString | null;
  fixed_fee_amount?: DecimalString | null;
  service_item_id?: UUID | null;
  currency?: string;
  budget_hours?: DecimalString | null;
  budget_amount?: DecimalString | null;
  start_date?: DateString | null;
  end_date?: DateString | null;
  notes?: string;
}

/**
 * projects.ProjectUpdateSerializer — the ONLY keys a PATCH reads. Customer,
 * code, currency and billing method are fixed after creation
 * (`billing_method_immutable`: logged time was resolved under the old method).
 */
export interface ProjectUpdateInput {
  name?: string;
  description?: string;
  default_hourly_rate?: DecimalString | null;
  fixed_fee_amount?: DecimalString | null;
  service_item_id?: UUID | null;
  budget_hours?: DecimalString | null;
  budget_amount?: DecimalString | null;
  start_date?: DateString | null;
  end_date?: DateString | null;
  notes?: string;
}

/** projects.TaskSerializer */
export interface Task {
  id: UUID;
  project: UUID;
  name: string;
  description: string;
  is_billable: boolean;
  is_active: boolean;
  hourly_rate: DecimalString | null;
  service_item: UUID | null;
  estimated_hours: DecimalString | null;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** projects.TaskCreateSerializer (POST /projects/{id}/tasks/). */
export interface TaskInput {
  name: string;
  description?: string;
  is_billable?: boolean;
  hourly_rate?: DecimalString | null;
  service_item_id?: UUID | null;
  estimated_hours?: DecimalString | null;
}

/** projects.TaskUpdateSerializer (PATCH /projects/tasks/{id}/) — adds `is_active`. */
export interface TaskUpdateInput extends Partial<TaskInput> {
  is_active?: boolean;
}

/** projects.ProjectMemberSerializer — every field read-only. */
export interface ProjectMember {
  id: UUID;
  project: UUID;
  user: UUID;
  user_email: string;
  billable_rate: DecimalString | null;
  cost_rate: DecimalString | null;
  is_active: boolean;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/** projects.ProjectMemberCreateSerializer (POST /projects/{id}/members/). */
export interface ProjectMemberInput {
  user_id: UUID;
  billable_rate?: DecimalString | null;
  cost_rate?: DecimalString | null;
}

/** projects.ProjectMemberUpdateSerializer (PATCH /projects/members/{id}/). Rates apply to time logged from now on. */
export interface ProjectMemberUpdateInput {
  billable_rate?: DecimalString | null;
  cost_rate?: DecimalString | null;
  is_active?: boolean;
}

export type TimeEntryStatus = "draft" | "submitted" | "approved" | "rejected" | "invoiced";

/**
 * projects.TimeEntrySerializer — entirely read-only. Billability and both
 * rates are resolved by the backend (services/rates.py) and frozen onto the
 * entry; the amounts are its own.
 */
export interface TimeEntry {
  id: UUID;
  project: UUID;
  task: UUID;
  user: UUID;
  entry_date: DateString;
  /** Two decimal places, e.g. "2.50". */
  hours: DecimalString;
  description: string;
  status: TimeEntryStatus;
  is_billable: boolean;
  billable_rate: DecimalString | null;
  cost_rate: DecimalString | null;
  billable_amount: DecimalString;
  cost_amount: DecimalString;
  submitted_at: DateTimeString | null;
  approved_by: UUID | null;
  approved_at: DateTimeString | null;
  rejection_reason: string;
  /** Set once billed; the entry is then immutable and cannot be deleted. */
  invoice_line: UUID | null;
  created_at: DateTimeString;
  updated_at: DateTimeString;
}

/**
 * projects.TimeEntryCreateSerializer. `user_id` other than the caller needs
 * VIEW_ALL_TIMESHEETS (403 `log_time_for_others_forbidden`). `is_billable` is
 * deliberately not accepted.
 */
export interface TimeEntryInput {
  project_id: UUID;
  task_id: UUID;
  entry_date: DateString;
  hours: DecimalString;
  description?: string;
  user_id?: UUID | null;
  billable_rate?: DecimalString | null;
  cost_rate?: DecimalString | null;
}

/** projects.TimeEntryUpdateSerializer — draft or rejected entries only (`time_entry_not_editable`). */
export interface TimeEntryUpdateInput {
  entry_date?: DateString;
  hours?: DecimalString;
  description?: string;
  task_id?: UUID;
}

/** POST /time-entries/bulk-submit/ and /bulk-approve/ — atomic; returns TimeEntry[]. */
export interface TimeEntryBulkInput {
  entry_ids: UUID[];
}

/**
 * GET /projects/{id}/profitability/ (VIEW_ALL_TIMESHEETS) —
 * projects/selectors.py :: get_project_profitability.
 *
 * CORRECTION: the old type invented `project`, `profit`, `cost`, `expenses`.
 * The real keys are below. Amounts and hours are decimal strings once the
 * backend fix in projects/api/views.py is deployed; before it the view sent
 * JSON floats, which `features/projects/profitability.ts` normalises.
 */
export interface ProjectProfitability {
  project_id: UUID;
  project_code: string;
  name: string;
  status: ProjectStatus;
  billing_method: BillingMethod;
  /** From the invoice lines that billed this project's time (voids excluded). */
  revenue: DecimalString;
  /** Billable work not yet on any invoice. Pipeline, NOT revenue. */
  unbilled_value: DecimalString;
  labour_cost: DecimalString;
  expense_cost: DecimalString;
  total_cost: DecimalString;
  margin: DecimalString;
  /** null when there is no revenue: an undefined margin, not 0%. */
  margin_percent: DecimalString | null;
  budget_amount: DecimalString | null;
  budget_hours: DecimalString | null;
  hours_over_budget: DecimalString;
  total_hours: DecimalString;
  billable_hours: DecimalString;
  non_billable_hours: DecimalString;
  approved_hours: DecimalString;
  invoiced_hours: DecimalString;
  pending_approval_hours: DecimalString;
}

/**
 * projects.InvoiceProjectTimeSerializer (POST /projects/{id}/invoice-time/,
 * INVOICE_TIME). Returns the created DRAFT sales invoice (InvoiceSerializer,
 * 201) — its `id` is the link to follow.
 */
export interface InvoiceProjectTimeInput {
  invoice_date: DateString;
  due_date: DateString;
  receivable_account_id: UUID;
  up_to_date?: DateString | null;
  tax_rate?: DecimalString;
  tax_payable_account_id?: UUID | null;
  reference?: string;
  notes?: string;
}
