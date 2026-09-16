"""Rate resolution — the ONE place the hourly-rate chain is defined.

Every time entry's `billable_rate` and `cost_rate` come from here and are
then frozen onto the entry (see models/time_entry.py). Resolving rates at
read time instead would mean a rate change silently restating the value of
work already invoiced.

BILLABLE RATE, most specific wins:

    1. the rate the caller passed explicitly for this entry
    2. ProjectMember.billable_rate  — this person, on this project
    3. Task.hourly_rate             — this kind of work, any person
    4. Project.default_hourly_rate  — this engagement's standard rate

Member beats task deliberately. A negotiated rate for a named individual is
the more specific agreement: if a client agreed £X/hour for a senior
engineer, that holds whichever task they touch.

COST RATE has no task or project fallback — only `ProjectMember.cost_rate`,
because what a person costs is a fact about the person, not about the work.
An unset cost rate resolves to None (treated as zero cost), which is honest:
guessing would put a fabricated number into a margin report.
"""

from decimal import Decimal

from core.exceptions import ApplicationError
from projects.models.project import BillingMethod, Project, ProjectMember, Task


def get_project_member(*, project: Project, user) -> ProjectMember | None:
    return ProjectMember.objects.filter(project=project, user=user).first()


def resolve_billable_rate(*, project: Project, task: Task, user, explicit_rate: Decimal | None = None) -> Decimal | None:
    """Returns the rate to charge for an hour, or None when none is defined.

    None is a legitimate answer (a non-billable project, or a billable one
    whose rates are not configured yet). Callers that MUST have a rate —
    invoicing — check for it and fail loudly rather than defaulting to zero
    and billing a customer nothing without saying so.
    """
    if explicit_rate is not None:
        if explicit_rate < 0:
            raise ApplicationError("Hourly rate cannot be negative.", code="rate_invalid")
        return explicit_rate

    member = get_project_member(project=project, user=user)
    if member is not None and member.billable_rate is not None:
        return member.billable_rate
    if task.hourly_rate is not None:
        return task.hourly_rate
    return project.default_hourly_rate


def resolve_cost_rate(*, project: Project, user, explicit_rate: Decimal | None = None) -> Decimal | None:
    if explicit_rate is not None:
        if explicit_rate < 0:
            raise ApplicationError("Cost rate cannot be negative.", code="rate_invalid")
        return explicit_rate
    member = get_project_member(project=project, user=user)
    return member.cost_rate if member is not None else None


def resolve_is_billable(*, project: Project, task: Task) -> bool:
    """Whether time on this task is chargeable.

    The project's billing method is an upper bound the task cannot escape:

    - NON_BILLABLE project -> never billable, whatever the task says. A
      single mis-ticked checkbox must not invoice a customer who agreed to
      pay nothing.
    - FIXED_FEE project -> hours are tracked for cost and profitability but
      are NOT separately chargeable; the agreed fee is the revenue, and
      billing the hours on top would double-charge. services/billing.py
      refuses to invoice time on such a project for the same reason.
    - HOURLY project -> the task's own `is_billable` decides.
    """
    if project.billing_method != BillingMethod.HOURLY:
        return False
    return task.is_billable
