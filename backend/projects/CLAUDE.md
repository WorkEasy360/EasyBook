# PROJECTS

PURPOSE
Project and time tracking: what work was done, by whom, at what cost, and what of it is chargeable. Phase 5 complete: Projects, Members, Tasks, Time Entries with an approval lifecycle, rate resolution, time→invoice billing, project expenses, and profitability.

**POSTS NO ACCOUNTING JOURNALS.** This is the module's defining property, not an omission. Logging time is not a financial event: nothing has been earned, owed or paid. Hours become money only when approved billable time is invoiced, and at that point `sales` does the posting; project costs post through `purchases.Expense`. If you find yourself reaching for `accounting.services` here, the design has gone wrong — there is deliberately no `accounting_bridge.py` in this app, unlike `sales`, `purchases` and `inventory`.

OWNS
- `models/project.py` — `Project`, `ProjectMember`, `Task`.
  - `Project.billing_method` (`BillingMethod`) is the upper bound on chargeability and is **immutable after creation**: existing entries carry billability and rates resolved under the old method, so flipping it would leave a project whose own time disagrees with its rules. Changing how an engagement bills is a new project.
  - `Project.service_item` / `Task.service_item` — a `sales.InvoiceLine` requires an `Item`, so time cannot be invoiced without one. Must be a SERVICE item: billing hours as a stocked PRODUCT would make posting the resulting invoice try to issue stock for work done.
  - `ProjectMember` carries TWO rates. `billable_rate` is what the customer is charged; `cost_rate` is what the person costs us. Conflating them — the common shortcut — makes every margin equal to revenue.
  - `Task.is_billable` is an upper bound the project can veto, never an override: a task cannot opt into billing on a NON_BILLABLE project, so a single mis-ticked checkbox cannot invoice a customer who agreed to pay nothing.
- `models/time_entry.py` — `TimeEntry`. Lifecycle DRAFT → SUBMITTED → APPROVED/REJECTED → INVOICED, not DRAFT/POSTED, because approval (not posting) is what gates billing. `billable_rate`/`cost_rate` are **frozen onto the entry at creation** — a rate change next quarter must not restate the value of work already done, the same historical-snapshot rule as sales/purchases document lines. A DB `CheckConstraint` enforces `status == INVOICED` ⟺ `invoice_line IS NOT NULL`, because "billed but not marked billed" is exactly the state that double-bills a customer.
- `services/rates.py` — the ONE definition of the rate chain. Billable: explicit → `ProjectMember.billable_rate` → `Task.hourly_rate` → `Project.default_hourly_rate`. Member beats task deliberately: a negotiated rate for a named individual is the more specific agreement. Cost has NO task or project fallback — what a person costs is a fact about the person, and guessing would put a fabricated number in a margin report.
- `services/projects.py` — project/member/task CRUD and the status machine. No path out of COMPLETED or CANCELLED: reopening would let new time attach to an engagement already reported on. A project with invoiced time cannot be cancelled, only completed.
- `services/time_entries.py` — the approval lifecycle. A REJECTED entry returns to DRAFT when edited rather than being deleted: the hours were really worked and the argument is about how they are recorded; deleting loses the cost side too. **Self-approval is refused in the service as well as the role matrix**, because a single owner account holds both LOG_TIME and APPROVE_TIME and the role check alone would wave it through.
- `services/billing.py` — builds invoice LINES and hands them to `sales.services.invoices.create_invoice`. Generates a DRAFT; a human posts it. Entries are marked INVOICED as soon as the draft exists (not at posting), because they are already committed to that document and leaving them unmarked would let a second draft bill the same hours. Locks the entries it selects, so a concurrent call cannot bill them twice.
- `selectors.py` — hours, revenue, cost, margin. All derived, nothing stored on `Project`.

