"""Celery tasks for purchases.

Recurring bill/expense generation must run here, never inline in a web
request (root CLAUDE.md). Scheduled hourly via
CELERY_BEAT_SCHEDULE["purchases-generate-recurring-bills"/"purchases-generate-recurring-expenses"]
(config/settings/base.py, phase 12 slice 6) — safe to call more often too,
since generation is idempotent per occurrence (see services/recurring.py).
"""

from celery import shared_task

from purchases.services.recurring import generate_due_bills, generate_due_expenses


@shared_task
def generate_recurring_bills_task():
    bills = generate_due_bills()
    return {"generated_count": len(bills), "bill_ids": [str(bill.id) for bill in bills]}


@shared_task
def generate_recurring_expenses_task():
    expenses = generate_due_expenses()
    return {"generated_count": len(expenses), "expense_ids": [str(expense.id) for expense in expenses]}
