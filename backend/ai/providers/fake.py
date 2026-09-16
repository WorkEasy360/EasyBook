"""Deterministic fake LLM provider — a TEST DOUBLE, not an AI.

It exists so the full automated suite (and local development) runs with no
external credentials and no paid API calls (phase section 75). It never
"knows" anything: it plans tool calls with a fixed keyword table, and its
"answers" only restate figures that already sit in backend tool results or
quote retrieved text, always citing the source ids it was given.

Tests can replace its behaviour entirely with `responder=` (a callable
taking the LLMRequest) to script malicious or malformed model output.
`ai/config.py::validate_ai_configuration` refuses to boot with this provider
when Ask Books is enabled without AI_ALLOW_FAKE_PROVIDERS.
"""

import json
import re

from ai.providers.base import LLMProvider, LLMRequest, LLMResponse, ToolCall, Usage

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _request_text(request: LLMRequest) -> str:
    parts = [request.system]
    for message in request.messages:
        parts.append(message.text)
        parts.extend(result.content for result in message.tool_results)
        parts.extend(document.text for document in message.documents)
    return "\n".join(parts)


def _last_user_question(request: LLMRequest) -> str:
    for message in reversed(request.messages):
        if message.role == "user" and message.text:
            return message.text
    return ""


def _count_in(question: str, default: int) -> int:
    match = re.search(r"\b(\d{1,3})\b", question)
    if match:
        return int(match.group(1))
    for word, value in _NUMBER_WORDS.items():
        if re.search(rf"\b{word}\b", question):
            return value
    return default


