from decimal import Decimal

from django.db import transaction

from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from items.models.item import ItemType
from projects.models.project import BillingMethod, Project, ProjectMember, ProjectStatus, Task
from sales.services.customers import assert_customer_usable_for_new_transaction

# A project may move only along these edges. There is no path back out of
# COMPLETED or CANCELLED: reopening would let new time attach to an
# engagement that has already been reported on and, for COMPLETED, invoiced.
# A follow-on piece of work is a new project.
_ALLOWED_TRANSITIONS = {
    ProjectStatus.DRAFT: {ProjectStatus.ACTIVE, ProjectStatus.CANCELLED},
    ProjectStatus.ACTIVE: {ProjectStatus.ON_HOLD, ProjectStatus.COMPLETED, ProjectStatus.CANCELLED},
    ProjectStatus.ON_HOLD: {ProjectStatus.ACTIVE, ProjectStatus.COMPLETED, ProjectStatus.CANCELLED},
}


def _validate_service_item(*, organization, item, field_name="service_item"):
    if item is None:
        return
    if item.organization_id != organization.id:
        raise ApplicationError(
            f"{field_name} must belong to the posting organization.", code="item_cross_org"
        )
    if item.item_type != ItemType.SERVICE:
        # Billing time as a PRODUCT would drag inventory into it: posting the
        # resulting invoice would try to issue stock for hours worked.
        raise ApplicationError(
            f"{field_name} must be a service item — time is not a stocked product.",
            code="service_item_required",
        )
    if not item.is_sellable:
        raise ApplicationError(f"{field_name} is not sellable.", code="item_not_sellable")


def _validate_billing_configuration(*, billing_method, fixed_fee_amount, default_hourly_rate):
    if billing_method == BillingMethod.FIXED_FEE:
        if fixed_fee_amount is None or fixed_fee_amount <= 0:
            raise ApplicationError(
                "A fixed-fee project needs a positive fixed_fee_amount.", code="fixed_fee_amount_required"
            )
    elif fixed_fee_amount is not None:
        raise ApplicationError(
            "fixed_fee_amount is only meaningful for a fixed-fee project.", code="fixed_fee_amount_unexpected"
        )
    if default_hourly_rate is not None and default_hourly_rate < 0:
        raise ApplicationError("default_hourly_rate cannot be negative.", code="rate_invalid")


def _get_project_for_update(*, project_id, organization) -> Project:
    try:
        return Project.objects.select_for_update().get(id=project_id, organization=organization)
    except Project.DoesNotExist:
        raise ApplicationError("Project not found.", code="project_not_found", status_code=404)


@transaction.atomic
def create_project(
    *,
    organization,
    customer,
    project_code: str,
    name: str,
    currency=None,
    billing_method: str = BillingMethod.HOURLY,
    default_hourly_rate: Decimal | None = None,
    fixed_fee_amount: Decimal | None = None,
    service_item=None,
    budget_hours: Decimal | None = None,
    budget_amount: Decimal | None = None,
    start_date=None,
    end_date=None,
    description: str = "",
    notes: str = "",
    actor=None,
) -> Project:
    if customer.organization_id != organization.id:
        raise ApplicationError("Customer must belong to the posting organization.", code="customer_cross_org")
    assert_customer_usable_for_new_transaction(customer=customer)
    if billing_method not in BillingMethod.values:
        raise ApplicationError(f"Unknown billing method '{billing_method}'.", code="billing_method_invalid")
    _validate_billing_configuration(
        billing_method=billing_method, fixed_fee_amount=fixed_fee_amount, default_hourly_rate=default_hourly_rate
    )
    _validate_service_item(organization=organization, item=service_item)
    if start_date and end_date and end_date < start_date:
        raise ApplicationError("end_date cannot be before start_date.", code="project_end_before_start")

    project = Project.objects.create(
        organization=organization,
        customer=customer,
        project_code=project_code,
        name=name,
        description=description,
        billing_method=billing_method,
        default_hourly_rate=default_hourly_rate,
        fixed_fee_amount=fixed_fee_amount,
        service_item=service_item,
        currency=currency or customer.currency,
        budget_hours=budget_hours,
        budget_amount=budget_amount,
        start_date=start_date,
        end_date=end_date,
        notes=notes,
        created_by=actor,
    )
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="projects.Project",
        object_id=project.id,
        changes={"project_code": project_code, "name": name, "billing_method": billing_method},
    )
    return project


