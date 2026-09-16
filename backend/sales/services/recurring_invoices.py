import datetime

from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from core.recurrence import advance_occurrence
from core.tenancy import tenant_context
from sales.models.customer import Customer
from sales.models.invoice import Invoice
from sales.models.recurring_invoice import (
    RecurringFrequency,
    RecurringInvoiceRun,
    RecurringInvoiceTemplate,
    RecurringInvoiceTemplateLine,
)
from sales.services.customers import assert_customer_usable_for_new_transaction
from sales.services.invoices import create_invoice
from sales.services.line_items import validate_item_for_line
from tax.services.documents import carry_forward_tax, resolve_document_tax


def _validate_line(*, organization, line: dict) -> dict:
    item = line["item"]
    validate_item_for_line(organization=organization, item=item)
    quantity = line["quantity"]
    if quantity <= 0:
        raise ApplicationError("Line quantity must be positive.", code="line_quantity_invalid")
    return {
        "item": item,
        "description": line.get("description", ""),
        "quantity": quantity,
        "unit_price": line["unit_price"],
        "discount_percent": line.get("discount_percent", 0),
        "tax_rate": line.get("tax_rate", 0),
        "cess_rate": line.get("cess_rate", 0),
    }


@transaction.atomic
def create_recurring_template(
    *,
    organization,
    customer: Customer,
    frequency: str,
    start_date,
    lines: list[dict],
    receivable_account,
    end_date=None,
    due_days: int = 0,
    tax_payable_account=None,
    warehouse=None,
    currency=None,
    exchange_rate=1,
    reference: str = "",
    notes: str = "",
    terms: str = "",
    is_active: bool = True,
    place_of_supply=None,
    actor=None,
) -> RecurringInvoiceTemplate:
    if customer.organization_id != organization.id:
        raise ApplicationError("Customer must belong to the posting organization.", code="customer_cross_org")
    assert_customer_usable_for_new_transaction(customer=customer)
    if frequency not in RecurringFrequency.values:
        raise ApplicationError(f"Unknown recurrence frequency '{frequency}'.", code="recurring_frequency_invalid")
    if end_date is not None and end_date < start_date:
        raise ApplicationError("end_date cannot be before start_date.", code="recurring_end_before_start")
    if not lines:
        raise ApplicationError("A recurring invoice template needs at least one line.", code="recurring_no_lines")

    currency = currency or customer.currency
    validated_lines = [_validate_line(organization=organization, line=line) for line in lines]

    tax_treatment = resolve_document_tax(
        organization=organization, party=customer, place_of_supply=place_of_supply
    )

    template = RecurringInvoiceTemplate.objects.create(
        organization=organization,
        customer=customer,
        **tax_treatment,
        frequency=frequency,
        start_date=start_date,
        end_date=end_date,
        next_run_at=start_date,
        is_active=is_active,
        due_days=due_days,
        receivable_account=receivable_account,
        tax_payable_account=tax_payable_account,
        warehouse=warehouse,
        currency=currency,
        exchange_rate=exchange_rate,
        reference=reference,
        notes=notes,
        terms=terms,
        created_by=actor,
    )
    for index, row in enumerate(validated_lines, start=1):
        RecurringInvoiceTemplateLine.objects.create(
            organization=organization, template=template, line_number=index, **row
        )

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="sales.RecurringInvoiceTemplate",
        object_id=template.id,
        changes={"frequency": frequency, "start_date": str(start_date)},
    )
    return template


@transaction.atomic
def update_recurring_template(*, template: RecurringInvoiceTemplate, lines: list[dict] | None = None, actor=None, **fields) -> RecurringInvoiceTemplate:
    changes = {}
    for field, value in fields.items():
        if getattr(template, field) == value:
            continue
        changes[field] = str(value.pk) if hasattr(value, "pk") else value
        setattr(template, field, value)
    if changes:
        template.save(update_fields=[*changes.keys(), "updated_at"])

    if lines is not None:
        if not lines:
            raise ApplicationError("A recurring invoice template needs at least one line.", code="recurring_no_lines")
        validated_lines = [_validate_line(organization=template.organization, line=line) for line in lines]
        template.lines.all().delete()
        for index, row in enumerate(validated_lines, start=1):
            RecurringInvoiceTemplateLine.objects.create(
                organization=template.organization, template=template, line_number=index, **row
            )
        changes["line_count"] = len(validated_lines)

    if changes:
        record_audit(
            organization_id=template.organization_id,
            actor=actor,
            action=AuditLog.Action.UPDATE,
            object_type="sales.RecurringInvoiceTemplate",
            object_id=template.id,
            changes=changes,
        )
    return template


