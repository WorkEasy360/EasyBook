"""Time entry lifecycle: DRAFT -> SUBMITTED -> APPROVED/REJECTED -> INVOICED.

Logging time posts NO accounting journal. Hours become money only when
approved billable time is invoiced, and `sales` does that posting — see
services/billing.py and projects/CLAUDE.md.

A REJECTED entry returns to DRAFT when edited, rather than being deleted:
the hours were really worked, and the argument is about how they are
recorded. Deleting them loses the cost side too.
"""

from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from projects.models.project import Project, Task
from projects.models.time_entry import TimeEntry, TimeEntryStatus
from projects.services.projects import assert_project_accepts_time
from projects.services.rates import resolve_billable_rate, resolve_cost_rate, resolve_is_billable

# Statuses a user may still edit the substance of. INVOICED is absent by
# design and APPROVED is too — re-editing approved hours would invalidate the
# approval silently.
_EDITABLE_STATUSES = {TimeEntryStatus.DRAFT, TimeEntryStatus.REJECTED}

_ALLOWED_TRANSITIONS = {
    TimeEntryStatus.DRAFT: {TimeEntryStatus.SUBMITTED},
    TimeEntryStatus.SUBMITTED: {TimeEntryStatus.APPROVED, TimeEntryStatus.REJECTED, TimeEntryStatus.DRAFT},
    # An approver may change their mind while the entry is still unbilled.
    TimeEntryStatus.APPROVED: {TimeEntryStatus.REJECTED},
    TimeEntryStatus.REJECTED: {TimeEntryStatus.DRAFT, TimeEntryStatus.SUBMITTED},
}


def _get_entry_for_update(*, entry_id, organization) -> TimeEntry:
    try:
        return TimeEntry.objects.select_for_update().get(id=entry_id, organization=organization)
    except TimeEntry.DoesNotExist:
        raise ApplicationError("Time entry not found.", code="time_entry_not_found", status_code=404)


def _validate_task_belongs_to_project(*, project: Project, task: Task) -> None:
    if task.project_id != project.id:
        raise ApplicationError("Task does not belong to this project.", code="task_project_mismatch")
    if not task.is_active:
        raise ApplicationError("Time cannot be logged against an inactive task.", code="task_inactive")


def _validate_hours(hours: Decimal) -> None:
    if hours <= 0:
        raise ApplicationError("Hours must be positive.", code="hours_invalid")
    if hours > Decimal("24"):
        raise ApplicationError("A single entry cannot exceed 24 hours.", code="hours_exceed_day")


@transaction.atomic
def log_time(
    *,
    organization,
    project: Project,
    task: Task,
    user,
    entry_date,
    hours: Decimal,
    description: str = "",
    billable_rate: Decimal | None = None,
    cost_rate: Decimal | None = None,
    actor=None,
) -> TimeEntry:
    """Records hours. Billability and both rates are RESOLVED here and frozen
    onto the entry — the client never dictates whether its own time is
    billable (see services/rates.py::resolve_is_billable)."""
    if project.organization_id != organization.id:
        raise ApplicationError("Project must belong to the posting organization.", code="project_cross_org")
    assert_project_accepts_time(project=project)
    _validate_task_belongs_to_project(project=project, task=task)
    _validate_hours(hours)

    if project.start_date and entry_date < project.start_date:
        raise ApplicationError(
            "Time cannot be logged before the project start date.", code="entry_date_before_project_start"
        )
    if project.end_date and entry_date > project.end_date:
        raise ApplicationError(
            "Time cannot be logged after the project end date.", code="entry_date_after_project_end"
        )

    is_billable = resolve_is_billable(project=project, task=task)
    resolved_billable_rate = (
        resolve_billable_rate(project=project, task=task, user=user, explicit_rate=billable_rate)
        if is_billable
        else None
    )
    resolved_cost_rate = resolve_cost_rate(project=project, user=user, explicit_rate=cost_rate)

    entry = TimeEntry.objects.create(
        organization=organization,
        project=project,
        task=task,
        user=user,
        entry_date=entry_date,
        hours=hours,
        description=description,
        is_billable=is_billable,
        billable_rate=resolved_billable_rate,
        cost_rate=resolved_cost_rate,
        created_by=actor,
    )
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="projects.TimeEntry",
        object_id=entry.id,
        changes={
            "project_id": str(project.id), "task_id": str(task.id), "user_id": str(user.id),
            "hours": str(hours), "is_billable": is_billable,
        },
    )
    return entry


