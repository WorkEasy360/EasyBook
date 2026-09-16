"""The reconciliation matcher.

WHAT MATCHING IS, AND THE ONE THING IT ALMOST NEVER DOES.

Matching a statement line to a `CustomerPayment` asserts that the money the
bank saw is the money we already booked. The payment posted its own journal
when it was recorded; posting a second one here would double the revenue.
So — the invariant this module exists to protect — **matching posts no
accounting**. Only `categorize_transaction` does, and only because it is the
case where no document exists and the user is telling us what the line was.

That is the direct analogue of `purchases.GoodsReceipt` moving stock without
posting, and of `projects` never posting at all: a module posts a journal only
where it is the first place the financial fact becomes known.

SUGGESTION IS NOT CONFIRMATION.
`suggest_matches` writes unconfirmed rows and nothing else. Confirmation is a
separate act with a separate permission, and auto-confirmation happens only
when the evidence is both strong and UNAMBIGUOUS — one candidate at high
confidence, not the best of several. Two identical 25,000 payments from two
customers on one day is not a hard problem to get wrong; it is the normal
case, and the only safe answer is to ask.

AI.
Root CLAUDE.md: AI may suggest; deterministic logic performs the authoritative
act. `record_ai_suggestion` is the only way an AI-sourced match can be
created, it is refused auto-confirmation unconditionally, and confirming one
requires a named human actor. Everything scored in this module is
deterministic arithmetic over amounts, dates and strings — run it twice on the
same data and it returns the same numbers.
"""

import datetime
from decimal import Decimal

from django.db import transaction as db_transaction
from django.db.models import Q
from django.utils import timezone

from accounting.models.journal import JournalEntry, JournalLine, JournalStatus
from audit.models import AuditLog
from audit.services import record as record_audit
from banking.accounting_bridge import post_banking_journal
from banking.models.match import (
    COUNTERPART_FIELDS,
    BankTransactionMatch,
    MatchType,
    SuggestionSource,
)
from banking.models.reconciliation import ReconciliationStatus
from banking.models.statement import BankTransaction, BankTransactionStatus
from banking.models.transfer import BankTransfer, BankTransferStatus
from banking.selectors import get_counterpart_matched_amount, get_matched_amount
from core.exceptions import ApplicationError
from purchases.models.expense import Expense, ExpenseStatus
from purchases.models.payment import VendorPayment
from sales.models.payment import CustomerPayment

# How far either side of the statement date a candidate may sit. Beyond a
# month, an amount coincidence is far likelier than a real settlement delay,
# and offering the coincidence trains users to click through suggestions.
MATCH_WINDOW_DAYS = 30

# A candidate must reach this to be auto-confirmable, AND must be the only
# candidate that reaches it.
#
# 0.8 is what "exact amount, same day" scores. That is deliberately the bar,
# because the threshold is NOT what makes auto-confirmation safe —
# uniqueness is. A score alone cannot distinguish the right payment from an
# identical wrong one, so no threshold, however high, would protect against
# the dangerous case; the "exactly one candidate" rule in `auto_match` is
# what does. What the threshold rules out is WEAK evidence: a document three
# weeks away whose only agreement is the amount scores 0.5 and stays for a
# human. Setting this at 0.9 instead would demand the document's own
# reference appear in the narration, which real bank narrations rarely carry
# — leaving auto-matching that never fires, which in practice means every
# line gets clicked through unread.
AUTO_CONFIRM_THRESHOLD = Decimal("0.800")

_SCORE_QUANTUM = Decimal("0.001")

# Journals raised by a document are reachable through that document, which
# carries far more context. Offering the journal as well would present the
# same economic event twice and let a user match both.
_DOCUMENT_SOURCE_PREFIXES = ("sales.", "purchases.", "inventory.", "projects.", "banking.")


# --------------------------------------------------------------- guards


def _get_transaction_for_update(*, transaction_id, organization) -> BankTransaction:
    locked = (
        BankTransaction.objects.select_for_update()
        .filter(pk=transaction_id, organization=organization)
        .first()
    )
    if locked is None:
        raise ApplicationError("Bank transaction not found.", code="bank_transaction_not_found", status_code=404)
    return locked


