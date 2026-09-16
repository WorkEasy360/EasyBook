"""Domain event receivers (phase sections 9, 20).

`sales`/`purchases`/`inventory`/... never import `automation` — it is the
top layer (see automation/CLAUDE.md), so it listens for their EXISTING
`post_save` signals instead, exactly mirroring ai/rag/signals.py's
relationship with `documents`. No change to any lower-layer service.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from automation.services.events import record_event
from sales.models.invoice import Invoice, InvoiceStatus


@receiver(post_save, sender=Invoice, dispatch_uid="automation_invoice_posted")
def invoice_posted(sender, instance: Invoice, created, update_fields=None, **kwargs):
    if created:
        return
    relevant = update_fields is None or "status" in update_fields
    if relevant and instance.status == InvoiceStatus.SENT:
        record_event(
            organization=instance.organization,
            event_type="invoice.posted",
            entity_id=instance.id,
            occurred_at=instance.posted_at,
            payload={"invoice_number": instance.invoice_number},
        )
