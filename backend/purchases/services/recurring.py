"""Recurring bill and recurring expense generation.

Both generators are Celery entry points (see tasks.py), never invoked inline
from a web request, and both generate DRAFT documents only — a human
reviews and posts. Neither posts accounting or moves stock.

Like sales' recurring generation, the sweep is NOT tenant-scoped (a
scheduled task spans every organization) so it enumerates
`accounts.Organization` and opens `tenant_context()` per organization. RLS
is enforced at the Postgres level regardless of which Django manager is
used, so there is no way to query these templates across every tenant at
once — see sales/CLAUDE.md for the full reasoning and the
`TransactionTestCase` requirement it imposes on tests.
"""

import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounting.services.currency import assert_base_currency
from audit.models import AuditLog
from audit.services import record as record_audit
from core.enums import RecurringFrequency
from core.exceptions import ApplicationError
from core.recurrence import advance_occurrence
from core.tenancy import tenant_context
from purchases.models.bill import Bill
from purchases.models.expense import Expense
from purchases.models.recurring import (
    RecurringBillRun,
    RecurringBillTemplate,
    RecurringBillTemplateLine,
    RecurringExpenseRun,
    RecurringExpenseTemplate,
)
from purchases.models.vendor import Vendor
from purchases.services.bills import create_bill
from purchases.services.expenses import create_expense
from purchases.services.line_items import is_inventoried, validate_item_for_line
from purchases.services.vendors import assert_vendor_usable_for_new_transaction
from tax.services.documents import carry_forward_tax, resolve_document_tax


def _validate_template_line(*, organization, line: dict) -> dict:
    item = line["item"]
    validate_item_for_line(organization=organization, item=item)
    if is_inventoried(item):
        # See models/recurring.py: a recurring purchase is a subscription or
        # retainer, not a goods delivery. Generating a DRAFT bill that would
        # receive stock on a schedule — with no goods receipt and no goods —
        # would silently inflate inventory every period. Rejected here rather
        # than only documented.
        raise ApplicationError(
            "Inventory-tracked items cannot appear on a recurring bill template — "
            "stock arrives on a goods receipt, not on a schedule.",
            code="recurring_item_inventoried",
        )
    quantity = line["quantity"]
    if quantity <= 0:
        raise ApplicationError("Line quantity must be positive.", code="line_quantity_invalid")
    return {
        "item": item,
        "expense_account": line.get("expense_account"),
        "description": line.get("description", ""),
        "quantity": quantity,
        "unit_price": line["unit_price"],
        "discount_percent": line.get("discount_percent", 0),
        "tax_rate": line.get("tax_rate", 0),
    }


def _validate_schedule(*, frequency, start_date, end_date):
    if frequency not in RecurringFrequency.values:
        raise ApplicationError(f"Unknown recurrence frequency '{frequency}'.", code="recurring_frequency_invalid")
    if end_date is not None and end_date < start_date:
        raise ApplicationError("end_date cannot be before start_date.", code="recurring_end_before_start")


# ---------------------------------------------------------------- bills


