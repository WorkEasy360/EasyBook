import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { DetailList, PageBody, Section } from "@/components/ui/detail";
import { Card, CardBody, CardHeader } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { DataTable, type Column } from "@/components/ui/data-table";
import { DateFilterForm } from "@/components/ui/date-filter-form";
import { LinkButton } from "@/components/ui/link-button";
import { Money, Quantity } from "@/components/ui/money";
import { StatCard, StatGrid } from "@/components/ui/stat-card";
import { BILLING_METHOD_LABELS, PROJECT_STATUS, StatusBadge } from "@/components/ui/status-badge";
import { ErrorState, ForbiddenState } from "@/components/ui/states";
import { DocumentAction } from "@/features/shared/document-action";
import { TaskDialogButton } from "@/features/projects/task-dialog";
import { MemberDialogButton } from "@/features/projects/member-dialog";
import { InvoiceTimeDialogButton } from "@/features/projects/invoice-time-dialog";
import { normalizeProfitability } from "@/features/projects/profitability";
import { serverApi, tryServer } from "@/lib/api/server";
import { recordsById } from "@/lib/api/lookups";
import { dateParamOf, wholeList, type RawSearchParams } from "@/lib/list-query";
import { requireSession } from "@/lib/auth/context";
import { PERMISSIONS, roleHasPermission } from "@/lib/authz/permissions";
import { ApiError, referenceOf } from "@/lib/api/errors";
import { formatDate } from "@/lib/datetime";
import { formatPercent, isNegative, isZero } from "@/lib/money";
import type { Membership } from "@/types/api/accounts";
import type { Item } from "@/types/api/items";
import type { Customer } from "@/types/api/sales";
import type { Project, ProjectMember, Task, TimeEntry } from "@/types/api/projects";

export const metadata: Metadata = { title: "Project" };

/**
 * A project: its terms, profitability, tasks, people and the time waiting to
 * be billed.
 *
 * Every money figure is the backend's. Profitability comes from
 * projects/selectors.py (revenue from invoice lines, cost from frozen entry
 * rates plus posted expenses) and is gated on VIEW_ALL_TIMESHEETS because it
 * exposes cost. Unbilled time is the exact preview of what "Invoice time"
 * would bill.
 */
