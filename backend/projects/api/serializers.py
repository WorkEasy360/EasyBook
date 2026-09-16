"""Projects API serializers.

Same two conventions as sales/purchases: tenant-scoped references are plain
UUID fields resolved at request time (never a class-level queryset, which
would evaluate before tenant context exists), and create-serializers call a
domain service and hand back the instance for a read serializer to render.
"""

from decimal import Decimal

from rest_framework import serializers

from accounting.models.account import Account
from accounts.models import Currency
from core.exceptions import ApplicationError
from items.models.item import Item
from projects.models.project import BillingMethod, Project, ProjectMember, Task
from projects.models.time_entry import TimeEntry
from projects.services.billing import invoice_project_time
from projects.services.projects import (
    add_project_member,
    create_project,
    create_task,
    update_project,
    update_project_member,
    update_task,
)
from projects.services.time_entries import log_time, update_time_entry
from sales.models.customer import Customer


def _get_or_404(model, pk, label: str):
    try:
        return model.objects.get(pk=pk)
    except model.DoesNotExist:
        raise ApplicationError(
            f"{label} not found.", code=f"{label.lower().replace(' ', '_')}_not_found", status_code=404
        )


def _maybe(model, pk, label: str):
    return _get_or_404(model, pk, label) if pk else None


def _get_org_user_or_404(pk, organization):
    """Resolves a user who is an active member of THIS organization.

    `User` is global (not tenant-scoped), so a bare pk lookup would confirm
    the existence of an account in another organization. Scoping through
    Membership means a foreign user id is simply not found.
    """
    from accounts.models import Membership

    membership = Membership.all_objects.filter(
        organization=organization, user_id=pk, is_active=True
    ).select_related("user").first()
    if membership is None:
        raise ApplicationError("User not found in this organization.", code="user_not_found", status_code=404)
    return membership.user


# ------------------------------------------------------------ projects


class ProjectSerializer(serializers.ModelSerializer):
    class Meta:
        model = Project
        fields = [
            "id", "customer", "project_code", "name", "description", "status",
            "billing_method", "default_hourly_rate", "fixed_fee_amount", "service_item",
            "currency", "budget_hours", "budget_amount", "start_date", "end_date", "notes",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "status", "created_at", "updated_at"]


class ProjectCreateSerializer(serializers.Serializer):
    customer_id = serializers.UUIDField()
    project_code = serializers.CharField(max_length=32)
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    billing_method = serializers.ChoiceField(
        choices=BillingMethod.choices, required=False, default=BillingMethod.HOURLY
    )
    default_hourly_rate = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True, default=None
    )
    fixed_fee_amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True, default=None
    )
    service_item_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    currency = serializers.PrimaryKeyRelatedField(queryset=Currency.objects.all(), required=False, default=None)
    budget_hours = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True, default=None
    )
    budget_amount = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True, default=None
    )
    start_date = serializers.DateField(required=False, allow_null=True, default=None)
    end_date = serializers.DateField(required=False, allow_null=True, default=None)
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def create(self, validated_data):
        request = self.context["request"]
        return create_project(
            organization=request.organization,
            customer=_get_or_404(Customer, validated_data.pop("customer_id"), "Customer"),
            service_item=_maybe(Item, validated_data.pop("service_item_id"), "Item"),
            actor=request.user,
            **validated_data,
        )


class ProjectUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    default_hourly_rate = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True
    )
    fixed_fee_amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    service_item_id = serializers.UUIDField(required=False, allow_null=True)
    budget_hours = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    budget_amount = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    start_date = serializers.DateField(required=False, allow_null=True)
    end_date = serializers.DateField(required=False, allow_null=True)
    notes = serializers.CharField(required=False, allow_blank=True)

    def save(self, **kwargs):
        request = self.context["request"]
        data = dict(self.validated_data)
        if "service_item_id" in data:
            data["service_item"] = _maybe(Item, data.pop("service_item_id"), "Item")
        return update_project(project=self.context["project"], actor=request.user, **data)


# ------------------------------------------------------------- members


class ProjectMemberSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source="user.email", read_only=True)

    class Meta:
        model = ProjectMember
        fields = ["id", "project", "user", "user_email", "billable_rate", "cost_rate", "is_active",
                  "created_at", "updated_at"]
        read_only_fields = fields


class ProjectMemberCreateSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    billable_rate = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True, default=None
    )
    cost_rate = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True, default=None
    )

    def create(self, validated_data):
        request = self.context["request"]
        return add_project_member(
            project=self.context["project"],
            user=_get_org_user_or_404(validated_data.pop("user_id"), request.organization),
            actor=request.user,
            **validated_data,
        )


class ProjectMemberUpdateSerializer(serializers.Serializer):
    billable_rate = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    cost_rate = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False)

    def save(self, **kwargs):
        request = self.context["request"]
        return update_project_member(
            member=self.context["member"], actor=request.user, **self.validated_data
        )


# --------------------------------------------------------------- tasks


class TaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = ["id", "project", "name", "description", "is_billable", "is_active",
                  "hourly_rate", "service_item", "estimated_hours", "created_at", "updated_at"]
        read_only_fields = ["id", "project", "created_at", "updated_at"]


class TaskCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    is_billable = serializers.BooleanField(required=False, default=True)
    hourly_rate = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True, default=None
    )
    service_item_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    estimated_hours = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True, default=None
    )

    def create(self, validated_data):
        request = self.context["request"]
        return create_task(
            project=self.context["project"],
            service_item=_maybe(Item, validated_data.pop("service_item_id"), "Item"),
            actor=request.user,
            **validated_data,
        )


class TaskUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    is_billable = serializers.BooleanField(required=False)
    is_active = serializers.BooleanField(required=False)
    hourly_rate = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)
    service_item_id = serializers.UUIDField(required=False, allow_null=True)
    estimated_hours = serializers.DecimalField(max_digits=18, decimal_places=2, required=False, allow_null=True)

    def save(self, **kwargs):
        request = self.context["request"]
        data = dict(self.validated_data)
        if "service_item_id" in data:
            data["service_item"] = _maybe(Item, data.pop("service_item_id"), "Item")
        return update_task(task=self.context["task"], actor=request.user, **data)


# -------------------------------------------------------- time entries


class TimeEntrySerializer(serializers.ModelSerializer):
    billable_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)
    cost_amount = serializers.DecimalField(max_digits=18, decimal_places=2, read_only=True)

    class Meta:
        model = TimeEntry
        fields = [
            "id", "project", "task", "user", "entry_date", "hours", "description",
            "status", "is_billable", "billable_rate", "cost_rate",
            "billable_amount", "cost_amount",
            "submitted_at", "approved_by", "approved_at", "rejection_reason",
            "invoice_line", "created_at", "updated_at",
        ]
        read_only_fields = fields


class TimeEntryCreateSerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    task_id = serializers.UUIDField()
    entry_date = serializers.DateField()
    hours = serializers.DecimalField(max_digits=8, decimal_places=2)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    # Whose time this is. Omitted means "mine" — the common case, and the only
    # one a plain contributor is allowed (enforced in the view, not here).
    user_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    # `is_billable` is deliberately NOT accepted: it is resolved from the
    # project's billing method and the task (services/rates.py), so a client
    # cannot mark its own time chargeable.
    billable_rate = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True, default=None
    )
    cost_rate = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True, default=None
    )

    def create(self, validated_data):
        request = self.context["request"]
        project = _get_or_404(Project, validated_data.pop("project_id"), "Project")
        task = _get_or_404(Task, validated_data.pop("task_id"), "Task")
        user_id = validated_data.pop("user_id")
        user = _get_org_user_or_404(user_id, request.organization) if user_id else request.user
        return log_time(
            organization=request.organization,
            project=project,
            task=task,
            user=user,
            actor=request.user,
            **validated_data,
        )


class TimeEntryUpdateSerializer(serializers.Serializer):
    entry_date = serializers.DateField(required=False)
    hours = serializers.DecimalField(max_digits=8, decimal_places=2, required=False)
    description = serializers.CharField(required=False, allow_blank=True)
    task_id = serializers.UUIDField(required=False)

    def save(self, **kwargs):
        request = self.context["request"]
        data = dict(self.validated_data)
        if "task_id" in data:
            data["task"] = _get_or_404(Task, data.pop("task_id"), "Task")
        return update_time_entry(
            entry_id=self.context["entry"].id,
            organization=request.organization,
            actor=request.user,
            **data,
        )


class TimeEntryRejectSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True, default="")


class TimeEntryBulkSerializer(serializers.Serializer):
    entry_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False, max_length=500)


# ------------------------------------------------------------- billing


class InvoiceProjectTimeSerializer(serializers.Serializer):
    invoice_date = serializers.DateField()
    due_date = serializers.DateField()
    receivable_account_id = serializers.UUIDField()
    up_to_date = serializers.DateField(required=False, allow_null=True, default=None)
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, default=Decimal("0"))
    tax_payable_account_id = serializers.UUIDField(required=False, allow_null=True, default=None)
    reference = serializers.CharField(required=False, allow_blank=True, default="")
    notes = serializers.CharField(required=False, allow_blank=True, default="")

    def save(self, **kwargs):
        request = self.context["request"]
        data = self.validated_data
        return invoice_project_time(
            organization=request.organization,
            project=self.context["project"],
            invoice_date=data["invoice_date"],
            due_date=data["due_date"],
            receivable_account=_get_or_404(Account, data["receivable_account_id"], "Account"),
            up_to_date=data["up_to_date"],
            tax_rate=data["tax_rate"],
            tax_payable_account=_maybe(Account, data["tax_payable_account_id"], "Account"),
            reference=data["reference"],
            notes=data["notes"],
            actor=request.user,
        )


class TimesheetQuerySerializer(serializers.Serializer):
    from_date = serializers.DateField()
    to_date = serializers.DateField()
    user_id = serializers.UUIDField(required=False, allow_null=True, default=None)

    def validate(self, attrs):
        if attrs["to_date"] < attrs["from_date"]:
            raise serializers.ValidationError({"to_date": "Must not be before from_date."})
        return attrs
