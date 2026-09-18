"""Celery tasks for automation (phase sections 13-14, 28-31, 42-48, 54).

Every task explicitly establishes tenant context before touching any
tenant-scoped table — a worker must never assume request middleware ran
(core/CLAUDE.md, phase section 54). Cross-org scans enumerate
`accounts.Organization` first and open `tenant_context()` per organization,
the same pattern as sales.services.recurring_invoices.generate_due_invoices
(phase section 55) — RLS enforces the boundary at the database layer
regardless.
"""

import datetime

from celery import shared_task
from celery.utils.log import get_task_logger
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from automation.models.event import AutomationEvent
from automation.models.execution import AutomationExecution, ExecutionStatus, TriggerSource
from automation.models.rule import RuleStatus
from automation.selectors import get_active_rules_for_trigger
from core.tenancy import tenant_context

logger = get_task_logger("automation.tasks")


class _RetryableStepsRemain(Exception):
    """Internal marker: at least one step in this execution failed with a
    retryable category (phase section 28) — Celery's own backoff/max_retries
    below govern the retry, never an unbounded loop."""


@shared_task(
    bind=True,
    max_retries=5,
    retry_backoff=True,
    retry_backoff_max=3600,
    retry_jitter=True,
    autoretry_for=(_RetryableStepsRemain,),
)
def run_execution_task(self, execution_id: str, organization_id: str):
    from automation.services.execution import retryable_step_ids, run_execution

    try:
        with tenant_context(organization_id=organization_id):
            execution = AutomationExecution.objects.filter(pk=execution_id).first()
            if execution is None:
                logger.warning("automation_execution_not_found", extra={"execution_id": execution_id})
                return {"skipped": "execution_not_found"}

            run_execution(execution)
            logger.info(
                "automation_execution_ran",
                extra={
                    "execution_id": execution_id, "rule_id": str(execution.rule_id), "status": execution.status,
                    "attempt": execution.attempt_count,
                },
            )
            retry_needed = retryable_step_ids(execution).exists()
            status = execution.status
    except Exception as exc:
        # This attempt's own transaction is already rolled back, so anything it
        # recorded is gone. Say so on the execution from a fresh transaction,
        # otherwise the failure is invisible: the row simply sits at PENDING and
        # only the recovery sweeper's budget (recovery_attempts) ever stops it.
        _record_failed_attempt(execution_id=execution_id, organization_id=organization_id, exc=exc)
        raise

    # Raised only after tenant_context's transaction has committed. Raised
    # inside it, the exception rolled back everything this attempt recorded —
    # attempt counts, RETRYING/SUCCEEDED step states, created notifications —
    # so every retry repeated every side effect and the step budget was never
    # reached.
    if retry_needed:
        raise _RetryableStepsRemain(execution_id)
    return {"execution_id": execution_id, "status": status}


def _record_failed_attempt(*, execution_id: str, organization_id: str, exc: Exception) -> None:
    with tenant_context(organization_id=organization_id):
        AutomationExecution.objects.filter(pk=execution_id).update(
            error_summary=f"An attempt failed with {type(exc).__name__}. Automatic recovery will retry it."[:2000],
            updated_at=timezone.now(),
        )
    logger.warning(
        "automation_execution_attempt_failed",
        extra={"execution_id": execution_id, "error_type": type(exc).__name__},
    )


def _dispatch_events_for_org(organization) -> int:
    from automation.services.execution import create_execution_for_event

    dispatched = 0
    with transaction.atomic():
        events = list(
            AutomationEvent.objects.select_for_update(skip_locked=True)
            .filter(dispatched_at__isnull=True)
            .order_by("occurred_at")[:200]
        )
        for event in events:
            for rule in get_active_rules_for_trigger(trigger_type=event.event_type):
                execution = create_execution_for_event(rule=rule, event=event)
                if execution is not None:
                    transaction.on_commit(
                        lambda execution_id=execution.id, org_id=str(organization.id): run_execution_task.delay(
                            str(execution_id), org_id
                        )
                    )
            event.dispatched_at = timezone.now()
            event.save(update_fields=["dispatched_at"])
            dispatched += 1
    return dispatched