def _assert_open_for_changes(transaction: BankTransaction) -> None:
    """A line inside a COMPLETED reconciliation is signed off. Re-matching it
    would silently change a balance someone has already certified, so the
    correction is to reopen the reconciliation — an act with its own audit
    record — not to edit underneath it."""
    if transaction.reconciliation_id is None:
        return
    if transaction.reconciliation.status == ReconciliationStatus.COMPLETED:
        raise ApplicationError(
            "This transaction belongs to a completed reconciliation. Reopen it first.",
            code="transaction_reconciled",
        )


# ---------------------------------------------------------- counterparts


_COUNTERPART_MODELS = {
    "customer_payment": CustomerPayment,
    "vendor_payment": VendorPayment,
    "expense": Expense,
    "bank_transfer": BankTransfer,
    "journal_entry": JournalEntry,
}


def _lock_counterpart(*, counterpart_field: str, counterpart, organization):
    """Lock the DOCUMENT before asking how much of it is already explained.

    Locking the statement line is not enough, and the gap is not theoretical:
    five different deposits confirmed at once each lock a DIFFERENT line, so
    nothing serialises them, and all five read "this payment explains nothing
    yet" before any of them writes. One 1,000 receipt then explained five
    separate 1,000 deposits — the identical read-then-write race that let one
    goods receipt be billed five times in Phase 4, with the contended row on
    the other side of the relationship. Covered by
    `tests/test_concurrency.py::ConcurrentMatchConfirmationTests`.
    """
    model = _COUNTERPART_MODELS[counterpart_field]
    locked = (
        model.objects.select_for_update()
        .filter(pk=counterpart.pk, organization=organization)
        .first()
    )
    if locked is None:
        raise ApplicationError(
            "The matched document no longer exists.", code="match_counterpart_not_found", status_code=404
        )
    return locked


def _counterpart_capacity(*, counterpart_field: str, counterpart, transaction) -> Decimal:
    """The largest amount this document can legitimately explain."""
    if counterpart_field == "customer_payment":
        return counterpart.amount
    if counterpart_field == "vendor_payment":
        return counterpart.amount
    if counterpart_field == "expense":
        return counterpart.total
    if counterpart_field == "bank_transfer":
        return counterpart.amount
    if counterpart_field == "journal_entry":
        # A journal can touch the bank account on several lines; what it can
        # explain is its NET effect there, signed the same way the statement
        # line is.
        totals = JournalLine.objects.filter(
            journal_entry=counterpart, account_id=transaction.bank_account.account_id
        )
        net = sum((line.base_debit - line.base_credit for line in totals), Decimal("0"))
        return abs(net)
    raise ValueError(f"Unknown counterpart field '{counterpart_field}'.")


def _validate_counterpart(*, counterpart_field: str, counterpart, transaction, organization) -> None:
    if counterpart_field not in COUNTERPART_FIELDS:
        raise ApplicationError(f"Unknown counterpart '{counterpart_field}'.", code="match_counterpart_invalid")
    if counterpart.organization_id != organization.id:
        raise ApplicationError(
            "The matched document belongs to another organization.", code="cross_org_reference"
        )

    bank_gl_account_id = transaction.bank_account.account_id
    # The counterpart must actually touch this bank account. Without this a
    # user could explain a HDFC line with an ICICI payment, and both accounts
    # would then reconcile to the wrong answer.
    if counterpart_field == "customer_payment" and counterpart.destination_account_id != bank_gl_account_id:
        raise ApplicationError(
            "That payment was received into a different account.", code="match_account_mismatch"
        )
    if counterpart_field == "vendor_payment" and counterpart.source_account_id != bank_gl_account_id:
        raise ApplicationError(
            "That payment was made from a different account.", code="match_account_mismatch"
        )
    if counterpart_field == "expense" and counterpart.paid_through_account_id != bank_gl_account_id:
        raise ApplicationError(
            "That expense was paid through a different account.", code="match_account_mismatch"
        )
    if counterpart_field == "bank_transfer":
        if transaction.bank_account_id not in (counterpart.from_bank_account_id, counterpart.to_bank_account_id):
            raise ApplicationError(
                "That transfer does not involve this bank account.", code="match_account_mismatch"
            )
        if counterpart.status != BankTransferStatus.POSTED:
            raise ApplicationError("That transfer has been voided.", code="match_counterpart_void")
    if counterpart_field == "journal_entry":
        if counterpart.status != JournalStatus.POSTED:
            raise ApplicationError("That journal is not posted.", code="match_counterpart_not_posted")
        if not JournalLine.objects.filter(
            journal_entry=counterpart, account_id=bank_gl_account_id
        ).exists():
            raise ApplicationError(
                "That journal does not touch this bank account.", code="match_account_mismatch"
            )

    # Direction. A deposit cannot be explained by money we paid out.
    inflow_fields = {"customer_payment"}
    outflow_fields = {"vendor_payment", "expense"}
    if transaction.is_inflow and counterpart_field in outflow_fields:
        raise ApplicationError(
            "Money arriving cannot be explained by a payment made.", code="match_direction_mismatch"
        )
    if not transaction.is_inflow and counterpart_field in inflow_fields:
        raise ApplicationError(
            "Money leaving cannot be explained by a payment received.", code="match_direction_mismatch"
        )
    if counterpart_field == "expense" and counterpart.status != ExpenseStatus.POSTED:
        raise ApplicationError(
            "Only a posted expense can explain a statement line.", code="match_counterpart_not_posted"
        )


