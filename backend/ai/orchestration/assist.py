"""Advisory features (phase sections 53/55). Both are strictly read-only:

* Payment reminder drafting returns text. It never sends, schedules or
  records anything — sending belongs to a future, explicitly authorized
  automation workflow.
* Expense-account suggestion returns an account id chosen from an allowlist
  of THIS organization's active expense accounts. A model answer outside the
  allowlist is discarded. Nothing is posted or modified; applying the
  suggestion is the user's action through the normal purchases workflow.

Reconciliation suggestions (phase section 54) are deliberately NOT built:
see ai/CLAUDE.md "DEFERRED".
"""

import json
import time

from ai.orchestration import guards, limits
from ai.orchestration.errors import AskBooksError, from_provider_error
from ai.orchestration.service import AskBooksService
from ai.orchestration.telemetry import Telemetry
from ai.prompts import ALL_PROMPTS, DRAFT_INSTRUCTIONS, PROMPT_VERSION, SUGGEST_INSTRUCTIONS
from ai.providers.base import Message, UntrustedDocument
from ai.providers.errors import AIProviderError, ProviderMalformedOutput
from ai.tools.base import ToolContext
from ai.tools.executor import execute_tool
from authz.roles import Permission, role_has_permission

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string", "maxLength": 200}, "body": {"type": "string", "maxLength": 4000}},
    "required": ["subject", "body"],
    "additionalProperties": False,
}
SUGGEST_SCHEMA = {
    "type": "object",
    "properties": {
        "account_id": {"type": ["string", "null"]},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "reasoning": {"type": "string", "maxLength": 300},
    },
    "required": ["account_id", "confidence", "reasoning"],
    "additionalProperties": False,
}
MAX_ALLOWED_ACCOUNTS = 200


class _AssistRun(AskBooksService):
    def begin(self, feature, user, organization, request_id):
        self.telemetry = Telemetry(feature=feature, provider=self.provider.name, model=self.provider.model, prompt_version=PROMPT_VERSION)
        self.deadline = time.monotonic() + self.config.request_deadline_seconds
        membership = self._authorize(user, organization)
        limits.check_and_consume(user=user, organization=organization, config=self.config)
        return membership

    def run(self, feature, user, organization, request_id, body):
        try:
            membership = self.begin(feature, user, organization, request_id)
            result = body(membership)
        except AIProviderError as exc:
            error = from_provider_error(exc)
            self._record(organization, user, request_id, "error", error.code, None)
            raise error
        except AskBooksError as exc:
            if exc.code not in ("ai_disabled", "forbidden", "organization_required"):
                self._record(organization, user, request_id, "rate_limited" if exc.status_code == 429 else "error", exc.code, None)
            raise
        self._record(organization, user, request_id, "ok", "", None)
        return result


def draft_payment_reminder(*, user, organization, invoice_id, request_id: str = "") -> dict:
    run = _AssistRun()

    def body(membership):
        ctx = ToolContext(user=user, organization=organization, request_id=request_id, today=run.today)
        invoice = execute_tool(ctx, "get_invoice", {"invoice_id": str(invoice_id)})
        run.telemetry.add_tool("get_invoice")
        if invoice.status == "not_found":
            raise AskBooksError("invoice_not_found", "Invoice not found.", 404)
        if invoice.status != "ok":
            raise AskBooksError(invoice.error_code or "tool_error", invoice.message or "Invoice unavailable.",
                                403 if invoice.error_code == "forbidden" else 400)
        facts = invoice.to_payload()["summary"]
        from decimal import Decimal

        if facts["status"] in ("draft", "void") or Decimal(facts["amount_due"]) <= 0:
            raise AskBooksError("invoice_not_outstanding", "This invoice has no outstanding amount to remind about.", 400)
        # JSON-normalized (dates -> ISO strings) so the facts shown to the model,
        # returned to the client and used for grounding are the same text.
        allowed = json.loads(json.dumps(
            {k: facts[k] for k in ("customer_name", "invoice_number", "invoice_date", "due_date", "currency", "amount_due")},
            default=str,
        ))

        request = run._request(
            instructions=DRAFT_INSTRUCTIONS, purpose="draft", response_schema=DRAFT_SCHEMA, metadata={"facts": allowed},
            messages=[Message(role="user", text=f"Invoice facts (authoritative): {json.dumps(allowed, sort_keys=True)}")],
        )
        output = run._call(request, "structured").structured
        subject, text = output.get("subject"), output.get("body")
        if not isinstance(subject, str) or not isinstance(text, str):
            raise ProviderMalformedOutput("draft output did not match schema")
        subject, text = guards.sanitize_markdown(subject)[:200], guards.sanitize_markdown(text)[:4000]
        fact_text = json.dumps(allowed)
        if guards.leaks_prompt(text, list(ALL_PROMPTS.values())) or guards.ungrounded_numbers(f"{subject}\n{text}", [fact_text]):
            # A reminder quoting a wrong amount is worse than no draft.
            raise AskBooksError("draft_rejected", "The generated draft could not be verified against the invoice. Please try again.", 422)
        return {
            "subject": subject,
            "body": text,
            "status": "draft",
            "sent": False,
            "facts": allowed,
            "sources": [source.to_public() for source in invoice.sources],
            "request_id": request_id,
        }

    return run.run("draft_payment_reminder", user, organization, request_id, body)


def suggest_expense_account(*, user, organization, description: str, vendor_name: str = "", request_id: str = "") -> dict:
    run = _AssistRun()

    def body(membership):
        if not role_has_permission(membership.role, Permission.VIEW_ACCOUNTING):
            raise AskBooksError("forbidden", "You do not have permission to view the chart of accounts.", 403)
        from accounting.models.account import Account, AccountType

        accounts = list(
            Account.objects.filter(account_type=AccountType.EXPENSE, is_active=True)
            .order_by("code")
            .values("id", "code", "name")[:MAX_ALLOWED_ACCOUNTS]
        )
        if not accounts:
            raise AskBooksError("no_expense_accounts", "No active expense accounts exist to suggest from.", 400)
        allowlist = [{"id": str(a["id"]), "code": a["code"], "name": a["name"]} for a in accounts]
        by_id = {account["id"]: account for account in allowlist}

        request = run._request(
            instructions=SUGGEST_INSTRUCTIONS, purpose="suggest", response_schema=SUGGEST_SCHEMA,
            metadata={"allowed_accounts": allowlist, "description": description},
            messages=[Message(
                role="user",
                text=f"Allowed accounts: {json.dumps(allowlist)}",
                documents=(UntrustedDocument(source_id="expense_description", label="Expense description",
                                             text=f"{description}\nVendor: {vendor_name}"[:2000]),),
            )],
        )
        output = run._call(request, "structured").structured
        account_id = output.get("account_id")
        confidence = output.get("confidence") if output.get("confidence") in ("low", "medium", "high") else "low"
        reasoning = guards.sanitize_markdown(str(output.get("reasoning") or ""))[:300]
        suggestion = by_id.get(account_id) if isinstance(account_id, str) else None
        if account_id is not None and suggestion is None:
            # Outside the allowlist (hallucinated or another tenant's id).
            confidence, reasoning = "low", "No suitable account could be verified."
        return {
            "suggested_account": suggestion,
            "confidence": confidence if suggestion else "low",
            "reasoning": reasoning,
            "applied": False,
            "request_id": request_id,
        }

    return run.run("suggest_expense_account", user, organization, request_id, body)