@shared_task
def dispatch_automation_events_task():
    """Outbox dispatcher (phase sections 12, 85): claims undispatched
    AutomationEvent rows per organization and turns each into zero or more
    AutomationExecution rows for every currently-ACTIVE matching rule.
    `SELECT ... FOR UPDATE SKIP LOCKED` means two concurrent dispatcher
    workers never claim the same event row."""
    from accounts.models import Organization

    total = 0
    for organization in Organization.objects.filter(is_active=True):
        with tenant_context(organization_id=organization.id):
            total += _dispatch_events_for_org(organization)
    logger.info("automation_events_dispatched", extra={"dispatched": total})
    return {"dispatched": total}


def _run_due_schedules_for_org(organization, as_of) -> int:
    from automation.models.schedule import AutomationSchedule
    from automation.services.scheduling import claim_due_schedule_occurrence

    fired = 0
    for schedule in AutomationSchedule.objects.select_related("rule").filter(next_run_at__lte=as_of):
        rule = schedule.rule
        if rule.status != RuleStatus.ACTIVE:
            continue
        execution = AutomationExecution.objects.create(
            organization=organization, rule=rule, rule_version=rule.version, trigger_source=TriggerSource.SCHEDULE,
            conditions_snapshot=[{"field": c.field, "operator": c.operator, "value": c.value} for c in rule.conditions.all()],
            actions_snapshot=[{"action_id": a.action_id, "config": a.config} for a in rule.actions.all()],
            executed_as=rule.created_by,
        )
        occurrence = claim_due_schedule_occurrence(schedule=schedule, as_of=as_of, execution=execution)
        if occurrence is None:
            # Another scheduler already claimed this occurrence — discard
            # our speculative execution row rather than running it.
            execution.delete()
            continue
        transaction.on_commit(
            lambda execution_id=execution.id, org_id=str(organization.id): run_execution_task.delay(str(execution_id), org_id)
        )
        fired += 1
    return fired


@shared_task
def run_due_schedules_task():
    """Pure schedule triggers (schedule.daily/weekly/monthly — phase section
    42). Missed-schedule policy is SKIP_MISSED (services/scheduling.py)."""
    from accounts.models import Organization

    as_of = timezone.now().date()
    total = 0
    for organization in Organization.objects.filter(is_active=True):
        with tenant_context(organization_id=organization.id):
            total += _run_due_schedules_for_org(organization, as_of)
    logger.info("automation_schedules_fired", extra={"fired": total, "as_of": str(as_of)})
    return {"fired": total}


def _run_due_scans_for_org(organization, as_of) -> int:
    from automation.services.scheduling import (
        SCAN_TRIGGER_TYPES,
        claim_scan_occurrence,
        is_scan_entity_in_cooldown,
        scan_candidate_entity_ids,
    )

    fired = 0
    for trigger_type in SCAN_TRIGGER_TYPES:
        entity_ids = scan_candidate_entity_ids(trigger_type=trigger_type, organization=organization)
        if not entity_ids:
            continue
        for rule in get_active_rules_for_trigger(trigger_type=trigger_type):
            for entity_id in entity_ids:
                if is_scan_entity_in_cooldown(rule=rule, entity_id=entity_id, as_of=as_of):
                    continue
                execution = AutomationExecution.objects.create(
                    organization=organization, rule=rule, rule_version=rule.version,
                    trigger_source=TriggerSource.SCHEDULE, entity_id=entity_id,
                    conditions_snapshot=[
                        {"field": c.field, "operator": c.operator, "value": c.value} for c in rule.conditions.all()
                    ],
                    actions_snapshot=[{"action_id": a.action_id, "config": a.config} for a in rule.actions.all()],
                    executed_as=rule.created_by,
                )
                occurrence = claim_scan_occurrence(rule=rule, entity_id=entity_id, as_of=as_of, execution=execution)
                if occurrence is None:
                    execution.delete()
                    continue
                transaction.on_commit(
                    lambda execution_id=execution.id, org_id=str(organization.id): run_execution_task.delay(
                        str(execution_id), org_id
                    )
                )
                fired += 1
    return fired