# -------------------------------------------------------------- scoring


def _contains(haystack: str, needle: str) -> bool:
    return bool(needle) and needle.strip().lower() in (haystack or "").lower()


def score_candidate(*, transaction, counterpart_date, reference: str = "", party_name: str = "") -> Decimal:
    """Deterministic 0..1 confidence.

    Amount equality is a precondition, not a contributor — every candidate
    reaching this function already matches to the cent, so scoring it would
    add a constant to everything. What varies is how close the dates are and
    whether the document's own identifier shows up in the bank narration,
    which is the difference between "plausible" and "certain".
    """
    haystack = f"{transaction.description} {transaction.bank_reference} {transaction.counterparty_name}"
    score = Decimal("0.5")

    delta = abs((transaction.transaction_date - counterpart_date).days)
    if delta == 0:
        score += Decimal("0.3")
    elif delta <= 3:
        score += Decimal("0.2")
    elif delta <= 7:
        score += Decimal("0.1")

    if _contains(haystack, reference):
        score += Decimal("0.2")
    if _contains(haystack, party_name):
        score += Decimal("0.1")

    return min(score, Decimal("1")).quantize(_SCORE_QUANTUM)


def _match_type(*, transaction, counterpart_date, reference: str) -> str:
    haystack = f"{transaction.description} {transaction.bank_reference}"
    same_day = transaction.transaction_date == counterpart_date
    if same_day and _contains(haystack, reference):
        return MatchType.EXACT
    return MatchType.AMOUNT_DATE


# ----------------------------------------------------------- candidates


def _already_matched_ids(counterpart_field: str) -> set:
    return set(
        BankTransactionMatch.objects.filter(is_confirmed=True)
        .exclude(**{f"{counterpart_field}__isnull": True})
        .values_list(counterpart_field, flat=True)
    )


def _date_window(transaction):
    delta = datetime.timedelta(days=MATCH_WINDOW_DAYS)
    return transaction.transaction_date - delta, transaction.transaction_date + delta


