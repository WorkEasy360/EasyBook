"""Vendor-neutral LLM interface.

The application talks to `LLMProvider` and these message dataclasses only.
A concrete provider translates them into its vendor's native structured
message format — system policy, user question, tool calls/results, and
UNTRUSTED retrieved documents stay separate typed parts all the way to the
wire, never one concatenated prompt string (phase section 31).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ai.providers.errors import ProviderMalformedOutput


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass(frozen=True)
class ToolResultBlock:
    tool_call_id: str
    name: str
    # JSON text produced by the backend's bounded result shaping
    # (ai/tools/executor.py), never a raw queryset dump.
    content: str
    is_error: bool = False


@dataclass(frozen=True)
class UntrustedDocument:
    """Retrieved document text. Providers MUST render this as clearly
    delimited, quoted data — never as instructions (phase section 29)."""

    source_id: str
    label: str
    text: str
    page: int | None = None
    section: str = ""


@dataclass(frozen=True)
class Message:
    role: str  # "user" | "assistant" | "tool"
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResultBlock, ...] = ()
    documents: tuple[UntrustedDocument, ...] = ()


@dataclass(frozen=True)
class LLMRequest:
    system: str
    messages: tuple[Message, ...]
    purpose: str  # "route" | "tool_planning" | "synthesis" | "draft" | "suggest"
    max_output_tokens: int
    temperature: float
    timeout_seconds: float
    tools: tuple[ToolDefinition, ...] = ()
    response_schema: dict | None = None
    # Non-authoritative hints the orchestrator ALSO renders into message text
    # (e.g. resolved calendar periods). Real providers ignore this field; the
    # deterministic fake provider reads it instead of parsing prose.
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_input_tokens: int | None = None


@dataclass(frozen=True)
class LLMResponse:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    structured: dict | None = None
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = "end"
    model: str = ""


class LLMProvider(ABC):
    name: str = "base"
    supports_tools: bool = True
    supports_structured_output: bool = True
    # Streaming is deliberately deferred until correctness/security gates
    # pass (phase section 45).
    supports_streaming: bool = False

    def __init__(self, *, model: str):
        self.model = model

    @abstractmethod
    def generate(self, request: LLMRequest) -> LLMResponse:
        """One model call. Must honour `request.timeout_seconds` and raise
        only `ai.providers.errors.AIProviderError` subclasses."""

    def structured_output(self, request: LLMRequest) -> LLMResponse:
        if request.response_schema is None:
            raise ValueError("structured_output requires response_schema")
        response = self.generate(request)
        if not isinstance(response.structured, dict):
            raise ProviderMalformedOutput("provider returned no structured object")
        return response

    def tool_calling(self, request: LLMRequest) -> LLMResponse:
        if not request.tools:
            raise ValueError("tool_calling requires at least one tool definition")
        return self.generate(request)

    def stream(self, request: LLMRequest):
        raise NotImplementedError("Streaming is not enabled for Ask Books in Phase 10.")
