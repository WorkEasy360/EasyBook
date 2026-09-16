"""Execution engine (phase sections 26-31, 67): evaluate a rule's snapshotted
conditions against freshly reloaded facts and, if they pass, run its
snapshotted actions in order. A later edit to the rule's live
conditions/actions can never change how a past execution is interpreted —
everything the engine reads from here on is the snapshot taken at
`create_execution_for_event` time, not a live FK.
"""

import datetime
import hashlib
import logging
from types import SimpleNamespace

from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import Membership
from authz.roles import Permission, role_has_permission
from automation.actions.errors import AutomationTransientError
from automation.actions.handlers import run_action as _run_action_default
from automation.actions.registry import get_action
from automation.conditions.evaluator import evaluate_conditions
from automation.models.execution import AutomationExecution, ExecutionStatus, TriggerSource
from automation.models.step_execution import RETRYABLE_CATEGORIES, AutomationStepExecution, FailureCategory, StepStatus
from automation.triggers.facts import build_facts
from core.exceptions import ApplicationError

logger = logging.getLogger("automation.execution")

# Which existing permission gates an action's DATA access, mirroring
# ai/tools/registry.py's "every tool declares the permissions it needs".
# None means "any active member of the organization" (send_notification is
# Level 1 and creates no cross-user data exposure beyond the message text
# the rule's own author configured).
ACTION_PERMISSIONS: dict[str, str | None] = {
    "send_notification": None,
    "generate_report": Permission.VIEW_REPORTS,
    "draft_payment_reminder": Permission.USE_AI_ASSISTANT,
    "call_webhook": Permission.MANAGE_AUTOMATION_WEBHOOKS,
}


def _step_idempotency_key(*, execution_id, action_id: str, order: int) -> str:
    return hashlib.sha256(f"{execution_id}:{action_id}:{order}".encode()).hexdigest()


RATE_LIMIT_PERIOD = datetime.timedelta(days=1)


def _rate_limited(rule) -> bool:
    """phase section 52: `max_runs_per_period` bounds executions/rule over a
    rolling 24h window — avoids a misconfigured or noisy trigger running up
    an unbounded number of notifications/webhook calls/AI drafts."""
    if not rule.max_runs_per_period:
        return False
    cutoff = timezone.now() - RATE_LIMIT_PERIOD
    return rule.executions.filter(created_at__gte=cutoff).count() >= rule.max_runs_per_period


def create_execution_for_event(*, rule, event) -> AutomationExecution | None:
    """Creates the (at most one) AutomationExecution for this (rule, event)
    pair. Returns None if one already exists — the DB UniqueConstraint on
    (rule, trigger_event) is the authoritative dedup guard (phase section
    11): two dispatcher workers racing the same event can both attempt the
    INSERT, but only one succeeds, and this treats the loser as a no-op
    rather than an error. Also returns None (no error — this is routine
    throttling, not a failure) if the rule has hit its configured
    `max_runs_per_period`."""
    if _rate_limited(rule):
        logger.warning(
            "automation_rule_rate_limited", extra={"rule_id": str(rule.id), "max_runs_per_period": rule.max_runs_per_period}
        )
        return None
    try:
        with transaction.atomic():
            return AutomationExecution.objects.create(
                organization=rule.organization,
                rule=rule,
                rule_version=rule.version,
                trigger_source=TriggerSource.EVENT,
                trigger_event=event,
                entity_id=event.entity_id,
                conditions_snapshot=[
                    {"field": c.field, "operator": c.operator, "value": c.value} for c in rule.conditions.all()
                ],
                actions_snapshot=[{"action_id": a.action_id, "config": a.config} for a in rule.actions.all()],
                causation_id=event.causation_id,
                correlation_id=event.correlation_id,
                depth=event.depth,
                executed_as=rule.created_by,
            )
    except IntegrityError:
        return None