def find_candidates(*, transaction, organization) -> list[dict]:
    """Every document that could explain this line, scored. Pure read."""
    amount = abs(transaction.amount)
    earliest, latest = _date_window(transaction)
    bank_gl_account_id = transaction.bank_account.account_id
    candidates: list[dict] = []

    if transaction.is_inflow:
        matched = _already_matched_ids("customer_payment")
        for payment in CustomerPayment.objects.filter(
            organization=organization,
            destination_account_id=bank_gl_account_id,
            amount=amount,
            payment_date__gte=earliest,
            payment_date__lte=latest,
        ).exclude(pk__in=matched).select_related("customer"):
            candidates.append(
                {
                    "counterpart_field": "customer_payment",
                    "counterpart": payment,
                    "confidence": score_candidate(
                        transaction=transaction,
                        counterpart_date=payment.payment_date,
                        reference=payment.reference or payment.payment_number,
                        party_name=payment.customer.display_name,
                    ),
                    "match_type": _match_type(
                        transaction=transaction,
                        counterpart_date=payment.payment_date,
                        reference=payment.reference or payment.payment_number,
                    ),
                    "reason": f"Customer payment {payment.payment_number}",
                }
            )
    else:
        matched_payments = _already_matched_ids("vendor_payment")
        for payment in VendorPayment.objects.filter(
            organization=organization,
            source_account_id=bank_gl_account_id,
            amount=amount,
            payment_date__gte=earliest,
            payment_date__lte=latest,
        ).exclude(pk__in=matched_payments).select_related("vendor"):
            candidates.append(
                {
                    "counterpart_field": "vendor_payment",
                    "counterpart": payment,
                    "confidence": score_candidate(
                        transaction=transaction,
                        counterpart_date=payment.payment_date,
                        reference=payment.reference or payment.payment_number,
                        party_name=payment.vendor.display_name,
                    ),
                    "match_type": _match_type(
                        transaction=transaction,
                        counterpart_date=payment.payment_date,
                        reference=payment.reference or payment.payment_number,
                    ),
                    "reason": f"Vendor payment {payment.payment_number}",
                }
            )

        matched_expenses = _already_matched_ids("expense")
        for expense in Expense.objects.filter(
            organization=organization,
            paid_through_account_id=bank_gl_account_id,
            total=amount,
            status=ExpenseStatus.POSTED,
            expense_date__gte=earliest,
            expense_date__lte=latest,
        ).exclude(pk__in=matched_expenses).select_related("vendor"):
            candidates.append(
                {
                    "counterpart_field": "expense",
                    "counterpart": expense,
                    "confidence": score_candidate(
                        transaction=transaction,
                        counterpart_date=expense.expense_date,
                        reference=expense.reference or expense.expense_number,
                        party_name=expense.vendor.display_name if expense.vendor_id else "",
                    ),
                    "match_type": _match_type(
                        transaction=transaction,
                        counterpart_date=expense.expense_date,
                        reference=expense.reference or expense.expense_number,
                    ),
                    "reason": f"Expense {expense.expense_number}",
                }
            )

    # Transfers already recorded in the books. Excluded only when THIS side
    # of the transfer is already matched — the other leg is a separate
    # statement line on a separate account and still needs explaining.
    matched_transfers = set(
        BankTransactionMatch.objects.filter(
            is_confirmed=True,
            bank_transfer__isnull=False,
            transaction__bank_account_id=transaction.bank_account_id,
        ).values_list("bank_transfer_id", flat=True)
    )
    transfer_side = (
        Q(to_bank_account_id=transaction.bank_account_id)
        if transaction.is_inflow
        else Q(from_bank_account_id=transaction.bank_account_id)
    )
    for transfer in BankTransfer.objects.filter(
        transfer_side,
        organization=organization,
        amount=amount,
        status=BankTransferStatus.POSTED,
        transfer_date__gte=earliest,
        transfer_date__lte=latest,
    ).exclude(pk__in=matched_transfers):
        candidates.append(
            {
                "counterpart_field": "bank_transfer",
                "counterpart": transfer,
                "confidence": score_candidate(
                    transaction=transaction,
                    counterpart_date=transfer.transfer_date,
                    reference=transfer.reference or transfer.transfer_number,
                ),
                "match_type": MatchType.TRANSFER,
                "reason": f"Transfer {transfer.transfer_number}",
            }
        )

    # Journals posted directly to the bank account — bank charges, interest,
    # opening entries. Document-raised journals are excluded because the
    # document itself is the better candidate and offering both would let one
    # event be matched twice.
    matched_journals = _already_matched_ids("journal_entry")
    line_side = Q(base_debit=amount) if transaction.is_inflow else Q(base_credit=amount)
    journal_ids = (
        JournalLine.objects.filter(line_side, account_id=bank_gl_account_id)
        .values_list("journal_entry_id", flat=True)
    )
    raised_by_a_document = Q()
    for prefix in _DOCUMENT_SOURCE_PREFIXES:
        raised_by_a_document |= Q(source_type__startswith=prefix)
    for journal in (
        JournalEntry.objects.filter(
            organization=organization,
            pk__in=list(journal_ids),
            status=JournalStatus.POSTED,
            posting_date__gte=earliest,
            posting_date__lte=latest,
        )
        .exclude(pk__in=matched_journals)
        .exclude(raised_by_a_document)
    ):
        candidates.append(
            {
                "counterpart_field": "journal_entry",
                "counterpart": journal,
                "confidence": score_candidate(
                    transaction=transaction,
                    counterpart_date=journal.posting_date,
                    reference=journal.reference or journal.journal_number,
                ),
                "match_type": _match_type(
                    transaction=transaction,
                    counterpart_date=journal.posting_date,
                    reference=journal.reference or journal.journal_number,
                ),
                "reason": f"Journal {journal.journal_number}",
            }
        )

    candidates.sort(key=lambda c: (-c["confidence"], str(c["counterpart"].pk)))
    return candidates