@transaction.atomic
def update_project(*, project: Project, actor=None, **fields) -> Project:
    """Header edits. `billing_method` is deliberately NOT editable here: time
    already logged carries billability and rates frozen under the OLD method,
    so flipping it would leave a project whose entries disagree with its own
    rules. Changing how an engagement bills is a new project."""
    if "billing_method" in fields and fields["billing_method"] != project.billing_method:
        raise ApplicationError(
            "A project's billing method cannot be changed after creation — "
            "existing time entries were resolved under the current method.",
            code="billing_method_immutable",
        )
    fields.pop("billing_method", None)

    if "service_item" in fields:
        _validate_service_item(organization=project.organization, item=fields["service_item"])
    _validate_billing_configuration(
        billing_method=project.billing_method,
        fixed_fee_amount=fields.get("fixed_fee_amount", project.fixed_fee_amount),
        default_hourly_rate=fields.get("default_hourly_rate", project.default_hourly_rate),
    )

    changes = {}
    for field, value in fields.items():
        if getattr(project, field) == value:
            continue
        changes[field] = str(value.pk) if hasattr(value, "pk") else value
        setattr(project, field, value)
    if not changes:
        return project

    project.save(update_fields=[*changes.keys(), "updated_at"])
    record_audit(
        organization_id=project.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="projects.Project",
        object_id=project.id,
        changes=changes,
    )
    return project


def _apply_transition(*, project: Project, to_status: str, organization, actor=None) -> Project:
    allowed = _ALLOWED_TRANSITIONS.get(project.status, set())
    if to_status not in allowed:
        raise ApplicationError(
            f"Cannot move project from '{project.status}' to '{to_status}'.", code="project_invalid_status"
        )
    from_status = project.status
    project.status = to_status
    project.save(update_fields=["status", "updated_at"])
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="projects.Project",
        object_id=project.id,
        changes={"status": {"from": from_status, "to": to_status}},
    )
    return project


@transaction.atomic
def activate_project(*, project_id, organization, actor=None) -> Project:
    project = _get_project_for_update(project_id=project_id, organization=organization)
    return _apply_transition(
        project=project, to_status=ProjectStatus.ACTIVE, organization=organization, actor=actor
    )


@transaction.atomic
def hold_project(*, project_id, organization, actor=None) -> Project:
    project = _get_project_for_update(project_id=project_id, organization=organization)
    return _apply_transition(
        project=project, to_status=ProjectStatus.ON_HOLD, organization=organization, actor=actor
    )


@transaction.atomic
def complete_project(*, project_id, organization, actor=None) -> Project:
    """Closes the project to new time. Unbilled approved time is NOT swept up
    or written off here — it stays exactly as it is, visible to the
    profitability report, because quietly discarding billable work at
    completion is how revenue goes missing."""
    project = _get_project_for_update(project_id=project_id, organization=organization)
    return _apply_transition(
        project=project, to_status=ProjectStatus.COMPLETED, organization=organization, actor=actor
    )


@transaction.atomic
def cancel_project(*, project_id, organization, actor=None) -> Project:
    project = _get_project_for_update(project_id=project_id, organization=organization)
    from projects.models.time_entry import TimeEntry, TimeEntryStatus

    if TimeEntry.objects.filter(project=project, status=TimeEntryStatus.INVOICED).exists():
        raise ApplicationError(
            "A project with invoiced time cannot be cancelled — complete it instead.",
            code="project_has_invoiced_time",
        )
    return _apply_transition(
        project=project, to_status=ProjectStatus.CANCELLED, organization=organization, actor=actor
    )


def assert_project_accepts_time(*, project: Project) -> None:
    """The shared guard every time-logging path calls. Only an ACTIVE project
    accepts new time: a draft one has not started, and held/completed/
    cancelled ones have stopped."""
    if project.status != ProjectStatus.ACTIVE:
        raise ApplicationError(
            f"Time cannot be logged against a project in status '{project.status}'.",
            code="project_not_active",
        )


