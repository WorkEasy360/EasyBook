"""Reconciliation rules: deterministic, first-match-wins, opt-in to posting.

FIRST MATCH WINS, AND RULES DO NOT COMPOSE.
A rule set where several rules each contribute part of the outcome is one
where nobody can predict what a given statement line will do, and where
adding a rule silently changes the treatment of lines the author never looked
at. One rule fires, in priority order, and its action is the whole outcome.
That is less expressive and enormously more explainable, which on something
that posts to a ledger is the right trade.

AUTO-POSTING IS OPT-IN PER RULE.
With `auto_confirm` off — the default — a matching rule produces a suggestion
and stops. With it on, the rule categorizes the line and posts the journal
with no human in the loop. Both are legitimate; the difference must be a
choice the user made for a rule they trust, which is why the flag lives on
the rule and not in a global setting.
"""

from django.db import transaction as db_transaction

from audit.models import AuditLog
from audit.services import record as record_audit
from banking.models.rule import BankRule, RuleAction, RuleDirection
from banking.models.statement import BankTransaction, BankTransactionStatus
from core.exceptions import ApplicationError


def _validate_rule_fields(*, organization, action, target_account, vendor, customer, bank_account) -> None:
    if action == RuleAction.CATEGORIZE and target_account is None:
        raise ApplicationError(
            "A categorizing rule needs the account to categorize to.", code="rule_target_account_required"
        )
    if action == RuleAction.EXCLUDE and target_account is not None:
        raise ApplicationError(
            "An excluding rule has no account to post to.", code="rule_target_account_unexpected"
        )
    for label, obj in (
        ("target_account", target_account),
        ("vendor", vendor),
        ("customer", customer),
        ("bank_account", bank_account),
    ):
        if obj is not None and obj.organization_id != organization.id:
            raise ApplicationError(
                f"{label} belongs to another organization.", code="cross_org_reference"
            )


@db_transaction.atomic
def create_rule(
    *,
    organization,
    name: str,
    action: str = RuleAction.CATEGORIZE,
    target_account=None,
    bank_account=None,
    description_contains: str = "",
    counterparty_contains: str = "",
    direction: str = RuleDirection.ANY,
    amount_min=None,
    amount_max=None,
    vendor=None,
    customer=None,
    auto_confirm: bool = False,
    priority: int = 100,
    actor=None,
) -> BankRule:
    _validate_rule_fields(
        organization=organization, action=action, target_account=target_account,
        vendor=vendor, customer=customer, bank_account=bank_account,
    )
    if not any([description_contains.strip(), counterparty_contains.strip(), amount_min is not None,
                amount_max is not None, direction != RuleDirection.ANY]):
        # A rule with no conditions matches every line on the account and
        # would categorize an entire statement to one account on import.
        raise ApplicationError(
            "A rule needs at least one condition.", code="rule_has_no_conditions"
        )

    rule = BankRule.objects.create(
        organization=organization,
        name=name,
        priority=priority,
        bank_account=bank_account,
        description_contains=description_contains.strip(),
        counterparty_contains=counterparty_contains.strip(),
        direction=direction,
        amount_min=amount_min,
        amount_max=amount_max,
        action=action,
        target_account=target_account,
        vendor=vendor,
        customer=customer,
        auto_confirm=auto_confirm,
    )
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="banking.BankRule",
        object_id=rule.id,
        changes={"name": name, "action": action, "auto_confirm": auto_confirm},
    )
    return rule


@db_transaction.atomic
def update_rule(*, rule: BankRule, actor=None, **fields) -> BankRule:
    allowed = {
        "name", "priority", "is_active", "description_contains", "counterparty_contains",
        "direction", "amount_min", "amount_max", "action", "target_account", "vendor",
        "customer", "auto_confirm", "bank_account",
    }
    unknown = set(fields) - allowed
    if unknown:
        raise ApplicationError(f"Unknown rule fields: {sorted(unknown)}.", code="rule_field_unknown")

    merged = {field: fields.get(field, getattr(rule, field)) for field in
              ("action", "target_account", "vendor", "customer", "bank_account")}
    _validate_rule_fields(organization=rule.organization, **merged)

    changes = {}
    for field, value in fields.items():
        if getattr(rule, field) == value:
            continue
        changes[field] = str(value.pk) if hasattr(value, "pk") else str(value)
        setattr(rule, field, value)
    if not changes:
        return rule

    rule.save(update_fields=[*changes.keys(), "updated_at"])
    record_audit(
        organization_id=rule.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="banking.BankRule",
        object_id=rule.id,
        changes=changes,
    )
    return rule


