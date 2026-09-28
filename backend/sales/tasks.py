"""Celery tasks for sales. Recurring-invoice generation must run here, never
inline in a web request (root CLAUDE.md). Scheduled hourly via
CELERY_BEAT_SCHEDULE["sales-generate-recurring-invoices"] (config/settings/base.py,
phase 12 slice 6) — safe to call more often too, since generate_due_invoices
is idempotent per occurrence (see services/recurring_invoices.py).
"""

from celery import shared_task

from sales.services.recurring_invoices import generate_due_invoices


@shared_task
def generate_recurring_invoices_task():
    invoices = generate_due_invoices()
    return {"generated_count": len(invoices), "invoice_ids": [str(invoice.id) for invoice in invoices]}