# ------------------------------------------------------------- members


@transaction.atomic
def add_project_member(
    *, project: Project, user, billable_rate=None, cost_rate=None, actor=None
) -> ProjectMember:
    if billable_rate is not None and billable_rate < 0:
        raise ApplicationError("billable_rate cannot be negative.", code="rate_invalid")
    if cost_rate is not None and cost_rate < 0:
        raise ApplicationError("cost_rate cannot be negative.", code="rate_invalid")

    from accounts.models import Membership

    if not Membership.all_objects.filter(
        organization=project.organization, user=user, is_active=True
    ).exists():
        # Assigning a non-member would create a person who can be billed for
        # but can never log in to record the work.
        raise ApplicationError(
            "Only an active member of this organization can be assigned to a project.",
            code="user_not_org_member",
        )

    member, created = ProjectMember.objects.get_or_create(
        organization=project.organization,
        project=project,
        user=user,
        defaults={"billable_rate": billable_rate, "cost_rate": cost_rate},
    )
    if not created:
        raise ApplicationError("This user is already assigned to the project.", code="member_already_assigned")

    record_audit(
        organization_id=project.organization_id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="projects.ProjectMember",
        object_id=member.id,
        changes={"project_id": str(project.id), "user_id": str(user.id)},
    )
    return member


@transaction.atomic
def update_project_member(*, member: ProjectMember, actor=None, **fields) -> ProjectMember:
    """Rate changes apply to time logged FROM NOW ON. Existing entries keep
    the rates frozen onto them at creation — see models/time_entry.py."""
    for rate_field in ("billable_rate", "cost_rate"):
        value = fields.get(rate_field)
        if value is not None and value < 0:
            raise ApplicationError(f"{rate_field} cannot be negative.", code="rate_invalid")

    changes = {}
    for field, value in fields.items():
        if getattr(member, field) == value:
            continue
        changes[field] = str(value.pk) if hasattr(value, "pk") else value
        setattr(member, field, value)
    if not changes:
        return member

    member.save(update_fields=[*changes.keys(), "updated_at"])
    record_audit(
        organization_id=member.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="projects.ProjectMember",
        object_id=member.id,
        changes=changes,
    )
    return member


# --------------------------------------------------------------- tasks


@transaction.atomic
def create_task(
    *,
    project: Project,
    name: str,
    description: str = "",
    is_billable: bool = True,
    hourly_rate: Decimal | None = None,
    service_item=None,
    estimated_hours: Decimal | None = None,
    actor=None,
) -> Task:
    if hourly_rate is not None and hourly_rate < 0:
        raise ApplicationError("hourly_rate cannot be negative.", code="rate_invalid")
    _validate_service_item(organization=project.organization, item=service_item)

    task = Task.objects.create(
        organization=project.organization,
        project=project,
        name=name,
        description=description,
        is_billable=is_billable,
        hourly_rate=hourly_rate,
        service_item=service_item,
        estimated_hours=estimated_hours,
    )
    record_audit(
        organization_id=project.organization_id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="projects.Task",
        object_id=task.id,
        changes={"project_id": str(project.id), "name": name},
    )
    return task


@transaction.atomic
def update_task(*, task: Task, actor=None, **fields) -> Task:
    if "hourly_rate" in fields and fields["hourly_rate"] is not None and fields["hourly_rate"] < 0:
        raise ApplicationError("hourly_rate cannot be negative.", code="rate_invalid")
    if "service_item" in fields:
        _validate_service_item(organization=task.organization, item=fields["service_item"])

    changes = {}
    for field, value in fields.items():
        if getattr(task, field) == value:
            continue
        changes[field] = str(value.pk) if hasattr(value, "pk") else value
        setattr(task, field, value)
    if not changes:
        return task

    task.save(update_fields=[*changes.keys(), "updated_at"])
    record_audit(
        organization_id=task.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="projects.Task",
        object_id=task.id,
        changes=changes,
    )
    return task


def resolve_service_item(*, project: Project, task: Task):
    """The item billable time is invoiced as: the task's override, else the
    project's. Returns None when neither is set — services/billing.py turns
    that into an explicit error rather than inventing an item."""
    return task.service_item or project.service_item
