from decimal import Decimal

from django.conf import settings
from django.db import models

from core.models import TenantScopedModel


class ProjectStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    ON_HOLD = "on_hold", "On Hold"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class BillingMethod(models.TextChoices):
    """How time on this project converts to money.

    Deliberately explicit per project rather than inferred: the same customer
    routinely has one fixed-fee engagement and one time-and-materials one, and
    guessing wrong means either billing for unbillable work or silently
    dropping billable hours.
    """

    # Hours are tracked for cost and reporting but never invoiced.
    NON_BILLABLE = "non_billable", "Non-billable"
    # Every billable hour is invoiced at a resolved rate — see
    # services/rates.py for the resolution chain.
    HOURLY = "hourly", "Hourly rate"
    # A single agreed price. Hours are still tracked (profitability depends
    # on them) but they do NOT drive the invoice amount; billing the hours
    # of a fixed-fee project would double-charge the customer, so
    # services/billing.py refuses it outright.
    FIXED_FEE = "fixed_fee", "Fixed fee"


class Project(TenantScopedModel):
    """A unit of client work that time and cost accrue against.

    Posts NO accounting journals of its own. Time is not a financial event
    until it is invoiced, and at that point `sales` owns the posting — see
    projects/CLAUDE.md. Project expenses likewise post through `purchases`.
    """

    customer = models.ForeignKey("sales.Customer", on_delete=models.PROTECT, related_name="projects")
    project_code = models.CharField(max_length=32)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=ProjectStatus.choices, default=ProjectStatus.DRAFT)

    billing_method = models.CharField(
        max_length=16, choices=BillingMethod.choices, default=BillingMethod.HOURLY
    )
    # The project-level fallback in the rate resolution chain. Nullable
    # because a NON_BILLABLE project has no rate, and an HOURLY one may rely
    # entirely on per-member rates.
    default_hourly_rate = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    # Only meaningful for FIXED_FEE. Validated by services/projects.py.
    fixed_fee_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    # The service Item billable time is invoiced as. A sales InvoiceLine
    # requires an item (it snapshots description/HSN/tax from it), so time
    # cannot be invoiced without one — this is where it comes from, unless a
    # Task overrides it. Nullable so a NON_BILLABLE project need not carry
    # one; required at billing time, not at creation.
    service_item = models.ForeignKey(
        "items.Item", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )

    currency = models.ForeignKey("accounts.Currency", on_delete=models.PROTECT, related_name="+")
    budget_hours = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    budget_amount = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["organization", "project_code"], name="uniq_project_code_per_org"),
            models.CheckConstraint(
                condition=models.Q(default_hourly_rate__isnull=True) | models.Q(default_hourly_rate__gte=Decimal("0")),
                name="project_default_rate_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(end_date__isnull=True)
                | models.Q(start_date__isnull=True)
                | models.Q(end_date__gte=models.F("start_date")),
                name="project_end_after_start",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "status"]),
            models.Index(fields=["organization", "customer"]),
        ]
        ordering = ["name"]

    def __str__(self):
        return f"{self.project_code} {self.name}".strip()

    @property
    def is_billable(self) -> bool:
        return self.billing_method != BillingMethod.NON_BILLABLE


class ProjectMember(TenantScopedModel):
    """A user assigned to a project, with the rates that apply to their time.

    Two separate rates, because profitability is meaningless without both:
    `billable_rate` is what the CUSTOMER is charged, `cost_rate` is what the
    person costs US. Conflating them (the common shortcut) makes every margin
    figure equal to revenue.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="project_memberships")

    billable_rate = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    cost_rate = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "user"], name="uniq_project_member"),
            models.CheckConstraint(
                condition=models.Q(billable_rate__isnull=True) | models.Q(billable_rate__gte=Decimal("0")),
                name="project_member_billable_rate_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(cost_rate__isnull=True) | models.Q(cost_rate__gte=Decimal("0")),
                name="project_member_cost_rate_nonnegative",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "project"]),
            models.Index(fields=["organization", "user"]),
        ]
        ordering = ["project", "user"]

    def __str__(self):
        return f"{self.project_id}:{self.user_id}"


class Task(TenantScopedModel):
    """A unit of work within a project that time is logged against.

    `is_billable` here is an upper bound, not an override: a task on a
    NON_BILLABLE project is never billable regardless of this flag (see
    services/time_entries.py). Modelling it the other way — letting a task
    opt into billing on a non-billable project — would let a single
    mis-ticked checkbox invoice a customer who agreed to pay nothing.
    """

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_billable = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    # Task-level rate override, above the project default but below the
    # per-member rate — see services/rates.py for the full chain.
    hourly_rate = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)
    # Overrides Project.service_item for time logged against this task.
    service_item = models.ForeignKey(
        "items.Item", null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    estimated_hours = models.DecimalField(max_digits=18, decimal_places=2, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["project", "name"], name="uniq_task_name_per_project"),
            models.CheckConstraint(
                condition=models.Q(hourly_rate__isnull=True) | models.Q(hourly_rate__gte=Decimal("0")),
                name="task_hourly_rate_nonnegative",
            ),
        ]
        indexes = [
            models.Index(fields=["organization", "project", "is_active"]),
        ]
        ordering = ["project", "name"]

    def __str__(self):
        return self.name