def activate_template(*, template: RecurringInvoiceTemplate, actor=None) -> RecurringInvoiceTemplate:
    return update_recurring_template(template=template, is_active=True, actor=actor)


def deactivate_template(*, template: RecurringInvoiceTemplate, actor=None) -> RecurringInvoiceTemplate:
    return update_recurring_template(template=template, is_active=False, actor=actor)


@transaction.atomic
def _generate_one_occurrence(*, template_id, organization, actor=None) -> Invoice | None:
    """Generates at most one DRAFT invoice for a template's current
    next_run_at. Locks the template row so concurrent task execution can't
    generate the same occurrence twice; the unique constraint on
    RecurringInvoiceRun is the authoritative backstop if it somehow does."""
    template = RecurringInvoiceTemplate.objects.select_for_update().filter(pk=template_id).first()
    if template is None or not template.is_active:
        return None

    occurrence_date = template.next_run_at
    if template.end_date and occurrence_date > template.end_date:
        return None

    lines = [
        {
            "item": line.item, "description": line.description, "quantity": line.quantity,
            "unit_price": line.unit_price, "discount_percent": line.discount_percent,
            "tax_rate": line.tax_rate, "cess_rate": line.cess_rate,
        }
        for line in template.lines.all()
    ]

    # Each occurrence inherits the template's treatment rather than
    # re-determining it per run. A template generating monthly for two years
    # must not silently change the tax it charges because the customer's
    # master data was edited in month seven.
    carried = carry_forward_tax(template)

    invoice = create_invoice(
        organization=organization,
        customer=template.customer,
        tax_treatment={k: v for k, v in carried.items() if k != "is_reverse_charge"},
        is_reverse_charge=carried["is_reverse_charge"],
        invoice_date=occurrence_date,
        due_date=occurrence_date + datetime.timedelta(days=template.due_days),
        receivable_account=template.receivable_account,
        tax_payable_account=template.tax_payable_account,
        warehouse=template.warehouse,
        currency=template.currency,
        exchange_rate=template.exchange_rate,
        reference=template.reference,
        notes=template.notes,
        terms=template.terms,
        lines=lines,
        actor=actor,
    )

    try:
        RecurringInvoiceRun.objects.create(
            organization=organization, template=template, occurrence_date=occurrence_date, invoice=invoice,
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
        object_type="sales.Invoice",
        object_id=invoice.id,
        changes={"source": "recurring_invoice_template", "template_id": str(template.id), "occurrence_date": str(occurrence_date)},
    )
    return invoice


def generate_due_invoices(*, as_of=None, actor=None) -> list[Invoice]:
    """The single entry point a Celery task calls (services/../tasks.py) —
    never invoked inline from a web request (root CLAUDE.md). Not itself
    tenant-scoped (a scheduled task spans every organization).

    RLS is enforced at the Postgres level independent of which Django
    manager is used (core/CLAUDE.md: it's the fail-closed backstop, not
    bypassable via all_objects) — so there is no way to query
    RecurringInvoiceTemplate across every tenant in one shot. Instead this
    enumerates accounts.Organization first (deliberately NOT RLS-protected,
    same reason a user must list their orgs before picking one — see
    accounts/CLAUDE.md) and opens tenant_context() per organization, same
    self-scoping pattern as accounts.services.allocate_sequence_number.
    For each active, due template, generates one DRAFT invoice per
    occurrence it is behind on (catch-up), stopping at `as_of`/end_date —
    never posts accounting or stock; a human reviews and calls post_invoice.
    """
    from accounts.models import Organization

    as_of = as_of or timezone.now().date()
    generated: list[Invoice] = []

    for organization in Organization.objects.filter(is_active=True):
        with tenant_context(organization_id=organization.id):
            template_ids = list(
                RecurringInvoiceTemplate.objects.filter(is_active=True, next_run_at__lte=as_of)
                .values_list("id", flat=True)
            )
            for template_id in template_ids:
                while True:
                    template = RecurringInvoiceTemplate.objects.filter(pk=template_id).first()
                    if template is None or not template.is_active or template.next_run_at > as_of:
                        break
                    if template.end_date and template.next_run_at > template.end_date:
                        break
                    invoice = _generate_one_occurrence(template_id=template_id, organization=organization, actor=actor)
                    if invoice is None:
                        break
                    generated.append(invoice)
    return generated
