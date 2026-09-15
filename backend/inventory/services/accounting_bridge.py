"""Shared helper for posting inventory-driven accounting entries.

Every inventory operation that has a financial impact (opening stock, stock
adjustments) goes through accounting.services.journals/posting — the Phase 1
engine — via this one function. Nothing in inventory ever constructs a
JournalLine directly (see accounting/CLAUDE.md, root CLAUDE.md).
"""

from collections import defaultdict
from decimal import Decimal

from accounting.services.journals import create_draft_journal
from accounting.services.posting import post_journal


def post_inventory_journal(
    *,
    organization,
    posting_date,
    currency,
    net_amount_by_inventory_account: dict,
    contra_account,
    memo: str,
    source_type: str,
    source_id: str,
    actor=None,
):
    """`net_amount_by_inventory_account` maps an accounting.Account (an
    item's inventory_account) to a signed Decimal — positive means the
    inventory asset increased (debit), negative means it decreased (credit).
    One journal line per distinct inventory account plus one balancing line
    on `contra_account`. Returns the posted JournalEntry, or None if there is
    no net financial effect to record.
    """
    lines = []
    total_net = Decimal("0")
    for account, net_amount in net_amount_by_inventory_account.items():
        if net_amount == 0:
            continue
        total_net += net_amount
        if net_amount > 0:
            lines.append({"account_id": account.id, "debit": net_amount})
        else:
            lines.append({"account_id": account.id, "credit": -net_amount})

    if not lines or total_net == 0:
        return None

    if total_net > 0:
        lines.append({"account_id": contra_account.id, "credit": total_net})
    else:
        lines.append({"account_id": contra_account.id, "debit": -total_net})

    journal = create_draft_journal(
        organization=organization,
        posting_date=posting_date,
        currency=currency,
        lines=lines,
        memo=memo,
        source_type=source_type,
        source_id=source_id,
        created_by=actor,
    )
    return post_journal(journal_id=journal.id, organization=organization, actor=actor)


def group_net_amount_by_inventory_account(entries: list[dict]) -> dict:
    """`entries` is a list of {"item": Item, "signed_value": Decimal}.
    Skips items with no inventory_account configured (financially neutral —
    see items/CLAUDE.md)."""
    grouped = defaultdict(lambda: Decimal("0"))
    for entry in entries:
        item = entry["item"]
        if item.inventory_account_id is None:
            continue
        grouped[item.inventory_account] += entry["signed_value"]
    return dict(grouped)