BILLING METHOD SEMANTICS (`services/rates.py::resolve_is_billable` and `services/billing.py`)
- `NON_BILLABLE` — hours tracked for cost and reporting, never chargeable. Invoicing refuses.
- `HOURLY` — the task's own `is_billable` decides; billable hours invoice at the resolved rate.
- `FIXED_FEE` — hours ARE tracked (profitability depends on them) but are NOT separately chargeable, and invoicing refuses. The agreed fee is the revenue; billing the hours on top would charge the customer twice for the same work. Raise the fee invoice through `sales` directly.

REVENUE vs UNBILLED VALUE (`selectors.py`)
Revenue comes from the INVOICE LINE, not from `hours × billable_rate`. The two legitimately differ — an invoice may be edited before posting, discounted, or credited — and the invoice is what the customer was actually asked to pay. Work not yet on any invoice is reported separately as `unbilled_value`, never folded into revenue. `margin_percent` is `None` (not `0`) when there is no revenue, because a project that has billed nothing has an undefined margin and `0%` would read as break-even.

OBJECT-LEVEL AUTHORIZATION (`api/views.py`)
The thing this module has that sales/purchases do not: a time entry belongs to a PERSON. `LOG_TIME` lets you record your own hours; it must not let you read or edit a colleague's, because an entry carries `cost_rate` — an indirect read on what that person is paid. `VIEW_ALL_TIMESHEETS` widens the view, and `_scope_time_entries` is the single place the narrowing happens. Logging time FOR someone else is a supervisory act and needs the same wider permission. Project profitability is gated on `VIEW_ALL_TIMESHEETS` rather than `VIEW_PROJECTS` for the same reason — margin exposes cost.

INVARIANTS
- Decimal only for money and hours. Hours are 2dp (1.5 = ninety minutes); 4dp elsewhere is quantity precision nobody has for time.
- No accounting journal originates here. Ever.
- Rates and billability are RESOLVED server-side and frozen; `is_billable` is never accepted from the client.
- Nobody approves their own time.
- The same hour can never appear on two invoices — enforced by the `invoiced ⟺ linked` DB constraint, the `invoice_line__isnull=True` filter, and a `select_for_update()` on the entries being billed.
- Tenant isolation mandatory — every model is `TenantScopedModel` with a matching RLS migration; `tests/test_rls.py` fails if a new model appears without one.
- No class-level tenant-scoped `.objects.all()` querysets — always build in `get_queryset()`.

DEPENDENCIES AND THE ONE LOOSE END
`projects` imports `sales` (Customer, `create_invoice`, InvoiceLine) and reads `purchases.Expense`. `purchases.Expense.project` is a **string** FK reference, so `purchases` never imports `projects` — the coupling is database-level only, and reusing `Expense` rather than inventing a parallel `ProjectExpense` keeps one definition of "a cost we incurred" and one place it posts from.

**Known limitation, deliberate:** voiding an invoice does NOT automatically return its time entries to APPROVED. `sales.void_invoice` does not know projects exist, and making it know would invert the dependency. `services/billing.py::release_invoiced_time` does the release and must be called by whoever voids a project-billed invoice. Until an event/automation layer exists (Phase 11), that is a manual step — `selectors.get_project_revenue` already excludes voided invoices, so the revenue figure is right either way; it is the entries' re-billability that needs the call.

TEST GOTCHA
`ProjectsTestsBase` / `ProjectsTransactionTestsBase` split off `ProjectsFixtureMixin` for the same reason `purchases.tests.base` does — `TestCase` subclasses `TransactionTestCase`, so mixing them directly silently yields `TestCase` semantics. The fixture also carries a dedicated `manager_user` approver: since nobody may approve their own time, a fixture that approved as the entry's author could not produce approved time for that author.

READ FIRST
- `services/rates.py` (the rate chain), `services/billing.py` (the sales boundary), `models/time_entry.py`, `selectors.py`, `api/views.py` (object-level scoping)

TOKEN DISCIPLINE
- Do not duplicate root, `core`, `sales` or `purchases` CLAUDE.md content here.
