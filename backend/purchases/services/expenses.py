"""Direct expenses — costs with no item and no stock.

See models/expense.py for why this is not a one-line Bill. The accounting is
deliberately simpler than a bill's: no inventory, no price variance, no
goods receipt, and the credit side may be either a bank/cash ASSET account
(already paid) or an AP LIABILITY account (to be settled later).
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.utils import timezone

from accounting.models.account import AccountType
from accounting.services.currency import assert_base_currency
from accounting.services.posting import reverse_journal
from accounts.services import allocate_sequence_number
from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from purchases.accounting_bridge import post_purchase_journal
from purchases.models.expense import Expense, ExpenseStatus
from purchases.services.vendors import assert_vendor_usable_for_new_transaction
from tax.enums import SupplyNature
from tax.services.computation import split_tax
from tax.services.documents import resolve_document_tax
from tax.services.posting import build_input_tax_lines

EXPENSE_NUMBER_SEQUENCE_KEY = "expense"

MONEY_QUANTUM = Decimal("0.01")

# The credit side of an expense is either money already gone (bank/cash) or
# money still owed (AP). Both are legitimate; anything else is not, and
# saying so explicitly here keeps the journal's shape predictable.
_ALLOWED_PAID_THROUGH_TYPES = {AccountType.ASSET, AccountType.LIABILITY}


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)


def _validate_account(*, organization, account, expected_type, field_name):
    if account.organization_id != organization.id:
        raise ApplicationError(f"{field_name} must belong to the posting organization.", code="cross_org_reference")
    if account.account_type != expected_type:
        raise ApplicationError(
            f"{field_name} must reference an account of type '{expected_type}'.", code="invalid_account_type"
        )


def _compute_totals(
    *, amount: Decimal, tax_rate: Decimal, cess_rate: Decimal = Decimal("0"),
    supply_nature: str = SupplyNature.UNSPECIFIED,
) -> dict:
    """An Expense is its own line - there is no ExpenseLine - so the component
    split lands on the header here rather than through
    `tax.services.computation.apply_tax_components`. The arithmetic is the
    same one, via the same `split_tax`, so the two cannot diverge."""
    if amount <= 0:
        raise ApplicationError("Expense amount must be positive.", code="expense_amount_invalid")
    if tax_rate < 0:
        raise ApplicationError("Expense tax rate cannot be negative.", code="expense_tax_rate_invalid")
    amount = _round_money(amount)
    split = split_tax(
        taxable_amount=amount, tax_rate=tax_rate, cess_rate=cess_rate, supply_nature=supply_nature
    )
    return {
        "amount": amount,
        "tax_amount": split["tax_amount"],
        "total": amount + split["tax_amount"],
        "cgst_amount": split["cgst"],
        "sgst_amount": split["sgst"],
        "igst_amount": split["igst"],
        "cess_amount": split["cess"],
    }


def _get_expense_for_update(*, expense_id, organization) -> Expense:
    try:
        return Expense.objects.select_for_update().get(id=expense_id, organization=organization)
    except Expense.DoesNotExist:
        raise ApplicationError("Expense not found.", code="expense_not_found", status_code=404)


@transaction.atomic
def create_expense(
    *,
    organization,
    expense_date,
    amount: Decimal,
    expense_account,
    paid_through_account,
    currency=None,
    exchange_rate: Decimal = Decimal("1"),
    vendor=None,
    tax_rate: Decimal = Decimal("0"),
    tax_recoverable_account=None,
    reference: str = "",
    description: str = "",
    is_billable: bool = False,
    customer=None,
    project=None,
    notes: str = "",
    cess_rate: Decimal = Decimal("0"),
    place_of_supply=None,
    tax_treatment: dict | None = None,
    actor=None,
) -> Expense:
    if vendor is not None:
        if vendor.organization_id != organization.id:
            raise ApplicationError("Vendor must belong to the posting organization.", code="vendor_cross_org")
        assert_vendor_usable_for_new_transaction(vendor=vendor)
    _validate_account(
        organization=organization, account=expense_account, expected_type=AccountType.EXPENSE,
        field_name="expense_account",
    )
    if paid_through_account.organization_id != organization.id:
        raise ApplicationError(
            "paid_through_account must belong to the posting organization.", code="cross_org_reference"
        )
    if paid_through_account.account_type not in _ALLOWED_PAID_THROUGH_TYPES:
        raise ApplicationError(
            "paid_through_account must be an asset (paid now) or liability (payable later) account.",
            code="invalid_account_type",
        )
    if tax_recoverable_account is not None:
        _validate_account(
            organization=organization, account=tax_recoverable_account, expected_type=AccountType.ASSET,
            field_name="tax_recoverable_account",
        )
    if is_billable and customer is None:
        raise ApplicationError(
            "A billable expense must name the customer it is billable to.", code="billable_customer_required"
        )
    if customer is not None and customer.organization_id != organization.id:
        raise ApplicationError("Customer must belong to the posting organization.", code="customer_cross_org")
    if project is not None and project.organization_id != organization.id:
        raise ApplicationError("Project must belong to the posting organization.", code="project_cross_org")

    if currency is None:
        currency = vendor.currency if vendor is not None else organization.default_currency
    assert_base_currency(organization=organization, currency=currency, exchange_rate=exchange_rate)
    tax_treatment = tax_treatment or resolve_document_tax(
        organization=organization, party=vendor, place_of_supply=place_of_supply
    )
    totals = _compute_totals(
        amount=amount, tax_rate=tax_rate, cess_rate=cess_rate,
        supply_nature=tax_treatment["supply_nature"],
    )

    expense = Expense.objects.create(
        organization=organization,
        vendor=vendor,
        expense_date=expense_date,
        reference=reference,
        description=description,
        currency=currency,
        exchange_rate=exchange_rate,
        expense_account=expense_account,
        paid_through_account=paid_through_account,
        tax_recoverable_account=tax_recoverable_account,
        amount=totals["amount"],
        tax_rate=tax_rate,
        cess_rate=cess_rate,
        tax_amount=totals["tax_amount"],
        total=totals["total"],
        cgst_amount=totals["cgst_amount"],
        sgst_amount=totals["sgst_amount"],
        igst_amount=totals["igst_amount"],
        cess_amount=totals["cess_amount"],
        **tax_treatment,
        is_billable=is_billable,
        customer=customer,
        project=project,
        notes=notes,
        created_by=actor,
    )
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="purchases.Expense",
        object_id=expense.id,
        changes={"total": str(totals["total"])},
    )
    return expense


@transaction.atomic
def update_expense(*, expense: Expense, actor=None, **fields) -> Expense:
    """DRAFT only. Recomputes tax/total whenever amount or tax_rate changes,
    so a client can never set `total` directly (root CLAUDE.md: server
    calculations are authoritative)."""
    if expense.status != ExpenseStatus.DRAFT:
        raise ApplicationError("Only draft expenses can be modified.", code="expense_not_draft")

    fields.pop("tax_amount", None)
    fields.pop("total", None)
    assert_base_currency(
        organization=expense.organization, currency=fields.get("currency"), exchange_rate=fields.get("exchange_rate")
    )

    changes = {}
    for field, value in fields.items():
        if getattr(expense, field) == value:
            continue
        changes[field] = str(value.pk) if hasattr(value, "pk") else value
        setattr(expense, field, value)

    if {"amount", "tax_rate", "cess_rate"} & set(fields):
        totals = _compute_totals(
            amount=expense.amount, tax_rate=expense.tax_rate, cess_rate=expense.cess_rate,
            supply_nature=expense.supply_nature,
        )
        expense.amount = totals["amount"]
        expense.tax_amount = totals["tax_amount"]
        expense.total = totals["total"]
        expense.cgst_amount = totals["cgst_amount"]
        expense.sgst_amount = totals["sgst_amount"]
        expense.igst_amount = totals["igst_amount"]
        expense.cess_amount = totals["cess_amount"]
        changes["total"] = str(totals["total"])

    if expense.is_billable and expense.customer_id is None:
        raise ApplicationError(
            "A billable expense must name the customer it is billable to.", code="billable_customer_required"
        )
    if not changes:
        return expense

    expense.save(
        update_fields=[
            *{
                *changes.keys(), "amount", "tax_amount", "total",
                "cgst_amount", "sgst_amount", "igst_amount", "cess_amount",
            },
            "updated_at",
        ]
    )
    record_audit(
        organization_id=expense.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="purchases.Expense",
        object_id=expense.id,
        changes=changes,
    )
    return expense


@transaction.atomic
def post_expense(*, expense_id, organization, actor=None) -> Expense:
    """DRAFT -> POSTED. Idempotent by construction like post_bill/post_journal:
    posting an already-POSTED expense is a locked no-op.

    Journal: Dr expense_account (net), Dr tax_recoverable_account (tax),
    Cr paid_through_account (total).
    """
    expense = _get_expense_for_update(expense_id=expense_id, organization=organization)

    if expense.status == ExpenseStatus.POSTED:
        return expense
    if expense.status != ExpenseStatus.DRAFT:
        raise ApplicationError(f"Cannot post an expense in status '{expense.status}'.", code="expense_invalid_status")
    # The tax-account requirement is enforced by build_input_tax_lines below.

    journal_lines = [{"account_id": expense.expense_account_id, "debit": expense.amount}]
    journal_lines.extend(
        build_input_tax_lines(
            organization=organization,
            document=expense,
            fallback_account_id=expense.tax_recoverable_account_id,
            # An Expense calls its tax total `tax_amount`, not `tax_total`.
            tax_total=expense.tax_amount,
        )
    )
    journal_lines.append({"account_id": expense.paid_through_account_id, "credit": expense.total})

    expense_number = allocate_sequence_number(
        organization_id=organization.id, key=EXPENSE_NUMBER_SEQUENCE_KEY, prefix="EXP-"
    )

    journal = post_purchase_journal(
        organization=organization,
        posting_date=expense.expense_date,
        currency=expense.currency,
        lines=journal_lines,
        memo=f"Expense {expense_number}",
        source_type="purchases.Expense",
        source_id=str(expense.id),
        actor=actor,
    )

    expense.expense_number = expense_number
    expense.status = ExpenseStatus.POSTED
    expense.posted_by = actor
    expense.posted_at = timezone.now()
    expense.accounting_journal = journal
    expense.save(
        update_fields=["expense_number", "status", "posted_by", "posted_at", "accounting_journal", "updated_at"]
    )

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.POST,
        object_type="purchases.Expense",
        object_id=expense.id,
        changes={"expense_number": expense_number, "total": str(expense.total), "journal_id": str(journal.id)},
    )
    return expense


@transaction.atomic
def void_expense(*, expense_id, organization, actor=None, reason: str = "") -> Expense:
    """Reverses a POSTED expense's journal. Idempotent — voiding an already
    VOID expense is a no-op. No stock to unwind: an expense never touches
    inventory."""
    expense = _get_expense_for_update(expense_id=expense_id, organization=organization)

    if expense.status == ExpenseStatus.VOID:
        return expense
    if expense.status != ExpenseStatus.POSTED:
        raise ApplicationError(f"Cannot void an expense in status '{expense.status}'.", code="expense_invalid_status")

    if expense.accounting_journal_id:
        reverse_journal(
            journal_id=expense.accounting_journal_id,
            organization=organization,
            actor=actor,
            posting_date=timezone.now().date(),
            memo=f"Void of expense {expense.expense_number}",
        )

    expense.status = ExpenseStatus.VOID
    expense.voided_by = actor
    expense.voided_at = timezone.now()
    expense.void_reason = reason
    expense.save(update_fields=["status", "voided_by", "voided_at", "void_reason", "updated_at"])

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.REVERSE,
        object_type="purchases.Expense",
        object_id=expense.id,
        changes={"reason": reason},
    )
    return expense