class FakeLLMProvider(LLMProvider):
    name = "fake"

    def __init__(self, *, model: str = "fake-llm-1", responder=None):
        super().__init__(model=model)
        self.responder = responder
        self.requests: list[LLMRequest] = []

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self.responder is not None:
            response = self.responder(request)
        elif request.purpose == "route":
            response = LLMResponse(structured=self._route(request))
        elif request.purpose == "tool_planning":
            response = self._plan_tools(request)
        elif request.purpose == "synthesis":
            response = LLMResponse(structured=self._synthesize(request))
        elif request.purpose == "draft":
            response = LLMResponse(structured=self._draft(request))
        elif request.purpose == "suggest":
            response = LLMResponse(structured=self._suggest(request))
        else:
            response = LLMResponse(text="")
        if response.usage.input_tokens is None:
            output = response.text or json.dumps(response.structured or {})
            response = LLMResponse(
                text=response.text, tool_calls=response.tool_calls, structured=response.structured,
                usage=Usage(input_tokens=_approx_tokens(_request_text(request)), output_tokens=_approx_tokens(output)),
                stop_reason=response.stop_reason, model=self.model,
            )
        return response

    # -- routing ---------------------------------------------------------
    def _route(self, request: LLMRequest) -> dict:
        question = _last_user_question(request).lower()
        numeric = bool(re.search(
            r"profit|revenue|cash|owe|overdue|gst|tax|stock|inventory|expense|balance|invoice|bill|sales|payable|receivable",
            question,
        ))
        document = bool(re.search(r"contract|document|uploaded|agreement|clause|says|policy|termination|summari[sz]e this", question))
        if numeric and document:
            intent = "both"
        elif document:
            intent = "rag"
        elif numeric:
            intent = "structured"
        else:
            intent = "conversational"
        return {"intent": intent, "reason": "fake keyword routing"}

    # -- tool planning ---------------------------------------------------
    def _plan_tools(self, request: LLMRequest) -> LLMResponse:
        if any(message.tool_results for message in request.messages):
            return LLMResponse(text="Tool results received.")
        offered = {tool.name for tool in request.tools}
        question = _last_user_question(request).lower()
        periods = request.metadata.get("periods", {})

        def period(name):
            value = periods.get(name) or {}
            return {"from_date": value.get("from_date"), "to_date": value.get("to_date")}

        chosen_period = "this_month"
        if "last quarter" in question:
            chosen_period = "last_quarter"
        elif "quarter" in question:
            chosen_period = "this_quarter"
        elif "last month" in question:
            chosen_period = "last_month"
        elif "year" in question:
            chosen_period = "this_year"

        calls: list[tuple[str, dict]] = []
        invoice_match = re.search(r"\b(inv[-/][a-z0-9-]+)", question)
        bill_match = re.search(r"\b(bill[-/][a-z0-9-]+)", question)
        if invoice_match:
            calls.append(("get_invoice", {"invoice_number": invoice_match.group(1).upper()}))
        elif bill_match:
            calls.append(("get_bill", {"bill_number": bill_match.group(1).upper()}))
        elif "gst" in question or "tax" in question:
            calls.append(("get_gst_summary", period(chosen_period)))
        elif "overdue" in question:
            days = _count_in(question, 0)
            calls.append(("get_overdue_invoices", {"min_days_overdue": days}))
        elif "owe us" in question or ("customer" in question and "owe" in question):
            calls.append(("get_customer_balances", {"limit": _count_in(question, 10), "order": "balance_desc"}))
        elif "owe" in question or "payable" in question or "vendor" in question:
            calls.append(("get_ap_ageing", {}))
        elif "low" in question and "stock" in question:
            calls.append(("get_low_stock", {}))
        elif "stock" in question or "inventory" in question:
            calls.append(("get_inventory_valuation", {}))
        elif "balance sheet" in question:
            calls.append(("get_balance_sheet", {"as_of_date": periods.get("today")}))
        elif "cash" in question:
            calls.append(("get_cash_flow", period(chosen_period)))
        elif "expense" in question:
            calls.append(("get_expenses_by_category", period(chosen_period)))
            calls.append(("get_expenses_by_category", period("last_month")))
        elif "compare" in question and "quarter" in question:
            current, previous = period("this_quarter"), period("last_quarter")
            calls.append(("get_profit_and_loss", {
                **current, "comparison_from": previous["from_date"], "comparison_to": previous["to_date"],
            }))
        elif re.search(r"profit|revenue|income|performance|p&l", question):
            calls.append(("get_profit_and_loss", period(chosen_period)))

        tool_calls = tuple(
            ToolCall(id=f"call_{index}", name=name, arguments=args)
            for index, (name, args) in enumerate(calls)
            if name in offered
        )
        return LLMResponse(tool_calls=tool_calls, stop_reason="tool_use" if tool_calls else "end")

    # -- synthesis -------------------------------------------------------
    def _synthesize(self, request: LLMRequest) -> dict:
        sentences, citations = [], []
        for message in request.messages:
            for result in message.tool_results:
                try:
                    payload = json.loads(result.content)
                except ValueError:
                    continue
                sources = payload.get("sources") or []
                source_id = sources[0]["source_id"] if sources else None
                if payload.get("status") != "ok":
                    sentences.append(f"The {result.name} data could not be retrieved ({payload.get('error_code')}).")
                    continue
                summary = payload.get("summary") or {}
                facts = ", ".join(f"{key.replace('_', ' ')}: {value}" for key, value in summary.items())
                label = sources[0]["label"] if sources else result.name
                sentences.append(f"{label} — {facts}." if facts else f"{label} returned no matching records.")
                if source_id:
                    citations.append(source_id)
            for document in message.documents:
                excerpt = " ".join(document.text.split())[:240]
                sentences.append(f'{document.label} states: "{excerpt}"')
                citations.append(document.source_id)
        if not sentences:
            return {
                "answer": "I could not find authoritative data or a relevant document to answer that.",
                "citations": [],
            }
        return {"answer": " ".join(sentences), "citations": citations}

    # -- drafting / suggestions -----------------------------------------
    def _draft(self, request: LLMRequest) -> dict:
        facts = request.metadata.get("facts", {})
        body = (
            f"Dear {facts.get('customer_name', 'Customer')},\n\n"
            f"This is a friendly reminder that invoice {facts.get('invoice_number', '')} "
            f"with an amount due of {facts.get('currency', '')} {facts.get('amount_due', '')} "
            f"was due on {facts.get('due_date', '')}. Please arrange payment at your earliest convenience.\n\n"
            "Thank you."
        )
        return {"subject": f"Payment reminder: invoice {facts.get('invoice_number', '')}", "body": body}

    def _suggest(self, request: LLMRequest) -> dict:
        accounts = request.metadata.get("allowed_accounts", [])
        description = str(request.metadata.get("description", "")).lower()
        words = set(re.findall(r"[a-z]{3,}", description))
        for account in accounts:
            if words & set(re.findall(r"[a-z]{3,}", account["name"].lower())):
                return {"account_id": account["id"], "confidence": "medium", "reasoning": "Description matches the account name."}
        return {"account_id": None, "confidence": "low", "reasoning": "No account clearly matches the description."}
