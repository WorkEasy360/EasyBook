"""Fact builders: reload authoritative, tenant-scoped data for one entity so
condition evaluation never trusts the event payload alone (phase section
16). Domain model imports are lazy (inside each builder), mirroring
documents/services/links.py's `_resolvers()` — automation is the top layer
and must not pull every domain module at Django app-loading time.

Only the triggers this phase actually wires end-to-end have a builder here.
A trigger registered in `triggers/registry.py` without one here still
accepts rules (validated against its field allowlist) but always evaluates
against an empty facts dict until a later slice adds its builder — exactly
like `schedule.*`/`manual`, which never carry entity fields at all.
"""

import datetime


def _invoice_facts(*, organization, entity_id):
    from sales.models.invoice import Invoice
    from sales.selectors import get_invoice_amount_due

    invoice = Invoice.objects.filter(pk=entity_id).first()
    if invoice is None:
        return None
    return {
        "amount_due": get_invoice_amount_due(invoice=invoice),
        "status": invoice.status,
        "customer_id": str(invoice.customer_id),
    }


def _invoice_overdue_facts(*, organization, entity_id, as_of=None):
    from sales.models.invoice import Invoice
    from sales.selectors import get_invoice_amount_due

    invoice = Invoice.objects.filter(pk=entity_id).first()
    if invoice is None:
        return None
    as_of = as_of or datetime.date.today()
    return {
        "amount_due": get_invoice_amount_due(invoice=invoice),
        "days_overdue": max((as_of - invoice.due_date).days, 0),
        "customer_id": str(invoice.customer_id),
    }


def _stock_low_facts(*, organization, entity_id):
    from inventory.selectors import get_stock_on_hand
    from items.models.item import Item

    item = Item.objects.filter(pk=entity_id).first()
    if item is None:
        return None
    return {
        "quantity_on_hand": get_stock_on_hand(item=item),
        "reorder_level": item.reorder_level,
        "item_id": str(item.id),
    }


_FACT_BUILDERS = {
    "invoice.posted": _invoice_facts,
    "invoice.paid": _invoice_facts,
    "invoice.overdue": _invoice_overdue_facts,
    "stock.low": _stock_low_facts,
}


def trigger_requires_entity(trigger_type: str) -> bool:
    """True when evaluating this trigger loads one record by id — so a manual
    run of it is meaningless (and fails in the worker) without an entity_id."""
    return trigger_type in _FACT_BUILDERS


def build_facts(*, trigger_type: str, organization, entity_id, as_of=None) -> dict:
    builder = _FACT_BUILDERS.get(trigger_type)
    if builder is None:
        return {}
    kwargs = {"organization": organization, "entity_id": entity_id}
    if trigger_type == "invoice.overdue":
        kwargs["as_of"] = as_of
    facts = builder(**kwargs)
    return facts or {}