def rule_matches(*, rule: BankRule, transaction: BankTransaction) -> bool:
    """Every populated condition must hold. Case-insensitive substring only —
    see banking/models/rule.py for why there is no regex."""
    if rule.bank_account_id and rule.bank_account_id != transaction.bank_account_id:
        return False
    if rule.direction == RuleDirection.INFLOW and not transaction.is_inflow:
        return False
    if rule.direction == RuleDirection.OUTFLOW and transaction.is_inflow:
        return False

    magnitude = abs(transaction.amount)
    if rule.amount_min is not None and magnitude < rule.amount_min:
        return False
    if rule.amount_max is not None and magnitude > rule.amount_max:
        return False

    if rule.description_contains:
        haystack = f"{transaction.description} {transaction.bank_reference}".lower()
        if rule.description_contains.lower() not in haystack:
            return False
    if rule.counterparty_contains:
        if rule.counterparty_contains.lower() not in (transaction.counterparty_name or "").lower():
            return False
    return True


def find_matching_rule(*, organization, transaction: BankTransaction) -> BankRule | None:
    """The first active rule, by priority, that matches. Ties break on name so
    the outcome is stable across runs and across databases."""
    for rule in BankRule.objects.filter(organization=organization, is_active=True).order_by(
        "priority", "name", "id"
    ):
        if rule_matches(rule=rule, transaction=transaction):
            return rule
    return None


@db_transaction.atomic
def apply_rules_to_transaction(*, transaction_id, organization, actor=None) -> dict:
    """Run the rule set against one line.

    Returns what happened rather than raising when nothing does: "no rule
    matched" is the ordinary case for most lines, not an error.
    """
    from banking.services.matching import categorize_transaction
    from banking.services.transactions import exclude_transaction

    transaction = (
        BankTransaction.objects.select_for_update()
        .filter(pk=transaction_id, organization=organization)
        .first()
    )
    if transaction is None:
        raise ApplicationError("Bank transaction not found.", code="bank_transaction_not_found", status_code=404)
    if transaction.status in (BankTransactionStatus.MATCHED, BankTransactionStatus.EXCLUDED):
        return {"rule": None, "applied": False, "reason": "transaction_not_open"}

    rule = find_matching_rule(organization=organization, transaction=transaction)
    if rule is None:
        return {"rule": None, "applied": False, "reason": "no_rule_matched"}

    if rule.action == RuleAction.EXCLUDE:
        exclude_transaction(
            transaction_id=transaction.id,
            organization=organization,
            reason=f"Rule: {rule.name}",
            actor=actor,
        )
        return {"rule": rule, "applied": True, "reason": "excluded"}

    if not rule.auto_confirm:
        # Deliberately NOT a suggestion row: a rule proposes an ACCOUNT, not
        # an existing document, and BankTransactionMatch only points at
        # documents. Inventing a journal just to have something to point at
        # would post the very entry the user has not yet approved.
        return {"rule": rule, "applied": False, "reason": "awaiting_confirmation"}

    categorize_transaction(
        transaction_id=transaction.id,
        organization=organization,
        account=rule.target_account,
        description=f"Rule: {rule.name}",
        actor=actor,
    )
    return {"rule": rule, "applied": True, "reason": "categorized"}


def apply_rules_to_statement_import(*, statement_import, organization, actor=None) -> dict:
    """Run the rule set over everything one import brought in."""
    counts = {"categorized": 0, "excluded": 0, "awaiting_confirmation": 0, "no_rule_matched": 0}
    for transaction in statement_import.transactions.all().order_by("transaction_date", "created_at"):
        outcome = apply_rules_to_transaction(
            transaction_id=transaction.id, organization=organization, actor=actor
        )
        counts[outcome["reason"]] = counts.get(outcome["reason"], 0) + 1
    return counts