@transaction.atomic
def create_recurring_bill_template(
    *,
    organization,
    vendor: Vendor,
    frequency: str,
    start_date,
    lines: list[dict],
    payable_account,
    end_date=None,
    due_days: int = 0,
    tax_recoverable_account=None,
    currency=None,
    exchange_rate=1,
    reference: str = "",
    notes: str = "",
    is_active: bool = True,
    place_of_supply=None,
    actor=None,
) -> RecurringBillTemplate:
    if vendor.organization_id != organization.id:
        raise ApplicationError("Vendor must belong to the posting organization.", code="vendor_cross_org")
    assert_vendor_usable_for_new_transaction(vendor=vendor)
    _validate_schedule(frequency=frequency, start_date=start_date, end_date=end_date)
    if not lines:
        raise ApplicationError("A recurring bill template needs at least one line.", code="recurring_no_lines")

    currency = currency or vendor.currency
    assert_base_currency(organization=organization, currency=currency, exchange_rate=exchange_rate)
    validated_lines = [_validate_template_line(organization=organization, line=line) for line in lines]

    tax_treatment = resolve_document_tax(
        organization=organization, party=vendor, place_of_supply=place_of_supply
    )

    template = RecurringBillTemplate.objects.create(
        organization=organization,
        vendor=vendor,
        **tax_treatment,
        frequency=frequency,
        start_date=start_date,
        end_date=end_date,
        next_run_at=start_date,
        is_active=is_active,
        due_days=due_days,
        payable_account=payable_account,
        tax_recoverable_account=tax_recoverable_account,
        currency=currency,
        exchange_rate=exchange_rate,
        reference=reference,
        notes=notes,
        created_by=actor,
    )
    for index, row in enumerate(validated_lines, start=1):
        RecurringBillTemplateLine.objects.create(
            organization=organization, template=template, line_number=index, **row
        )

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="purchases.RecurringBillTemplate",
        object_id=template.id,
        changes={"frequency": frequency, "start_date": str(start_date)},
    )
    return template


@transaction.atomic
def update_recurring_bill_template(
    *, template: RecurringBillTemplate, lines: list[dict] | None = None, actor=None, **fields
) -> RecurringBillTemplate:
    changes = {}
    assert_base_currency(
        organization=template.organization, currency=fields.get("currency"), exchange_rate=fields.get("exchange_rate")
    )
    for field, value in fields.items():
        if getattr(template, field) == value:
            continue
        changes[field] = str(value.pk) if hasattr(value, "pk") else value
        setattr(template, field, value)
    if changes:
        template.save(update_fields=[*changes.keys(), "updated_at"])

    if lines is not None:
        if not lines:
            raise ApplicationError("A recurring bill template needs at least one line.", code="recurring_no_lines")
        validated_lines = [_validate_template_line(organization=template.organization, line=line) for line in lines]
        template.lines.all().delete()
        for index, row in enumerate(validated_lines, start=1):
            RecurringBillTemplateLine.objects.create(
                organization=template.organization, template=template, line_number=index, **row
            )
        changes["line_count"] = len(validated_lines)

    if changes:
        record_audit(
            organization_id=template.organization_id,
            actor=actor,
            action=AuditLog.Action.UPDATE,
            object_type="purchases.RecurringBillTemplate",
            object_id=template.id,
            changes=changes,
        )
    return template


def activate_bill_template(*, template: RecurringBillTemplate, actor=None) -> RecurringBillTemplate:
    return update_recurring_bill_template(template=template, is_active=True, actor=actor)


def deactivate_bill_template(*, template: RecurringBillTemplate, actor=None) -> RecurringBillTemplate:
    return update_recurring_bill_template(template=template, is_active=False, actor=actor)


