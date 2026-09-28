from decimal import Decimal

from django.db import transaction

from accounting.services.currency import assert_base_currency
from accounts.services import allocate_sequence_number
from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from core.money import calculate_document_totals
from sales.models.customer import Customer
from sales.models.quote import Quote, QuoteLine, QuoteStatus
from sales.services.customers import assert_customer_usable_for_new_transaction
from sales.services.line_items import build_line_snapshot
from tax.services.documents import resolve_document_tax

QUOTE_NUMBER_SEQUENCE_KEY = "quote"

# Quotes may transition only along these edges — anything else is rejected
# (sales/CLAUDE.md: state transitions only through explicit services).
_ALLOWED_TRANSITIONS = {
    QuoteStatus.DRAFT: {QuoteStatus.SENT, QuoteStatus.CANCELLED},
    QuoteStatus.SENT: {QuoteStatus.ACCEPTED, QuoteStatus.REJECTED, QuoteStatus.EXPIRED, QuoteStatus.CANCELLED},
}


def _get_quote_for_update(*, quote_id, organization) -> Quote:
    try:
        return Quote.objects.select_for_update().get(id=quote_id, organization=organization)
    except Quote.DoesNotExist:
        raise ApplicationError("Quote not found.", code="quote_not_found", status_code=404)


@transaction.atomic
def create_quote(
    *,
    organization,
    customer: Customer,
    issue_date,
    lines: list[dict],
    expiry_date=None,
    currency=None,
    exchange_rate: Decimal = Decimal("1"),
    notes: str = "",
    terms: str = "",
    place_of_supply=None,
    actor=None,
) -> Quote:
    if customer.organization_id != organization.id:
        raise ApplicationError("Customer must belong to the posting organization.", code="customer_cross_org")
    assert_customer_usable_for_new_transaction(customer=customer)
    if not lines:
        raise ApplicationError("A quote needs at least one line.", code="quote_no_lines")

    currency = currency or customer.currency
    assert_base_currency(organization=organization, currency=currency, exchange_rate=exchange_rate)
    # Determined here even though a quote carries no component columns and
    # posts nothing: the treatment is what converts forward to the order and
    # invoice, so deciding it once at the start is what stops it drifting.
    tax_treatment = resolve_document_tax(
        organization=organization, party=customer, place_of_supply=place_of_supply
    )

    quote_number = allocate_sequence_number(
        organization_id=organization.id, key=QUOTE_NUMBER_SEQUENCE_KEY, prefix="QUO-"
    )

    line_rows = [
        build_line_snapshot(organization=organization, line=line, line_number=index)
        for index, line in enumerate(lines, start=1)
    ]
    totals = calculate_document_totals(line_rows)

    quote = Quote.objects.create(
        organization=organization,
        customer=customer,
        quote_number=quote_number,
        issue_date=issue_date,
        expiry_date=expiry_date,
        currency=currency,
        exchange_rate=exchange_rate,
        subtotal=totals["subtotal"],
        discount_total=totals["discount"],
        tax_total=totals["tax"],
        total=totals["total"],
        notes=notes,
        terms=terms,
        **tax_treatment,
    )
    for row in line_rows:
        QuoteLine.objects.create(organization=organization, quote=quote, **row)

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="sales.Quote",
        object_id=quote.id,
        changes={"quote_number": quote_number, "total": str(totals["total"])},
    )
    return quote


@transaction.atomic
def replace_quote_lines(*, quote: Quote, lines: list[dict], actor=None) -> Quote:
    """Replaces all lines on a DRAFT quote and recomputes header totals.
    Raises if the quote is not a draft — see Quote._MUTABLE_AFTER_DRAFT_FIELDS."""
    if quote.status != QuoteStatus.DRAFT:
        raise ApplicationError("Only draft quotes can be modified.", code="quote_not_draft")
    if not lines:
        raise ApplicationError("A quote needs at least one line.", code="quote_no_lines")

    line_rows = [
        build_line_snapshot(organization=quote.organization, line=line, line_number=index)
        for index, line in enumerate(lines, start=1)
    ]
    totals = calculate_document_totals(line_rows)

    quote.lines.all().delete()
    for row in line_rows:
        QuoteLine.objects.create(organization=quote.organization, quote=quote, **row)

    quote.subtotal = totals["subtotal"]
    quote.discount_total = totals["discount"]
    quote.tax_total = totals["tax"]
    quote.total = totals["total"]
    quote.save(update_fields=["subtotal", "discount_total", "tax_total", "total", "updated_at"])

    record_audit(
        organization_id=quote.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="sales.Quote",
        object_id=quote.id,
        changes={"total": str(totals["total"])},
    )
    return quote


def _apply_transition(*, quote: Quote, to_status: str, organization, actor=None) -> Quote:
    allowed = _ALLOWED_TRANSITIONS.get(quote.status, set())
    if to_status not in allowed:
        raise ApplicationError(
            f"Cannot move quote from '{quote.status}' to '{to_status}'.", code="quote_invalid_status"
        )
    from_status = quote.status
    quote.status = to_status
    quote.save(update_fields=["status", "updated_at"])
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="sales.Quote",
        object_id=quote.id,
        changes={"status": {"from": from_status, "to": to_status}},
    )
    return quote


@transaction.atomic
def send_quote(*, quote_id, organization, actor=None) -> Quote:
    quote = _get_quote_for_update(quote_id=quote_id, organization=organization)
    if not quote.lines.exists():
        raise ApplicationError("Cannot send a quote with no lines.", code="quote_no_lines")
    return _apply_transition(quote=quote, to_status=QuoteStatus.SENT, organization=organization, actor=actor)


@transaction.atomic
def accept_quote(*, quote_id, organization, actor=None) -> Quote:
    quote = _get_quote_for_update(quote_id=quote_id, organization=organization)
    return _apply_transition(quote=quote, to_status=QuoteStatus.ACCEPTED, organization=organization, actor=actor)


@transaction.atomic
def reject_quote(*, quote_id, organization, actor=None) -> Quote:
    quote = _get_quote_for_update(quote_id=quote_id, organization=organization)
    return _apply_transition(quote=quote, to_status=QuoteStatus.REJECTED, organization=organization, actor=actor)


@transaction.atomic
def cancel_quote(*, quote_id, organization, actor=None) -> Quote:
    quote = _get_quote_for_update(quote_id=quote_id, organization=organization)
    return _apply_transition(quote=quote, to_status=QuoteStatus.CANCELLED, organization=organization, actor=actor)
