"""Scheduled-trigger evaluation (phase sections 42-48, 87).

Two distinct mechanisms, matching the two shapes of "schedule" the phase
spec describes:

- PURE schedule triggers (schedule.daily/weekly/monthly): a rule fires once
  per calendar occurrence, tracked by `AutomationSchedule.next_run_at` +
  `AutomationScheduleOccurrence` (same shape as sales.RecurringInvoiceRun).
- SCAN-based triggers (invoice.overdue, stock.low): these describe a STATE,
  not a one-shot occurrence, so a rule is evaluated against whatever
  currently matches an EXISTING selector (never re-derived), with
  `AutomationScanOccurrence` + `AutomationRule.cooldown_days` preventing the
  same entity from re-firing the rule every single scan.

Missed-schedule policy (phase section 44): SKIP_MISSED. If the scheduler was
down and `next_run_at` is now days in the past, this advances directly to
the next occurrence AFTER `as_of` rather than generating every occurrence
missed in between — unlike sales' recurring-invoice catch-up, an automation
notification/report/draft has no standing value in being backfilled for a
date that has already passed silently. This is an explicit, documented
default (phase section 44 requires one), not an oversight.
"""

import datetime

from django.conf import settings
from django.db import IntegrityError, transaction

from automation.models.schedule import AutomationScanOccurrence, AutomationSchedule, AutomationScheduleOccurrence
from core.exceptions import ApplicationError

SCHEDULE_TRIGGER_TYPES = frozenset({"schedule.daily", "schedule.weekly", "schedule.monthly"})
SCAN_TRIGGER_TYPES = frozenset({"invoice.overdue", "stock.low"})

DEFAULT_COOLDOWN_DAYS = 1


def _advance(current: datetime.date, trigger_type: str) -> datetime.date:
    if trigger_type == "schedule.daily":
        return current + datetime.timedelta(days=1)
    if trigger_type == "schedule.weekly":
        return current + datetime.timedelta(weeks=1)
    if trigger_type == "schedule.monthly":
        from dateutil.relativedelta import relativedelta

        return current + relativedelta(months=1)
    raise ApplicationError(f"'{trigger_type}' is not a pure schedule trigger.", code="automation_not_a_schedule_trigger")


def ensure_schedule(*, rule, start_date=None) -> AutomationSchedule | None:
    """Creates the AutomationSchedule row for a newly-activated pure-schedule
    rule if one doesn't already exist. No-op for every other trigger type."""
    if rule.trigger_type not in SCHEDULE_TRIGGER_TYPES:
        return None
    schedule, _created = AutomationSchedule.objects.get_or_create(
        rule=rule, defaults={"organization": rule.organization, "next_run_at": start_date or datetime.date.today()}
    )
    return schedule


def claim_due_schedule_occurrence(*, schedule: AutomationSchedule, as_of, execution) -> AutomationScheduleOccurrence | None:
    """Atomically claims `schedule.next_run_at` as fired and advances it past
    `as_of` (SKIP_MISSED). Returns None — without creating anything — if
    another worker already claimed this exact occurrence (the UniqueConstraint
    is the authoritative guard, phase section 43)."""
    if schedule.next_run_at > as_of:
        return None
    scheduled_for = schedule.next_run_at
    try:
        with transaction.atomic():
            occurrence = AutomationScheduleOccurrence.objects.create(
                organization=schedule.organization, rule=schedule.rule, scheduled_for=scheduled_for, execution=execution
            )
            next_run_at = scheduled_for
            while next_run_at <= as_of:
                next_run_at = _advance(next_run_at, schedule.rule.trigger_type)
            schedule.next_run_at = next_run_at
            schedule.save(update_fields=["next_run_at", "updated_at"])
    except IntegrityError:
        return None
    return occurrence


def _cooldown_days_for(rule) -> int:
    if rule.cooldown_days is not None:
        return rule.cooldown_days
    return getattr(settings, "AUTOMATION_DEFAULT_COOLDOWN_DAYS", DEFAULT_COOLDOWN_DAYS)


def is_scan_entity_in_cooldown(*, rule, entity_id, as_of) -> bool:
    cooldown_days = _cooldown_days_for(rule)
    cutoff = as_of - datetime.timedelta(days=cooldown_days)
    return AutomationScanOccurrence.objects.filter(
        rule=rule, entity_id=str(entity_id), occurrence_date__gt=cutoff
    ).exists()


def claim_scan_occurrence(*, rule, entity_id, as_of, execution) -> AutomationScanOccurrence | None:
    """Atomically claims (rule, entity_id, as_of) as fired today. Returns
    None if another worker already claimed it (phase sections 47, 81)."""
    try:
        with transaction.atomic():
            return AutomationScanOccurrence.objects.create(
                organization=rule.organization, rule=rule, entity_id=str(entity_id), occurrence_date=as_of,
                execution=execution,
            )
    except IntegrityError:
        return None


def scan_candidate_entity_ids(*, trigger_type: str, organization) -> list[str]:
    """Reuses EXISTING selectors — never re-derives the overdue/low-stock
    calculation (root CLAUDE.md, phase section 46-47)."""
    if trigger_type == "invoice.overdue":
        from sales.selectors import get_overdue_invoices

        return [str(invoice.id) for invoice in get_overdue_invoices(organization=organization)]
    if trigger_type == "stock.low":
        from inventory.selectors import get_low_stock_items

        return [str(row["item"].id) for row in get_low_stock_items(organization=organization)]
    return []
