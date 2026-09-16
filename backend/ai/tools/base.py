import datetime
from collections.abc import Callable
from dataclasses import dataclass, field

from rest_framework import serializers

from ai.providers.base import ToolDefinition
from ai.sources import Source
from ai.tools.schemas import serializer_to_json_schema
from reports.selectors.params import to_json_safe


@dataclass(frozen=True)
class ToolContext:
    user: object
    organization: object
    request_id: str = ""
    today: datetime.date = field(default_factory=datetime.date.today)


class ToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ToolResult:
    tool: str
    status: str  # "ok" | "not_found" | "error"
    # Headline authoritative figures, exactly as the domain selector returned
    # them (Decimal -> str via to_json_safe, never float, never recomputed).
    summary: dict = field(default_factory=dict)
    # Bounded detail rows (ai/tools/executor.py enforces the size budget).
    data: dict = field(default_factory=dict)
    sources: list[Source] = field(default_factory=list)
    truncated: bool = False
    error_code: str = ""
    message: str = ""
    # Backend-only metrics (retrieval counts, embedding tokens). Never part
    # of the payload shown to the model or the client.
    telemetry: dict = field(default_factory=dict)

    def to_payload(self) -> dict:
        return to_json_safe(
            {
                "tool": self.tool,
                "status": self.status,
                "summary": self.summary,
                "data": self.data,
                "sources": [source.to_public() for source in self.sources],
                "truncated": self.truncated,
                "error_code": self.error_code or None,
                "message": self.message or None,
            }
        )


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    required_permissions: tuple[str, ...]
    input_serializer: type[serializers.Serializer]
    handler: Callable[[ToolContext, dict], ToolResult]
    # "structured" tools may be offered to the model during tool planning;
    # "document" tools are called by the orchestrator itself, so retrieved
    # (untrusted) text never shares a model turn with tool availability.
    category: str = "structured"
    read_only: bool = True

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name, description=self.description, input_schema=serializer_to_json_schema(self.input_serializer)
        )
