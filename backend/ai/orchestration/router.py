"""Intent routing: a structured model classification plus deterministic
guardrails (phase section 25).

The model handles phrasing no keyword list anticipates; the guardrails
enforce the non-negotiable rule regardless of what the model says:
QUESTIONS ABOUT NUMBERS ALWAYS GET STRUCTURED TOOLS — a figure is never
answered from documents alone. A routing-model failure is not fatal: the
deterministic signals take over.
"""

import re
from dataclasses import dataclass

from ai.providers.base import LLMRequest, Message, Usage
from ai.providers.errors import AIProviderError

STRUCTURED, RAG, BOTH, CONVERSATIONAL = "structured", "rag", "both", "conversational"
INTENTS = (STRUCTURED, RAG, BOTH, CONVERSATIONAL)

ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": list(INTENTS)},
        "reason": {"type": "string", "maxLength": 200},
    },
    "required": ["intent"],
    "additionalProperties": False,
}

_FINANCIAL = re.compile(
    r"\b(profit|loss|p\s*&\s*l|revenue|income|sales|sold|expenses?|costs?|cash|bank|balance|balances|ledger|"
    r"owe|owes|owed|overdue|outstanding|receivables?|payables?|ageing|aging|gst|igst|cgst|sgst|cess|tax|taxes|tds|tcs|"
    r"itc|gstr|stock|inventory|valuation|margin|invoices?|bills?|payments?|paid|unpaid|how much|how many|total|"
    r"amount|spend|spent|earn|earned|financial|performance)\b",
    re.IGNORECASE,
)
_DOCUMENT = re.compile(
    r"\b(documents?|contracts?|agreements?|uploaded|attachments?|files?|clauses?|terms|polic(y|ies)|letters?|"
    r"say|says|said|mention|mentions|summari[sz]e|termination|terminate|cancellation|warranty|lease|renewal|"
    r"notice period|obligations?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class RouteDecision:
    intent: str
    decided_by: str  # "model" | "guardrail" | "fallback"
    usage: Usage | None = None


def deterministic_signals(question: str) -> tuple[bool, bool]:
    return bool(_FINANCIAL.search(question)), bool(_DOCUMENT.search(question))


def _from_signals(financial: bool, document: bool) -> str:
    if financial and document:
        return BOTH
    if document:
        return RAG
    if financial:
        return STRUCTURED
    return CONVERSATIONAL


def apply_guardrails(model_intent: str | None, *, question: str, has_document_scope: bool) -> tuple[str, str]:
    financial, document = deterministic_signals(question)
    if model_intent not in INTENTS:
        intent, decided_by = _from_signals(financial, document or has_document_scope), "fallback"
    else:
        intent, decided_by = model_intent, "model"

    if financial and intent == RAG:
        intent, decided_by = BOTH, "guardrail"
    elif financial and intent == CONVERSATIONAL:
        intent, decided_by = (BOTH if document else STRUCTURED), "guardrail"
    elif document and intent == CONVERSATIONAL:
        # A question about document content must be checked against the
        # documents, so "nothing found" is an honest answer, not a chat reply.
        intent, decided_by = RAG, "guardrail"
    if has_document_scope and intent == STRUCTURED:
        intent, decided_by = BOTH, "guardrail"
    elif has_document_scope and intent == CONVERSATIONAL:
        intent, decided_by = RAG, "guardrail"
    return intent, decided_by


def route(*, question: str, has_document_scope: bool, call_model, system: str, config) -> RouteDecision:
    """`call_model(request) -> LLMResponse` applies timeout/retry policy."""
    request = LLMRequest(
        system=system,
        messages=(Message(role="user", text=question),),
        purpose="route",
        max_output_tokens=100,
        temperature=0.0,
        timeout_seconds=config.llm_timeout_seconds,
        response_schema=ROUTE_SCHEMA,
    )
    model_intent, usage = None, None
    try:
        response = call_model(request)
        usage = response.usage
        if isinstance(response.structured, dict):
            model_intent = response.structured.get("intent")
    except AIProviderError:
        model_intent = None
    intent, decided_by = apply_guardrails(model_intent, question=question, has_document_scope=has_document_scope)
    return RouteDecision(intent=intent, decided_by=decided_by, usage=usage)
