"""Action execution handlers (phase sections 20, 32-38). Each handler is a
thin, validated call into an EXISTING domain selector/service — automation
never mutates another module's data directly (root CLAUDE.md), and every
handler here is Level 1 (notification/report/AI draft/webhook): nothing
posts accounting, moves stock, or sends money.

`run_action` is the single dispatch point automation/services/execution.py
calls. A handler raises `AutomationTransientError` for a retryable failure
(phase section 28); anything else (including a domain `ApplicationError`)
is treated as permanent.
"""

# Re-exported here for backward-compat call sites/tests that import it from
# this module; the class itself lives in errors.py so webhook_sender.py can
# raise it without importing this (heavier) module.
from automation.actions.errors import AutomationTransientError
from core.exceptions import ApplicationError


def _send_notification(*, config, execution, executed_as, step=None):
    from automation.models.notification import AutomationNotification

    recipient_id = config.get("recipient_id") or str(executed_as.id)
    notification = AutomationNotification.objects.create(
        organization=execution.organization,
        recipient_id=recipient_id,
        message=config["message"],
        source_execution=execution,
    )
    return {"notification_id": str(notification.id)}


def _overdue_invoices_report(*, organization):
    from sales.selectors import get_overdue_invoices

    invoices = get_overdue_invoices(organization=organization)
    return {
        "count": len(invoices),
        "invoice_ids": [str(invoice.id) for invoice in invoices],
    }


def _low_stock_items_report(*, organization):
    from inventory.selectors import get_low_stock_items

    rows = get_low_stock_items(organization=organization)
    return {
        "count": len(rows),
        "items": [
            {"item_id": str(row["item"].id), "on_hand": str(row["on_hand"]), "reorder_level": str(row["reorder_level"])}
            for row in rows
        ],
    }


_REPORT_BUILDERS = {
    "overdue_invoices": _overdue_invoices_report,
    "low_stock_items": _low_stock_items_report,
}


def _generate_report(*, config, execution, executed_as, step=None):
    report_type = config["report_type"]
    builder = _REPORT_BUILDERS.get(report_type)
    if builder is None:
        raise ApplicationError(f"Unknown report_type '{report_type}'.", code="automation_report_type_unknown")
    return {"report_type": report_type, "summary": builder(organization=execution.organization)}


def _draft_payment_reminder(*, config, execution, executed_as, step=None):
    from ai.orchestration.assist import draft_payment_reminder
    from ai.providers.errors import AIProviderError

    try:
        draft = draft_payment_reminder(
            user=executed_as,
            organization=execution.organization,
            invoice_id=execution.entity_id,
            request_id=str(execution.id),
        )
    except AIProviderError as exc:
        raise AutomationTransientError(str(exc)) from exc
    return {"draft": draft}


def _call_webhook(*, config, execution, executed_as, step=None):
    import hashlib
    import hmac
    import json
    import time

    from django.utils import timezone

    from automation.actions import webhook_sender
    from automation.actions.webhook_security import validate_webhook_url

    url = config["url"]
    secret = config.get("secret", "")
    # Re-validated at execution time, not just at rule save time (phase
    # section 41) — a resolved IP can change between the two.
    validate_webhook_url(url)

    # Stable across retries: idempotency_key never changes for this step
    # (phase section 83's "delivery_id remains constant across retry").
    delivery_id = step.idempotency_key if step is not None else str(execution.id)
    payload = {
        "event": execution.rule.trigger_type,
        "entity_id": execution.entity_id,
        "occurred_at": timezone.now().isoformat(),
        "delivery_id": delivery_id,
    }
    body = json.dumps(payload, sort_keys=True).encode()
    timestamp = str(int(time.time()))
    signature = (
        hmac.new(secret.encode(), f"{timestamp}.{body.decode()}".encode(), hashlib.sha256).hexdigest()
        if secret
        else ""
    )
    headers = {
        "Content-Type": "application/json",
        "X-Automation-Delivery-Id": delivery_id,
        "X-Automation-Timestamp": timestamp,
        "X-Automation-Signature": signature,
    }

    sender = webhook_sender.get_sender()
    response = sender(url=url, payload=body, headers=headers, timeout=webhook_sender.DEFAULT_TIMEOUT_SECONDS)
    return {"delivery_id": delivery_id, "http_status": response.http_status, "latency_ms": response.latency_ms}


_HANDLERS = {
    "send_notification": _send_notification,
    "generate_report": _generate_report,
    "draft_payment_reminder": _draft_payment_reminder,
    "call_webhook": _call_webhook,
}


def run_action(*, action_id: str, config: dict, execution, executed_as, step=None):
    handler = _HANDLERS.get(action_id)
    if handler is None:
        raise ApplicationError(f"No handler registered for action '{action_id}'.", code="automation_action_unhandled")
    return handler(config=config, execution=execution, executed_as=executed_as, step=step)
