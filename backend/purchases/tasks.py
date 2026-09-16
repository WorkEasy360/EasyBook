"""Celery tasks for purchases.

Recurring bill/expense generation must run here, never inline in a web
request (root CLAUDE.md). Periodic invocation (celery beat or an external
scheduler) is a deployment concern left for the infrastructure phase that
introduces one — both tasks are safe to call as often as needed, since
generation is idempotent per occurrence (see services/recurring.py).
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