def find_transfer_counterparts(*, transaction, organization) -> list[BankTransaction]:
    """The opposite leg of an unrecorded transfer: a line in ANOTHER of our
    accounts, same amount, opposite sign, within a few days.

    Deliberately narrower than MATCH_WINDOW_DAYS. Inter-account transfers
    settle in days, and a wide window over a business with several accounts
    turns every round-number payment into a transfer candidate — which is how
    a genuine supplier payment gets booked as an internal movement and
    disappears from expenses.
    """
    window = datetime.timedelta(days=5)
    return list(
        BankTransaction.objects.filter(
            organization=organization,
            amount=-transaction.amount,
            transaction_date__gte=transaction.transaction_date - window,
            transaction_date__lte=transaction.transaction_date + window,
            status__in=[BankTransactionStatus.UNMATCHED, BankTransactionStatus.SUGGESTED],
        )
        .exclude(bank_account_id=transaction.bank_account_id)
        .select_related("bank_account")
        .order_by("transaction_date", "created_at")
    )


# ------------------------------------------------------------ mutations


def _refresh_transaction_status(transaction: BankTransaction) -> None:
    """UNMATCHED -> SUGGESTED -> MATCHED, derived, never set by hand.

    A PARTIALLY confirmed line reads as SUGGESTED rather than MATCHED: it
    still needs a decision, and the four statuses the product exposes have no
    separate "partly explained" value. Calling it MATCHED would be the
    dangerous rounding — it would let the reconciliation close over a line
    that is only half accounted for.
    """
    if transaction.status == BankTransactionStatus.EXCLUDED:
        return
    confirmed = get_matched_amount(transaction=transaction)
    if confirmed >= abs(transaction.amount):
        new_status = BankTransactionStatus.MATCHED
    elif confirmed > 0 or BankTransactionMatch.objects.filter(transaction=transaction).exists():
        new_status = BankTransactionStatus.SUGGESTED
    else:
        new_status = BankTransactionStatus.UNMATCHED
    if new_status != transaction.status:
        transaction.status = new_status
        transaction.save(update_fields=["status", "updated_at"])


@db_transaction.atomic
def suggest_matches(*, transaction_id, organization, limit: int = 5, replace: bool = True) -> list:
    """Write unconfirmed suggestions for one line. Confirms nothing."""
    transaction = _get_transaction_for_update(transaction_id=transaction_id, organization=organization)
    if transaction.status in (BankTransactionStatus.EXCLUDED, BankTransactionStatus.MATCHED):
        return []
    _assert_open_for_changes(transaction)

    if replace:
        # Stale suggestions outlive their usefulness the moment the books
        # change; keeping them would show a user a candidate that has since
        # been matched elsewhere.
        BankTransactionMatch.objects.filter(transaction=transaction, is_confirmed=False).delete()

    created = []
    for candidate in find_candidates(transaction=transaction, organization=organization)[:limit]:
        created.append(
            BankTransactionMatch.objects.create(
                organization=organization,
                transaction=transaction,
                amount=abs(transaction.amount),
                match_type=candidate["match_type"],
                suggestion_source=SuggestionSource.SYSTEM,
                confidence=candidate["confidence"],
                reason=candidate["reason"],
                **{candidate["counterpart_field"]: candidate["counterpart"]},
            )
        )
    _refresh_transaction_status(transaction)
    return created