@transaction.atomic
def _generate_one_bill(*, template_id, organization, actor=None) -> Bill | None:
    """Generates at most one DRAFT bill for a template's current
    next_run_at. Locks the template row so concurrent task execution can't
    generate the same occurrence twice; the unique constraint on
    RecurringBillRun is the authoritative backstop if it somehow does."""
    template = RecurringBillTemplate.objects.select_for_update().filter(pk=template_id).first()
    if template is None or not template.is_active:
        return None

    occurrence_date = template.next_run_at
    if template.end_date and occurrence_date > template.end_date:
        return None

    lines = [
        {
            "item": line.item, "expense_account": line.expense_account, "description": line.description,
            "quantity": line.quantity, "unit_price": line.unit_price,
            "discount_percent": line.discount_percent, "tax_rate": line.tax_rate,
        }
        for line in template.lines.select_related("item", "expense_account").all()
    ]

    # Each occurrence inherits the template's treatment rather than
    # re-determining it per run: a template generating monthly for two years
    # must not silently change the tax it charges because the vendor's master
    # data was edited in month seven.
    carried = carry_forward_tax(template)

    bill = create_bill(
        organization=organization,
        vendor=template.vendor,
        tax_treatment={k: v for k, v in carried.items() if k != "is_reverse_charge"},
        is_reverse_charge=carried["is_reverse_charge"],
        bill_date=occurrence_date,
        due_date=occurrence_date + datetime.timedelta(days=template.due_days),
        payable_account=template.payable_account,
        tax_recoverable_account=template.tax_recoverable_account,
        currency=template.currency,
        exchange_rate=template.exchange_rate,
        reference=template.reference,
        notes=template.notes,
        lines=lines,
        # Deliberately NO vendor_bill_number: the vendor's own document
        # number is on paper we have not received yet. Inventing one would
        # trip the duplicate-bill constraint on the very next occurrence.
        actor=actor,
    )

    try:
        RecurringBillRun.objects.create(
            organization=organization, template=template, occurrence_date=occurrence_date, bill=bill
        )
    except IntegrityError:
        raise ApplicationError(
            "This occurrence has already been generated.", code="recurring_occurrence_duplicate"
        )

    template.next_run_at = advance_occurrence(occurrence_date, template.frequency)
    template.save(update_fields=["next_run_at", "updated_at"])

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="purchases.Bill",
        object_id=bill.id,
        changes={
            "source": "recurring_bill_template", "template_id": str(template.id),
            "occurrence_date": str(occurrence_date),
        },
    )
    return bill


# ------------------------------------------------------------- expenses


@transaction.atomic
def create_recurring_expense_template(
    *,
    organization,
    frequency: str,
    start_date,
    amount,
    expense_account,
    paid_through_account,
    vendor: Vendor | None = None,
    end_date=None,
    tax_rate=0,
    tax_recoverable_account=None,
    currency=None,
    exchange_rate=1,
    description: str = "",
    reference: str = "",
    notes: str = "",
    is_active: bool = True,
    place_of_supply=None,
    actor=None,
) -> RecurringExpenseTemplate:
    if vendor is not None:
        if vendor.organization_id != organization.id:
            raise ApplicationError("Vendor must belong to the posting organization.", code="vendor_cross_org")
        assert_vendor_usable_for_new_transaction(vendor=vendor)
    _validate_schedule(frequency=frequency, start_date=start_date, end_date=end_date)
    if amount <= 0:
        raise ApplicationError("Expense amount must be positive.", code="expense_amount_invalid")

    if currency is None:
        currency = vendor.currency if vendor is not None else organization.default_currency
    assert_base_currency(organization=organization, currency=currency, exchange_rate=exchange_rate)

    tax_treatment = resolve_document_tax(
        organization=organization, party=vendor, place_of_supply=place_of_supply
    )

    template = RecurringExpenseTemplate.objects.create(
        organization=organization,
        vendor=vendor,
        **tax_treatment,
        frequency=frequency,
        start_date=start_date,
        end_date=end_date,
        next_run_at=start_date,
        is_active=is_active,
        expense_account=expense_account,
        paid_through_account=paid_through_account,
        tax_recoverable_account=tax_recoverable_account,
        currency=currency,
        exchange_rate=exchange_rate,
        amount=amount,
        tax_rate=tax_rate,
        description=description,
        reference=reference,
        notes=notes,
        created_by=actor,
    )
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="purchases.RecurringExpenseTemplate",
        object_id=template.id,
        changes={"frequency": frequency, "start_date": str(start_date), "amount": str(amount)},
    )
    return template


@transaction.atomic
def update_recurring_expense_template(
    *, template: RecurringExpenseTemplate, actor=None, **fields
) -> RecurringExpenseTemplate:
    changes = {}
    assert_base_currency(
        organization=template.organization, currency=fields.get("currency"), exchange_rate=fields.get("exchange_rate")
    )
    for field, value in fields.items():
        if getattr(template, field) == value:
            continue
        changes[field] = str(value.pk) if hasattr(value, "pk") else value
        setattr(template, field, value)
    if not changes:
        return template
    template.save(update_fields=[*changes.keys(), "updated_at"])
    record_audit(
        organization_id=template.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="purchases.RecurringExpenseTemplate",
        object_id=template.id,
        changes=changes,
    )
    return template