def create_manual_execution(*, rule, actor) -> AutomationExecution:
    """A human explicitly running a rule now (phase section 8: manual
    trigger) — no event-level dedup (there is no repeated occurrence to
    dedupe against), but still subject to `max_runs_per_period`: unlike the
    event path's silent no-op, a manual run raises so the human gets clear
    feedback rather than a request that appears to do nothing."""
    if _rate_limited(rule):
        raise ApplicationError(
            f"This rule has already run {rule.max_runs_per_period} times in the last 24 hours.",
            code="automation_rule_rate_limited",
        )
    return AutomationExecution.objects.create(
        organization=rule.organization,
        rule=rule,
        rule_version=rule.version,
        trigger_source=TriggerSource.MANUAL,
        conditions_snapshot=[
            {"field": c.field, "operator": c.operator, "value": c.value} for c in rule.conditions.all()
        ],
        actions_snapshot=[{"action_id": a.action_id, "config": a.config} for a in rule.actions.all()],
        initiated_by=actor,
        executed_as=rule.created_by,
    )


STEP_MAX_ATTEMPTS = 5


def _fail_step(step: AutomationStepExecution, *, category: str, message: str) -> bool:
    """Terminal FAILED for a non-retryable category or an exhausted retry
    budget (phase section 28-29); otherwise RETRYING, which `run_execution`
    treats as still-pending-work so a later call (the bounded Celery
    autoretry in tasks.py) attempts it again. Only `retry_execution`
    (explicit human action) may bring a terminal FAILED step back."""
    terminal = category not in RETRYABLE_CATEGORIES or step.attempt >= STEP_MAX_ATTEMPTS
    step.status = StepStatus.FAILED if terminal else StepStatus.RETRYING
    step.failure_category = category
    step.error_message = message[:2000]
    step.finished_at = timezone.now() if terminal else None
    step.save(update_fields=["status", "failure_category", "error_message", "finished_at", "updated_at"])
    # Structured, never the message body/secrets (phase section 77) — just
    # correlation identifiers, the action, and why it failed.
    logger.warning(
        "automation_step_failed",
        extra={
            "execution_id": str(step.execution_id), "step_id": str(step.id), "action_id": step.action_id,
            "failure_category": category, "attempt": step.attempt, "terminal": terminal,
        },
    )
    return False


def _execute_step(*, step: AutomationStepExecution, execution: AutomationExecution, run_action) -> bool:
    step.status = StepStatus.RUNNING
    step.started_at = step.started_at or timezone.now()
    step.attempt += 1
    step.save(update_fields=["status", "started_at", "attempt", "updated_at"])

    action_def = get_action(step.action_id)
    if action_def is None:
        return _fail_step(step, category=FailureCategory.VALIDATION_ERROR, message=f"Unknown action '{step.action_id}'.")

    executed_as = execution.executed_as
    if executed_as is None:
        return _fail_step(
            step, category=FailureCategory.PERMISSION_ERROR, message="This rule has no executing user (created_by is unset)."
        )

    membership = Membership.all_objects.filter(
        organization_id=execution.organization_id, user_id=executed_as.id, is_active=True
    ).first()
    if membership is None:
        return _fail_step(
            step, category=FailureCategory.PERMISSION_ERROR, message="The rule's executing user has no active membership."
        )
    required = ACTION_PERMISSIONS.get(step.action_id)
    if required and not role_has_permission(membership.role, required):
        return _fail_step(
            step, category=FailureCategory.PERMISSION_ERROR,
            message="The rule's executing user no longer holds the permission this action requires.",
        )

    try:
        result = run_action(
            action_id=step.action_id, config=step.config_snapshot, execution=execution, executed_as=executed_as,
            step=step,
        )
    except AutomationTransientError as exc:
        return _fail_step(step, category=FailureCategory.TRANSIENT_EXTERNAL_ERROR, message=str(exc))
    except ApplicationError as exc:
        codes = exc.get_codes()
        code = codes if isinstance(codes, str) else "domain_conflict"
        category = FailureCategory.TARGET_MISSING if code == "not_found" else FailureCategory.DOMAIN_CONFLICT
        return _fail_step(step, category=category, message=str(exc.detail))
    except Exception:  # pragma: no cover - defensive: a handler bug must never crash the engine
        return _fail_step(step, category=FailureCategory.UNEXPECTED_ERROR, message="The action failed unexpectedly.")

    step.status = StepStatus.SUCCEEDED
    step.result = result or {}
    step.finished_at = timezone.now()
    step.save(update_fields=["status", "result", "finished_at", "updated_at"])
    return True