@db_transaction.atomic
def record_ai_suggestion(
    *, transaction_id, organization, counterpart_field: str, counterpart, reason: str = "", confidence=None
) -> BankTransactionMatch:
    """The ONLY way an AI-proposed match enters the system.

    It lands unconfirmed, tagged `SuggestionSource.AI`, and no code path will
    auto-confirm it (`auto_match` skips AI sources; `confirm_match` demands a
    named human actor). Root CLAUDE.md: AI may suggest, deterministic logic
    performs the authoritative act — and reconciliation status IS an
    authoritative accounting fact.
    """
    transaction = _get_transaction_for_update(transaction_id=transaction_id, organization=organization)
    _assert_open_for_changes(transaction)
    _validate_counterpart(
        counterpart_field=counterpart_field,
        counterpart=counterpart,
        transaction=transaction,
        organization=organization,
    )
    match = BankTransactionMatch.objects.create(
        organization=organization,
        transaction=transaction,
        amount=abs(transaction.amount),
        match_type=MatchType.AMOUNT_DATE,
        suggestion_source=SuggestionSource.AI,
        confidence=Decimal(confidence).quantize(_SCORE_QUANTUM) if confidence is not None else Decimal("0"),
        reason=reason,
        **{counterpart_field: counterpart},
    )
    _refresh_transaction_status(transaction)
    return match


@db_transaction.atomic
def create_match(
    *,
    transaction_id,
    organization,
    counterpart_field: str,
    counterpart,
    amount=None,
    match_type: str = MatchType.MANUAL,
    reason: str = "",
    actor=None,
    confirm: bool = True,
) -> BankTransactionMatch:
    """A person (or a rule) says this line is that document.

    Posts nothing. The counterpart already carries its own journal — see this
    module's docstring.
    """
    transaction = _get_transaction_for_update(transaction_id=transaction_id, organization=organization)
    _assert_open_for_changes(transaction)
    if transaction.status == BankTransactionStatus.EXCLUDED:
        raise ApplicationError(
            "An excluded transaction cannot be matched; restore it first.", code="transaction_excluded"
        )
    _validate_counterpart(
        counterpart_field=counterpart_field,
        counterpart=counterpart,
        transaction=transaction,
        organization=organization,
    )

    amount = abs(amount) if amount is not None else abs(transaction.amount)
    if amount <= 0:
        raise ApplicationError("Match amount must be positive.", code="match_amount_invalid")

    match = BankTransactionMatch.objects.create(
        organization=organization,
        transaction=transaction,
        amount=amount,
        match_type=match_type,
        suggestion_source=SuggestionSource.USER if actor is not None else SuggestionSource.SYSTEM,
        confidence=Decimal("1.000") if confirm else Decimal("0.500"),
        reason=reason,
        **{counterpart_field: counterpart},
    )
    if confirm:
        return confirm_match(match_id=match.id, organization=organization, actor=actor)
    _refresh_transaction_status(transaction)
    return match


@db_transaction.atomic
def confirm_match(*, match_id, organization, actor=None, _allow_ai: bool = False) -> BankTransactionMatch:
    """Turn a suggestion into the reconciliation record.

    The two capacity checks below are taken AFTER locking the statement line,
    and that ordering is the whole point: an unlocked "how much is already
    matched?" read is a read-then-write race, and two concurrent confirms each
    see zero matched and each succeed. That exact bug let one goods receipt be
    billed five times in Phase 4 — see purchases/services/bills.py.
    """
    match = (
        BankTransactionMatch.objects.select_for_update()
        .filter(pk=match_id, organization=organization)
        .first()
    )
    if match is None:
        raise ApplicationError("Match not found.", code="bank_match_not_found", status_code=404)
    if match.is_confirmed:
        return match

    if match.suggestion_source == SuggestionSource.AI and actor is None:
        raise ApplicationError(
            "An AI-suggested match must be confirmed by a person.", code="ai_match_requires_human",
            status_code=403,
        )

    transaction = _get_transaction_for_update(
        transaction_id=match.transaction_id, organization=organization
    )
    _assert_open_for_changes(transaction)
    if transaction.status == BankTransactionStatus.EXCLUDED:
        raise ApplicationError(
            "An excluded transaction cannot be matched; restore it first.", code="transaction_excluded"
        )

    counterpart_field = match.counterpart_type
    # Locked before its matched-total is read — see _lock_counterpart.
    counterpart = _lock_counterpart(
        counterpart_field=counterpart_field, counterpart=match.counterpart, organization=organization
    )
    _validate_counterpart(
        counterpart_field=counterpart_field,
        counterpart=counterpart,
        transaction=transaction,
        organization=organization,
    )

    already_on_line = get_matched_amount(transaction=transaction, exclude_match_id=match.id)
    if already_on_line + match.amount > abs(transaction.amount):
        raise ApplicationError(
            "Matches would explain more than this transaction's amount.", code="over_matched_transaction"
        )

    # A transfer is the one counterpart that legitimately explains a line on
    # each of two accounts, so its capacity is asked per leg.
    already_on_counterpart = get_counterpart_matched_amount(
        counterpart_field=counterpart_field,
        counterpart=counterpart,
        exclude_match_id=match.id,
        bank_account=transaction.bank_account if counterpart_field == "bank_transfer" else None,
    )
    capacity = _counterpart_capacity(
        counterpart_field=counterpart_field, counterpart=counterpart, transaction=transaction
    )
    if already_on_counterpart + match.amount > capacity:
        raise ApplicationError(
            "That document is already accounted for on another statement line.",
            code="over_matched_counterpart",
        )

    match.is_confirmed = True
    match.confirmed_by = actor
    match.confirmed_at = timezone.now()
    match.save(update_fields=["is_confirmed", "confirmed_by", "confirmed_at", "updated_at"])

    # Competing suggestions for the portion now explained are obsolete.
    if get_matched_amount(transaction=transaction) >= abs(transaction.amount):
        BankTransactionMatch.objects.filter(transaction=transaction, is_confirmed=False).delete()

    _refresh_transaction_status(transaction)
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="banking.BankTransactionMatch",
        object_id=match.id,
        changes={
            "transaction": str(transaction.id),
            "counterpart_type": counterpart_field,
            "counterpart_id": str(counterpart.pk),
            "amount": str(match.amount),
            "source": match.suggestion_source,
        },
    )
    return match


