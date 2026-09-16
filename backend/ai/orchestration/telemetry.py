"""Usage telemetry and structured logs (phase sections 37/42/61).

Metadata only. The question, prompts, tool payloads, retrieved text and
answer are never logged or stored here.
"""

import logging
import time
from dataclasses import dataclass, field

from ai.models import AIRequestLog

logger = logging.getLogger("ai.requests")


def _add(total: int | None, value: int | None) -> int | None:
    if value is None:
        return total
    return (total or 0) + value


@dataclass
class Telemetry:
    feature: str
    started: float = field(default_factory=time.monotonic)
    provider: str = ""
    model: str = ""
    prompt_version: str = ""
    embedding_config_key: str = ""
    intent: str = ""
    tool_names: list[str] = field(default_factory=list)
    tool_call_count: int = 0
    retrieval_count: int = 0
    llm_calls: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None
    embedding_tokens: int | None = None

    def add_usage(self, usage) -> None:
        self.llm_calls += 1
        if usage is None:
            return
        self.input_tokens = _add(self.input_tokens, usage.input_tokens)
        self.output_tokens = _add(self.output_tokens, usage.output_tokens)
        self.cached_input_tokens = _add(self.cached_input_tokens, usage.cached_input_tokens)

    def add_tool(self, name: str, telemetry: dict | None = None) -> None:
        self.tool_call_count += 1
        if name not in self.tool_names:
            self.tool_names.append(name)
        if telemetry:
            self.retrieval_count += telemetry.get("retrieval_count") or 0
            self.embedding_tokens = _add(self.embedding_tokens, telemetry.get("embedding_tokens"))

    @property
    def latency_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)


def record_request(*, organization, user, request_id: str, telemetry: Telemetry, status: str, error_code: str = "", conversation=None):
    log = AIRequestLog.objects.create(
        organization=organization, user=user, conversation=conversation, request_id=request_id[:64],
        feature=telemetry.feature, intent=telemetry.intent, status=status, error_code=error_code,
        provider=telemetry.provider, model=telemetry.model, prompt_version=telemetry.prompt_version,
        embedding_config_key=telemetry.embedding_config_key, tool_names=telemetry.tool_names,
        tool_call_count=telemetry.tool_call_count, retrieval_count=telemetry.retrieval_count,
        llm_calls=telemetry.llm_calls, input_tokens=telemetry.input_tokens, output_tokens=telemetry.output_tokens,
        cached_input_tokens=telemetry.cached_input_tokens, embedding_tokens=telemetry.embedding_tokens,
        latency_ms=telemetry.latency_ms,
    )
    logger.info(
        "ai_request",
        extra={
            "request_id": request_id, "organization_id": str(organization.id), "feature": telemetry.feature,
            "intent": telemetry.intent, "status": status, "error_code": error_code, "provider": telemetry.provider,
            "model": telemetry.model, "tool_names": telemetry.tool_names, "tool_call_count": telemetry.tool_call_count,
            "retrieval_count": telemetry.retrieval_count, "input_tokens": telemetry.input_tokens,
            "output_tokens": telemetry.output_tokens, "latency_ms": telemetry.latency_ms,
        },
    )
    return log