def run_execution(execution: AutomationExecution, *, run_action=None) -> AutomationExecution:
    """Evaluates conditions and, if they pass, runs every snapshotted action
    in order. Safe to call more than once for the SAME execution (a Celery
    retry after a crash): a step already SUCCEEDED is never re-run, thanks
    to `get_or_create` on `idempotency_key` plus the SUCCEEDED short-circuit
    below (phase section 82-84 crash recovery)."""
    if execution.status == ExecutionStatus.SUCCEEDED or execution.status == ExecutionStatus.CANCELLED:
        return execution

    execution.status = ExecutionStatus.RUNNING
    execution.started_at = execution.started_at or timezone.now()
    execution.attempt_count += 1
    execution.save(update_fields=["status", "started_at", "attempt_count", "updated_at"])

    facts = build_facts(trigger_type=execution.rule.trigger_type, organization=execution.organization, entity_id=execution.entity_id)
    conditions = [SimpleNamespace(**c) for c in execution.conditions_snapshot]
    matched = evaluate_conditions(trigger_type=execution.rule.trigger_type, conditions=conditions, facts=facts)

    if not matched:
        execution.status = ExecutionStatus.SUCCEEDED
        execution.finished_at = timezone.now()
        execution.save(update_fields=["status", "finished_at", "updated_at"])
        return execution

    run_action = run_action or _run_action_default
    for order, action in enumerate(execution.actions_snapshot):
        step, _created = AutomationStepExecution.objects.get_or_create(
            organization=execution.organization,
            execution=execution,
            order=order,
            defaults={
                "action_id": action["action_id"],
                "config_snapshot": action.get("config") or {},
                "idempotency_key": _step_idempotency_key(execution_id=execution.id, action_id=action["action_id"], order=order),
            },
        )
        if step.status in (StepStatus.SUCCEEDED, StepStatus.FAILED):
            continue  # terminal — a non-retryable failure never retries itself
        if not _execute_step(step=step, execution=execution, run_action=run_action):
            if execution.rule.stop_on_failure:
                break

    statuses = set(execution.steps.values_list("status", flat=True))
    if StepStatus.RETRYING in statuses:
        execution.status = ExecutionStatus.RUNNING
    elif statuses <= {StepStatus.SUCCEEDED}:
        execution.status = ExecutionStatus.SUCCEEDED
    elif StepStatus.SUCCEEDED in statuses:
        execution.status = ExecutionStatus.PARTIAL
    else:
        execution.status = ExecutionStatus.FAILED
    execution.finished_at = None if execution.status == ExecutionStatus.RUNNING else timezone.now()
    execution.save(update_fields=["status", "finished_at", "updated_at"])
    return execution


def retryable_step_ids(execution: AutomationExecution):
    """Steps the bounded Celery autoretry (tasks.py) should wait for —
    RETRYING only; a terminal FAILED step never auto-retries."""
    return execution.steps.filter(status=StepStatus.RETRYING).values_list("id", flat=True)


def retry_execution(execution: AutomationExecution, *, actor) -> AutomationExecution:
    """Human-initiated retry (phase section 31): resets every terminal FAILED
    step back to PENDING with a fresh attempt budget, including categories
    the automatic retry would never touch (e.g. permission_error, once an
    operator has actually fixed the permission) — `run_execution`'s own
    per-step idempotency (get_or_create on idempotency_key, SUCCEEDED
    short-circuit) still guarantees this can never double the effect of a
    step that actually succeeded."""
    if execution.status not in (ExecutionStatus.FAILED, ExecutionStatus.PARTIAL):
        raise ApplicationError(
            "Only a failed or partially-failed execution can be retried.", code="automation_execution_not_retryable"
        )
    execution.steps.filter(status=StepStatus.FAILED).update(
        status=StepStatus.PENDING, failure_category="", error_message="", attempt=0, next_retry_at=None,
    )
    execution.status = ExecutionStatus.PENDING
    execution.save(update_fields=["status", "updated_at"])
    return execution