@db_transaction.atomic
def unmatch(*, match_id, organization, actor=None) -> BankTransaction:
    """Undo one match. The counterpart document is untouched — it was never
    changed by being matched in the first place."""
    match = (
        BankTransactionMatch.objects.select_for_update()
        .filter(pk=match_id, organization=organization)
        .first()
    )
    if match is None:
        raise ApplicationError("Match not found.", code="bank_match_not_found", status_code=404)

    transaction = _get_transaction_for_update(
        transaction_id=match.transaction_id, organization=organization
    )
    _assert_open_for_changes(transaction)

    if match.match_type == MatchType.CATEGORIZATION:
        raise ApplicationError(
            "A categorized transaction posted a journal; reverse it with "
            "uncategorize_transaction so the ledger stays correct.",
            code="categorization_needs_reversal",
        )

    counterpart_field = match.counterpart_type
    counterpart_id = getattr(match, f"{counterpart_field}_id", None)
    match.delete()
    _refresh_transaction_status(transaction)

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.DELETE,
        object_type="banking.BankTransactionMatch",
        object_id=match_id,
        changes={
            "transaction": str(transaction.id),
            "counterpart_type": counterpart_field,
            "counterpart_id": str(counterpart_id),
        },
    )
    return transaction


@db_transaction.atomic
def auto_match(*, transaction_id, organization, actor=None) -> BankTransactionMatch | None:
    """Confirm the one obvious answer, if there is exactly one.

    THE AMBIGUITY RULE. Auto-confirmation requires a single candidate at or
    above the threshold. Two candidates both scoring 1.000 — two customers who
    each paid 25,000 on the same day — is not a tie to be broken by id order
    or by whichever the query returned first; it is a question only the user
    can answer, and answering it for them attributes a payment to the wrong
    customer and sends the wrong dunning letter.
    """
    transaction = _get_transaction_for_update(transaction_id=transaction_id, organization=organization)
    if transaction.status in (BankTransactionStatus.MATCHED, BankTransactionStatus.EXCLUDED):
        return None
    _assert_open_for_changes(transaction)

    candidates = find_candidates(transaction=transaction, organization=organization)
    strong = [c for c in candidates if c["confidence"] >= AUTO_CONFIRM_THRESHOLD]
    if len(strong) != 1:
        return None

    winner = strong[0]
    return create_match(
        transaction_id=transaction.id,
        organization=organization,
        counterpart_field=winner["counterpart_field"],
        counterpart=winner["counterpart"],
        match_type=winner["match_type"],
        reason=winner["reason"],
        actor=actor,
        confirm=True,
    )