@shared_task
def run_due_scans_task():
    """Scan-based triggers (invoice.overdue, stock.low — phase sections
    46-48): these describe a STATE, not a one-shot occurrence, so this scans
    EXISTING selectors and lets AutomationScanOccurrence + cooldown_days
    prevent re-firing every scan for an entity that remains matched."""
    from accounts.models import Organization

    as_of = timezone.now().date()
    total = 0
    for organization in Organization.objects.filter(is_active=True):
        with tenant_context(organization_id=organization.id):
            total += _run_due_scans_for_org(organization, as_of)
    logger.info("automation_scans_fired", extra={"fired": total, "as_of": str(as_of)})
    return {"fired": total}


# Recovery sweeper thresholds. PENDING must wait out any healthy enqueue ->
# pickup delay; RUNNING must outlast run_execution_task's hard time limit plus
# its longest retry countdown (retry_backoff_max), so a retry that is merely
# waiting is never raced. RECOVERY_MAX_ATTEMPTS bounds recovery: past it the
# execution is failed visibly (human-retryable) instead of re-enqueued forever.
# It counts AutomationExecution.recovery_attempts — incremented here, in this
# sweeper's own transaction — and never attempt_count, which the run increments
# inside its own transaction and therefore loses whenever a run rolls back.
RECOVERY_PENDING_AFTER = datetime.timedelta(minutes=10)
RECOVERY_RUNNING_AFTER = datetime.timedelta(minutes=90)
RECOVERY_MAX_ATTEMPTS = 10
RECOVERY_BATCH_SIZE = 200


def _recover_stalled_executions_for_org(organization, now) -> tuple[int, int]:
    recovered = abandoned = 0
    stalled = (
        AutomationExecution.objects.select_for_update(skip_locked=True)
        .filter(
            Q(status=ExecutionStatus.PENDING, updated_at__lt=now - RECOVERY_PENDING_AFTER)
            | Q(status=ExecutionStatus.RUNNING, updated_at__lt=now - RECOVERY_RUNNING_AFTER)
        )
        .order_by("updated_at")[:RECOVERY_BATCH_SIZE]
    )
    for execution in stalled:
        if execution.recovery_attempts >= RECOVERY_MAX_ATTEMPTS:
            execution.status = ExecutionStatus.FAILED
            execution.finished_at = now
            execution.error_summary = (
                f"Stopped by automatic recovery after {execution.recovery_attempts} attempts without finishing. "
                "Retry it once the cause is fixed."
            )
            execution.save(update_fields=["status", "finished_at", "error_summary", "updated_at"])
            logger.warning(
                "automation_execution_recovery_abandoned",
                extra={"execution_id": str(execution.id), "recovery_attempts": execution.recovery_attempts},
            )
            abandoned += 1
            continue
        # Spend one unit of budget and touch updated_at, both committed with
        # this sweep: the budget must not depend on what the run does, and the
        # timestamp keeps the next sweep from re-enqueuing a message that is
        # still on its way to a worker.
        AutomationExecution.objects.filter(pk=execution.pk).update(
            recovery_attempts=F("recovery_attempts") + 1, updated_at=now
        )
        transaction.on_commit(
            lambda execution_id=execution.id, org_id=str(organization.id): run_execution_task.delay(
                str(execution_id), org_id
            )
        )
        logger.info(
            "automation_execution_recovery_requeued",
            extra={
                "execution_id": str(execution.id), "status": execution.status,
                "recovery_attempts": execution.recovery_attempts + 1,
            },
        )
        recovered += 1
    return recovered, abandoned


@shared_task
def recover_stalled_executions_task():
    """Re-enqueues executions no worker will otherwise ever run: a lost
    enqueue leaves them PENDING, a worker killed mid-run leaves them RUNNING.
    Safe because run_execution never repeats a SUCCEEDED step."""
    from accounts.models import Organization

    now = timezone.now()
    totals = {"recovered": 0, "abandoned": 0}
    for organization in Organization.objects.filter(is_active=True):
        with tenant_context(organization_id=organization.id):
            recovered, abandoned = _recover_stalled_executions_for_org(organization, now)
        totals["recovered"] += recovered
        totals["abandoned"] += abandoned
    logger.info("automation_executions_recovery_swept", extra=totals)
    return totals