@transaction.atomic
def update_time_entry(*, entry_id, organization, actor=None, **fields) -> TimeEntry:
    """Edits a DRAFT or REJECTED entry, re-resolving rates if the task
    changed. A REJECTED entry returns to DRAFT so it must be resubmitted —
    an edit is not an approval."""
    entry = _get_entry_for_update(entry_id=entry_id, organization=organization)
    if entry.status not in _EDITABLE_STATUSES:
        raise ApplicationError(
            f"A time entry in status '{entry.status}' cannot be edited.", code="time_entry_not_editable"
        )

    if "hours" in fields:
        _validate_hours(fields["hours"])
    if "task" in fields:
        _validate_task_belongs_to_project(project=entry.project, task=fields["task"])

    changes = {}
    for field in ("entry_date", "hours", "description", "task"):
        if field not in fields:
            continue
        value = fields[field]
        if getattr(entry, field) == value:
            continue
        changes[field] = str(value.pk) if hasattr(value, "pk") else value
        setattr(entry, field, value)

    # The task may now imply a different billability and rate.
    if "task" in changes:
        entry.is_billable = resolve_is_billable(project=entry.project, task=entry.task)
        entry.billable_rate = (
            resolve_billable_rate(project=entry.project, task=entry.task, user=entry.user)
            if entry.is_billable
            else None
        )
        changes["is_billable"] = entry.is_billable

    was_rejected = entry.status == TimeEntryStatus.REJECTED
    if was_rejected:
        entry.status = TimeEntryStatus.DRAFT
        entry.rejection_reason = ""
        changes["status"] = TimeEntryStatus.DRAFT

    if not changes:
        return entry

    entry.save(
        update_fields=[
            *{*changes.keys(), "is_billable", "billable_rate", "status", "rejection_reason"},
            "updated_at",
        ]
    )
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="projects.TimeEntry",
        object_id=entry.id,
        changes=changes,
    )
    return entry


@transaction.atomic
def delete_time_entry(*, entry_id, organization, actor=None) -> None:
    entry = _get_entry_for_update(entry_id=entry_id, organization=organization)
    if entry.status == TimeEntryStatus.INVOICED:
        raise ApplicationError("Invoiced time entries cannot be deleted.", code="time_entry_invoiced")
    entry_pk = entry.id
    entry.delete()
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.DELETE,
        object_type="projects.TimeEntry",
        object_id=entry_pk,
        changes={},
    )


def _apply_transition(*, entry: TimeEntry, to_status: str, organization, actor=None, **extra) -> TimeEntry:
    allowed = _ALLOWED_TRANSITIONS.get(entry.status, set())
    if to_status not in allowed:
        raise ApplicationError(
            f"Cannot move time entry from '{entry.status}' to '{to_status}'.",
            code="time_entry_invalid_status",
        )
    from_status = entry.status
    entry.status = to_status
    update_fields = ["status", "updated_at"]
    for field, value in extra.items():
        setattr(entry, field, value)
        update_fields.append(field)
    entry.save(update_fields=update_fields)
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="projects.TimeEntry",
        object_id=entry.id,
        changes={"status": {"from": from_status, "to": to_status}},
    )
    return entry


@transaction.atomic
def submit_time_entry(*, entry_id, organization, actor=None) -> TimeEntry:
    entry = _get_entry_for_update(entry_id=entry_id, organization=organization)
    if entry.status == TimeEntryStatus.SUBMITTED:
        return entry
    return _apply_transition(
        entry=entry, to_status=TimeEntryStatus.SUBMITTED, organization=organization, actor=actor,
        submitted_at=timezone.now(),
    )


@transaction.atomic
def approve_time_entry(*, entry_id, organization, actor=None) -> TimeEntry:
    """Approval is what makes billable time invoiceable.

    Nobody approves their own timesheet. That check lives here as well as in
    the permission matrix, because a single owner/admin account holds both
    LOG_TIME and APPROVE_TIME and the role check alone would wave it through.
    """
    entry = _get_entry_for_update(entry_id=entry_id, organization=organization)
    if entry.status == TimeEntryStatus.APPROVED:
        return entry
    if actor is not None and entry.user_id == getattr(actor, "id", None):
        raise ApplicationError(
            "You cannot approve your own time entry.", code="self_approval_forbidden", status_code=403
        )
    return _apply_transition(
        entry=entry, to_status=TimeEntryStatus.APPROVED, organization=organization, actor=actor,
        approved_by=actor, approved_at=timezone.now(), rejection_reason="",
    )


@transaction.atomic
def reject_time_entry(*, entry_id, organization, actor=None, reason: str = "") -> TimeEntry:
    entry = _get_entry_for_update(entry_id=entry_id, organization=organization)
    if entry.status == TimeEntryStatus.REJECTED:
        return entry
    if entry.status == TimeEntryStatus.INVOICED:
        raise ApplicationError(
            "Invoiced time cannot be rejected — credit the invoice instead.", code="time_entry_invoiced"
        )
    return _apply_transition(
        entry=entry, to_status=TimeEntryStatus.REJECTED, organization=organization, actor=actor,
        rejection_reason=reason, approved_by=None, approved_at=None,
    )


@transaction.atomic
def bulk_submit(*, organization, entry_ids, actor=None) -> list[TimeEntry]:
    """Submits a week's worth of entries in one operation — the shape a
    timesheet UI actually needs. Atomic: one invalid entry rolls the whole
    submission back rather than leaving half a week submitted."""
    submitted = []
    for entry_id in entry_ids:
        submitted.append(submit_time_entry(entry_id=entry_id, organization=organization, actor=actor))
    return submitted


@transaction.atomic
def bulk_approve(*, organization, entry_ids, actor=None) -> list[TimeEntry]:
    approved = []
    for entry_id in entry_ids:
        approved.append(approve_time_entry(entry_id=entry_id, organization=organization, actor=actor))
    return approved