@db_transaction.atomic
def categorize_transaction(
    *, transaction_id, organization, account, organization_currency=None, description: str = "", actor=None
) -> BankTransactionMatch:
    """The one path in this module that posts accounting.

    Used when a statement line has no document behind it — a bank charge,
    interest, an owner's contribution. The journal is the line's FIRST
    appearance in the books, so it belongs here in exactly the way a matched
    payment's journal does not.

    Journal, using the app-wide sign convention with no special case for
    credit cards: money in debits the bank GL account and credits `account`;
    money out does the reverse.
    """
    transaction = _get_transaction_for_update(transaction_id=transaction_id, organization=organization)
    _assert_open_for_changes(transaction)
    if transaction.status == BankTransactionStatus.EXCLUDED:
        raise ApplicationError(
            "An excluded transaction cannot be categorized; restore it first.", code="transaction_excluded"
        )
    if account.organization_id != organization.id:
        raise ApplicationError(
            "The category account must belong to the posting organization.", code="cross_org_reference"
        )
    if account.id == transaction.bank_account.account_id:
        raise ApplicationError(
            "A transaction cannot be categorized to its own bank account.", code="category_account_invalid"
        )

    unexplained = abs(transaction.amount) - get_matched_amount(transaction=transaction)
    if unexplained <= 0:
        raise ApplicationError(
            "This transaction is already fully explained.", code="transaction_fully_matched"
        )

    bank_gl_account_id = transaction.bank_account.account_id
    if transaction.is_inflow:
        lines = [
            {"account_id": bank_gl_account_id, "debit": unexplained},
            {"account_id": account.id, "credit": unexplained},
        ]
    else:
        lines = [
            {"account_id": account.id, "debit": unexplained},
            {"account_id": bank_gl_account_id, "credit": unexplained},
        ]

    currency = organization_currency or transaction.bank_account.currency
    journal = post_banking_journal(
        organization=organization,
        posting_date=transaction.transaction_date,
        currency=currency,
        lines=lines,
        memo=description or f"Bank transaction {transaction.transaction_date}",
        source_type="banking.BankTransaction",
        source_id=str(transaction.id),
        actor=actor,
    )

    match = BankTransactionMatch.objects.create(
        organization=organization,
        transaction=transaction,
        journal_entry=journal,
        amount=unexplained,
        match_type=MatchType.CATEGORIZATION,
        suggestion_source=SuggestionSource.USER if actor is not None else SuggestionSource.SYSTEM,
        confidence=Decimal("1.000"),
        reason=description or f"Categorized to {account.code}",
        is_confirmed=True,
        confirmed_by=actor,
        confirmed_at=timezone.now(),
    )
    BankTransactionMatch.objects.filter(transaction=transaction, is_confirmed=False).delete()
    _refresh_transaction_status(transaction)

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.POST,
        object_type="banking.BankTransaction",
        object_id=transaction.id,
        changes={
            "categorized_to": account.code,
            "amount": str(unexplained),
            "journal_id": str(journal.id),
        },
    )
    return match


@db_transaction.atomic
def uncategorize_transaction(*, match_id, organization, actor=None) -> BankTransaction:
    """Reverse a categorization's journal and drop the match.

    A reversing journal, never a deleted one — root CLAUDE.md: corrections
    append, they do not erase.
    """
    from accounting.services.posting import reverse_journal

    match = (
        BankTransactionMatch.objects.select_for_update()
        .filter(pk=match_id, organization=organization, match_type=MatchType.CATEGORIZATION)
        .first()
    )
    if match is None:
        raise ApplicationError("Categorization not found.", code="categorization_not_found", status_code=404)

    transaction = _get_transaction_for_update(
        transaction_id=match.transaction_id, organization=organization
    )
    _assert_open_for_changes(transaction)

    if match.journal_entry_id:
        reverse_journal(
            journal_id=match.journal_entry_id,
            organization=organization,
            actor=actor,
            posting_date=timezone.now().date(),
            memo=f"Reversal of bank categorization {match.id}",
        )
    match.delete()
    _refresh_transaction_status(transaction)

    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.REVERSE,
        object_type="banking.BankTransaction",
        object_id=transaction.id,
        changes={"uncategorized_match": str(match_id)},
    )
    return transaction