def activate_expense_template(*, template: RecurringExpenseTemplate, actor=None) -> RecurringExpenseTemplate:
    return update_recurring_expense_template(template=template, is_active=True, actor=actor)


def deactivate_expense_template(*, template: RecurringExpenseTemplate, actor=None) -> RecurringExpenseTemplate:
    return update_recurring_expense_template(template=template, is_active=False, actor=actor)


@transaction.atomic
def _generate_one_expense(*, template_id, organization, actor=None) -> Expense | None:
    template = RecurringExpenseTemplate.objects.select_for_update().filter(pk=template_id).first()
    if template is None or not template.is_active:
        return None

    occurrence_date = template.next_run_at
    if template.end_date and occurrence_date > template.end_date:
        return None

    # Each occurrence inherits the template's treatment rather than
    # re-determining it per run: a template generating monthly for two years
    # must not silently change the tax it charges because the vendor's master
    # data was edited in month seven.
    carried = carry_forward_tax(template)

    expense = create_expense(
        organization=organization,
        tax_treatment={k: v for k, v in carried.items() if k != "is_reverse_charge"},
        expense_date=occurrence_date,
        amount=template.amount,
        expense_account=template.expense_account,
        paid_through_account=template.paid_through_account,
        currency=template.currency,
        exchange_rate=template.exchange_rate,
        vendor=template.vendor,
        tax_rate=template.tax_rate,
        tax_recoverable_account=template.tax_recoverable_account,
        reference=template.reference,
        description=template.description,
        notes=template.notes,
        actor=actor,
    )

    try:
        RecurringExpenseRun.objects.create(
            organization=organization, template=template, occurrence_date=occurrence_date, expense=expense
        )
    except IntegrityError:
        raise ApplicationError(
            "This occurrence has already been generated.", code="recurring_occurrence_duplicate"
        )

    template.next_run_at = advance_occurrence(occurrence_date, template.frequency)
    template.save(update_fields=["next_run_at", "updated_at"])

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="purchases.Expense",
        object_id=expense.id,
        changes={
            "source": "recurring_expense_template", "template_id": str(template.id),
            "occurrence_date": str(occurrence_date),
        },
    )
    return expense


# ----------------------------------------------------------- the sweeps


def _sweep(*, model, generate_one, as_of, actor):
    """Shared catch-up loop for both template kinds: for every organization,
    every active template that is due generates one document per occurrence
    it is behind on, stopping at `as_of` or the template's end_date."""
    from accounts.models import Organization

    generated = []
    for organization in Organization.objects.filter(is_active=True):
        with tenant_context(organization_id=organization.id):
            template_ids = list(
                model.objects.filter(is_active=True, next_run_at__lte=as_of).values_list("id", flat=True)
            )
            for template_id in template_ids:
                while True:
                    template = model.objects.filter(pk=template_id).first()
                    if template is None or not template.is_active or template.next_run_at > as_of:
                        break
                    if template.end_date and template.next_run_at > template.end_date:
                        break
                    document = generate_one(template_id=template_id, organization=organization, actor=actor)
                    if document is None:
                        break
                    generated.append(document)
    return generated


def generate_due_bills(*, as_of=None, actor=None) -> list[Bill]:
    """Celery entry point. Generates DRAFT bills only — never posts."""
    return _sweep(
        model=RecurringBillTemplate,
        generate_one=_generate_one_bill,
        as_of=as_of or timezone.now().date(),
        actor=actor,
    )


def generate_due_expenses(*, as_of=None, actor=None) -> list[Expense]:
    """Celery entry point. Generates DRAFT expenses only — never posts."""
    return _sweep(
        model=RecurringExpenseTemplate,
        generate_one=_generate_one_expense,
        as_of=as_of or timezone.now().date(),
        actor=actor,
    )