export default async function ProjectDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<RawSearchParams>;
}) {
  const session = await requireSession();
  const { id } = await params;
  const query = await searchParams;
  const role = session.role;

  if (!roleHasPermission(role, PERMISSIONS.VIEW_PROJECTS)) {
    return (
      <>
        <PageHeader title="Project" />
        <ForbiddenState resource="projects" />
      </>
    );
  }

  const result = await tryServer(() => serverApi.get<Project>(`projects/${id}`));
  if (!result.ok) {
    if (result.error instanceof ApiError && result.error.isNotFound) notFound();
    return (
      <>
        <PageHeader title="Project" />
        <ErrorState message={result.error.message} reference={referenceOf(result.error)} />
      </>
    );
  }

  const project = result.data;
  const self = `/projects/${project.id}`;
  const canManage = roleHasPermission(role, PERMISSIONS.MANAGE_PROJECTS);
  const canSeeCost = roleHasPermission(role, PERMISSIONS.VIEW_ALL_TIMESHEETS);
  const canInvoice = roleHasPermission(role, PERMISSIONS.INVOICE_TIME);
  const canLogTime = roleHasPermission(role, PERMISSIONS.LOG_TIME);
  const isHourly = project.billing_method === "hourly";
  const upToDate = dateParamOf(query, "up_to_date");

  const [customer, tasks, members, people, profitability, unbilled] = await Promise.all([
    roleHasPermission(role, PERMISSIONS.VIEW_CUSTOMERS)
      ? tryServer(() => serverApi.get<Customer>(`sales/customers/${project.customer}`))
      : Promise.resolve(null),
    tryServer(() => serverApi.list<Task>(`projects/${project.id}/tasks`, { query: { page_size: 200 } })),
    tryServer(() => serverApi.list<ProjectMember>(`projects/${project.id}/members`, { query: { page_size: 200 } })),
    // A bare array of active memberships, used only to put names to user ids.
    tryServer(() => serverApi.get<Membership[]>("organizations/members")),
    canSeeCost ? tryServer(() => serverApi.get<unknown>(`projects/${project.id}/profitability`)) : Promise.resolve(null),
    canInvoice && isHourly
      ? tryServer(() =>
          serverApi.get<TimeEntry[]>(`projects/${project.id}/unbilled-time`, { query: { up_to_date: upToDate } }),
        )
      : Promise.resolve(null),
  ]);

  const taskRows = tasks.ok ? tasks.data.results : [];
  const memberRows = members.ok ? members.data.results : [];
  const unbilledRows = unbilled?.ok ? unbilled.data : [];
  const taskById = new Map(taskRows.map((task) => [task.id, task]));
  const personByUser = new Map((people.ok ? people.data : []).map((membership) => [membership.user.id, membership]));
  const items = roleHasPermission(role, PERMISSIONS.VIEW_ITEMS)
    ? await recordsById<Item>("items", [project.service_item, ...taskRows.map((task) => task.service_item)])
    : new Map<string, Item>();
  const report = profitability?.ok ? normalizeProfitability(profitability.data) : null;

  function personLabel(userId: string, fallbackEmail?: string): string {
    const person = personByUser.get(userId);
    if (!person) return fallbackEmail ?? "Former member";
    const full = `${person.user.first_name} ${person.user.last_name}`.trim();
    return full || person.user.email;
  }

  const status = project.status;
  const transitions = canManage ? (
    <>
      {status === "active" ? (
        <DocumentAction
          resource="projects"
          id={project.id}
          action="hold"
          label="Put on hold"
          confirmTitle="Put this project on hold?"
          confirmMessage={<p>No new time can be logged while the project is on hold. You can resume it later.</p>}
          successTitle="Project on hold"
        />
      ) : null}
      {status === "active" || status === "on_hold" ? (
        <DocumentAction
          resource="projects"
          id={project.id}
          action="complete"
          label="Complete"
          confirmTitle="Complete this project?"
          confirmMessage={
            <>
              <p>Completing closes the project to new time for good — a completed project cannot be reopened.</p>
              <p className="mt-2">
                Approved time not yet invoiced stays as it is and can still be billed; nothing is written off.
              </p>
            </>
          }
          successTitle="Project completed"
        />
      ) : null}
      {status === "draft" || status === "active" || status === "on_hold" ? (
        <DocumentAction
          resource="projects"
          id={project.id}
          action="cancel"
          label="Cancel project"
          variant="danger"
          confirmTitle="Cancel this project?"
          confirmMessage={
            <p>
              A cancelled project accepts no more time and cannot be reopened. A project with invoiced time cannot be
              cancelled — complete it instead.
            </p>
          }
          successTitle="Project cancelled"
        />
      ) : null}
      {status === "draft" || status === "on_hold" ? (
        <DocumentAction
          resource="projects"
          id={project.id}
          action="activate"
          label={status === "draft" ? "Activate" : "Resume"}
          variant="primary"
          confirmTitle={status === "draft" ? "Activate this project?" : "Resume this project?"}
          confirmMessage={<p>Time can be logged against an active project. Nothing is posted to the ledger.</p>}
          successTitle={status === "draft" ? "Project activated" : "Project resumed"}
        />
      ) : null}
    </>
  ) : null;

  const taskColumns: Column<Task>[] = [
    {
      key: "name",
      header: "Task",
      cell: (task) => (
        <div>
          <span className="font-medium text-ink-900">{task.name}</span>
          {task.description ? <p className="line-clamp-1 text-xs text-ink-500">{task.description}</p> : null}
        </div>
      ),
    },
    {
      key: "is_billable",
      header: "Billable",
      cell: (task) =>
        task.is_billable && isHourly ? (
          <Badge tone="success" size="sm" marker>
            Billable
          </Badge>
        ) : (
          <Badge tone="neutral" size="sm">
            {task.is_billable ? "Not on this project" : "Non-billable"}
          </Badge>
        ),
    },
    {
      key: "hourly_rate",
      header: "Rate",
      numeric: true,
      hideBelow: "sm",
      cell: (task) => <Money value={task.hourly_rate} currency={project.currency} />,
    },
    {
      key: "estimated_hours",
      header: "Estimate",
      numeric: true,
      hideBelow: "md",
      cell: (task) => <Quantity value={task.estimated_hours} unit="h" />,
    },
    {
      key: "service_item",
      header: "Invoiced as",
      hideBelow: "lg",
      cell: (task) =>
        task.service_item ? (items.get(task.service_item)?.name ?? "Service item") : <span className="text-ink-400">Project default</span>,
    },
    {
      key: "is_active",
      header: "Status",
      cell: (task) => (
        <Badge tone={task.is_active ? "success" : "neutral"} size="sm" marker={task.is_active}>
          {task.is_active ? "Active" : "Inactive"}
        </Badge>
      ),
    },
    ...(canManage
      ? [
          {
            key: "actions",
            header: "",
            headerLabel: "Actions",
            numeric: true,
            cell: (task: Task) => (
              <TaskDialogButton projectId={project.id} billingMethod={project.billing_method} currency={project.currency} task={task} />
            ),
          },
        ]
      : []),
  ];

  const memberColumns: Column<ProjectMember>[] = [
    {
      key: "user",
      header: "Person",
      cell: (member) => (
        <div>
          <span className="font-medium text-ink-900">{personLabel(member.user, member.user_email)}</span>
          <p className="text-xs text-ink-500">{member.user_email}</p>
        </div>
      ),
    },
    {
      key: "billable_rate",
      header: "Billable rate",
      numeric: true,
      cell: (member) => <Money value={member.billable_rate} currency={project.currency} />,
    },
    ...(canSeeCost
      ? [
          {
            key: "cost_rate",
            header: "Cost rate",
            numeric: true,
            cell: (member: ProjectMember) => <Money value={member.cost_rate} currency={project.currency} />,
          },
        ]
      : []),
    {
      key: "is_active",
      header: "Status",
      cell: (member) => (
        <Badge tone={member.is_active ? "success" : "neutral"} size="sm" marker={member.is_active}>
          {member.is_active ? "Active" : "Inactive"}
        </Badge>
      ),
    },
    ...(canManage
      ? [
          {
            key: "actions",
            header: "",
            headerLabel: "Actions",
            numeric: true,
            cell: (member: ProjectMember) => (
              <MemberDialogButton
                projectId={project.id}
                currency={project.currency}
                member={member}
                memberLabel={personLabel(member.user, member.user_email)}
                canSeeCost={canSeeCost}
              />
            ),
          },
        ]
      : []),
  ];

  const unbilledColumns: Column<TimeEntry>[] = [
    { key: "entry_date", header: "Date", cell: (entry) => <span className="tabular whitespace-nowrap">{formatDate(entry.entry_date)}</span> },
    { key: "task", header: "Task", cell: (entry) => taskById.get(entry.task)?.name ?? "Task" },
    { key: "user", header: "Person", hideBelow: "sm", cell: (entry) => personLabel(entry.user) },
    { key: "hours", header: "Hours", numeric: true, cell: (entry) => <Quantity value={entry.hours} unit="h" /> },
    {
      key: "billable_rate",
      header: "Rate",
      numeric: true,
      hideBelow: "md",
      cell: (entry) => <Money value={entry.billable_rate} currency={project.currency} />,
    },
    {
      key: "billable_amount",
      header: "Amount",
      numeric: true,
      cell: (entry) => <Money value={entry.billable_amount} currency={project.currency} />,
    },
  ];

  const customerName = customer?.ok ? customer.data.display_name : null;

  return (
    <>
      <PageHeader
        title={project.name}
        breadcrumbs={[{ label: "Projects", href: "/projects" }, { label: project.project_code }]}
        meta={
          <>
            <StatusBadge status={project.status} map={PROJECT_STATUS} />
            <Badge tone="neutral">{BILLING_METHOD_LABELS[project.billing_method] ?? project.billing_method}</Badge>
          </>
        }
        description={
          customerName ? (
            <>
              For{" "}
              <Link href={`/sales/customers/${project.customer}`} className="text-brand-700 hover:underline">
                {customerName}
              </Link>
            </>
          ) : undefined
        }
        actions={
          <>
            {canLogTime || canSeeCost ? (
              <LinkButton href={`/projects/timesheets?project=${project.id}`}>Time entries</LinkButton>
            ) : null}
            {canManage ? <LinkButton href={`/projects/${project.id}/edit`}>Edit</LinkButton> : null}
            {transitions}
          </>
        }
      />

      <PageBody>
        {profitability ? (
          report ? (
            <Section
              title="Profitability"
              description="Revenue is what has been invoiced for this project's time; unbilled value is work not yet on an invoice and is not revenue."
            >
              <StatGrid columns={4}>
                <StatCard label="Revenue" value={<Money value={report.revenue} currency={project.currency} />} hint="Invoiced, excluding void" />
                <StatCard
                  label="Total cost"
                  value={<Money value={report.total_cost} currency={project.currency} />}
                  hint={
                    <>
                      Labour <Money value={report.labour_cost} currency={project.currency} /> · Expenses{" "}
                      <Money value={report.expense_cost} currency={project.currency} />
                    </>
                  }
                />
                <StatCard
                  label="Margin"
                  value={<Money value={report.margin} currency={project.currency} />}
                  tone={isNegative(report.margin) ? "negative" : "default"}
                  hint={report.margin_percent === null ? "No revenue yet — margin undefined" : `${formatPercent(report.margin_percent)} of revenue`}
                />
                <StatCard label="Unbilled value" value={<Money value={report.unbilled_value} currency={project.currency} />} hint="Billable work not yet invoiced" />
              </StatGrid>
              <StatGrid columns={4}>
                <StatCard
                  label="Hours logged"
                  value={<Quantity value={report.total_hours} unit="h" />}
                  hint={
                    report.budget_hours !== null ? (
                      <>
                        of <Quantity value={report.budget_hours} unit="h" /> budgeted
                      </>
                    ) : (
                      "No hours budget"
                    )
                  }
                  tone={isZero(report.hours_over_budget) ? "default" : "warning"}
                />
                <StatCard label="Awaiting approval" value={<Quantity value={report.pending_approval_hours} unit="h" />} />
                <StatCard label="Approved" value={<Quantity value={report.approved_hours} unit="h" />} hint="Including invoiced" />
                <StatCard label="Invoiced" value={<Quantity value={report.invoiced_hours} unit="h" />} />
              </StatGrid>
            </Section>
          ) : (
            <ErrorState
              compact
              title="Could not load profitability"
              message={profitability.ok ? "Unexpected response." : profitability.error.message}
              reference={profitability.ok ? null : referenceOf(profitability.error)}
            />
          )
        ) : null}

        <Card>
          <CardHeader title="Details" />
          <CardBody>
            <DetailList
              columns={3}
              items={[
                { label: "Code", value: <span className="tabular">{project.project_code}</span> },
                { label: "Customer", value: customerName ?? "Customer" },
                { label: "Billing method", value: BILLING_METHOD_LABELS[project.billing_method] ?? project.billing_method },
                ...(isHourly
                  ? [
                      { label: "Default hourly rate", value: <Money value={project.default_hourly_rate} currency={project.currency} /> },
                      {
                        label: "Invoiced as",
                        value: project.service_item
                          ? (items.get(project.service_item)?.name ?? "Service item")
                          : "Not set — needed to invoice time",
                      },
                    ]
                  : []),
                ...(project.billing_method === "fixed_fee"
                  ? [{ label: "Fixed fee", value: <Money value={project.fixed_fee_amount} currency={project.currency} /> }]
                  : []),
                { label: "Budget", value: <Money value={project.budget_amount} currency={project.currency} /> },
                { label: "Budget hours", value: <Quantity value={project.budget_hours} unit="h" /> },
                { label: "Start", value: formatDate(project.start_date) },
                { label: "End", value: project.end_date ? formatDate(project.end_date) : "Open" },
                { label: "Currency", value: project.currency },
                ...(project.description ? [{ label: "Description", value: <span className="whitespace-pre-line">{project.description}</span>, span: true }] : []),
                ...(project.notes ? [{ label: "Notes", value: <span className="whitespace-pre-line">{project.notes}</span>, span: true }] : []),
              ]}
            />
          </CardBody>
        </Card>

        <Section
          title="Tasks"
          description={isHourly ? "Time is logged against a task. Billable tasks can be invoiced." : "Time is logged against a task. On this project no time is invoiced."}
          {...(canManage
            ? { actions: <TaskDialogButton projectId={project.id} billingMethod={project.billing_method} currency={project.currency} /> }
            : {})}
        >
          <DataTable
            caption={`Tasks on ${project.name}`}
            columns={taskColumns}
            data={tasks.ok ? tasks.data : undefined}
            error={tasks.ok ? null : (tasks.error as ApiError)}
            getRowId={(task) => task.id}
            emptyTitle="No tasks yet"
            emptyDescription="Add a task before anyone can log time on this project."
            page={1}
            pageSize={200}
            buildPageHref={() => self}
          />
        </Section>

        <Section
          title="People"
          description="A person's project rate beats the task and project rates. Anyone in the organization can log time; assigning them sets their rates."
          {...(canManage
            ? {
                actions: (
                  <MemberDialogButton
                    projectId={project.id}
                    currency={project.currency}
                    assignedUserIds={memberRows.map((member) => member.user)}
                    canSeeCost={canSeeCost}
                  />
                ),
              }
            : {})}
        >
          <DataTable
            caption={`People on ${project.name}`}
            columns={memberColumns}
            data={members.ok ? members.data : undefined}
            error={members.ok ? null : (members.error as ApiError)}
            getRowId={(member) => member.id}
            emptyTitle="No one assigned"
            emptyDescription="Assign people to give them project-specific billable and cost rates."
            page={1}
            pageSize={200}
            buildPageHref={() => self}
          />
        </Section>

        {canInvoice ? (
          <Section
            title="Unbilled time"
            description={
              isHourly
                ? "Approved, billable hours not yet on an invoice — exactly what Invoice time will bill."
                : project.billing_method === "fixed_fee"
                  ? "Time on a fixed-fee project is not invoiced separately; raise an invoice for the agreed fee in Sales."
                  : "Time on a non-billable project is never invoiced."
            }
            {...(isHourly && unbilledRows.length > 0
              ? {
                  actions: (
                    <InvoiceTimeDialogButton
                      projectId={project.id}
                      projectCode={project.project_code}
                      upToDate={upToDate ?? null}
                      entryCount={unbilledRows.length}
                    />
                  ),
                }
              : {})}
          >
            {isHourly ? (
              <>
                <DateFilterForm
                  action={self}
                  fields={[{ name: "up_to_date", label: "Up to", value: upToDate }]}
                  {...(upToDate ? { clearHref: self } : {})}
                />
                <DataTable
                  caption={`Unbilled time on ${project.name}`}
                  columns={unbilledColumns}
                  data={unbilled?.ok ? wholeList(unbilledRows) : undefined}
                  error={unbilled && !unbilled.ok ? (unbilled.error as ApiError) : null}
                  getRowId={(entry) => entry.id}
                  emptyTitle="No approved time waiting to be billed"
                  emptyDescription="Time appears here once billable entries are submitted and approved."
                  page={1}
                  pageSize={Math.max(unbilledRows.length, 1)}
                  buildPageHref={() => self}
                />
              </>
            ) : null}
          </Section>
        ) : null}
      </PageBody>
    </>
  );
}
