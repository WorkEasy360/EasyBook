"""The single entry point for running an AI tool. Every call — whether the
model requested it or the orchestrator did — passes the same gates here, in
this order, independently of anything the chat endpoint already checked
(phase section 22, defense in depth):

  1. the tool exists in the read-only registry
  2. the ambient tenant context IS the caller's organization (fail closed)
  3. the user holds an ACTIVE membership in that organization, re-read from
     the database now
  4. that membership's role grants every permission the tool declares
  5. the arguments pass the tool's strict schema (unknown keys rejected)
  6. the handler runs inside a savepoint that is ALWAYS rolled back, so even
     a hypothetical write inside a selector can never persist
  7. the result is shaped to the configured size budget
Failures return a ToolResult with status "error"/"not_found" and a safe
code — never a raw exception or stack trace.
"""

import json
import logging
import time

from django.db import transaction

from accounts.models import Membership
from ai.config import get_ai_config
from ai.tools.base import ToolContext, ToolError, ToolResult
from ai.tools.registry import get_tool
from authz.roles import role_has_permission
from core.exceptions import ApplicationError
from core.tenancy import get_current_organization_id

logger = logging.getLogger("ai.tools")


def _error(name: str, code: str, message: str, status: str = "error") -> ToolResult:
    return ToolResult(tool=name, status=status, error_code=code, message=message)


def _payload_size(result: ToolResult) -> int:
    return len(json.dumps(result.to_payload(), default=str))


def shape_result(result: ToolResult, *, max_chars: int, max_rows: int) -> ToolResult:
    """Bound a result for LLM context: cap every list in `data` at max_rows,
    then halve lists until the serialized payload fits max_chars, and as a
    last resort drop `data` entirely (summary + sources always survive)."""
    for key, value in list(result.data.items()):
        if isinstance(value, list) and len(value) > max_rows:
            result.data[key] = value[:max_rows]
            result.truncated = True
    while _payload_size(result) > max_chars:
        lists = [key for key, value in result.data.items() if isinstance(value, list) and len(value) > 1]
        if not lists:
            if result.data:
                result.data = {}
                result.truncated = True
                continue
            break
        for key in lists:
            result.data[key] = result.data[key][: len(result.data[key]) // 2]
        result.truncated = True
    return result


def execute_tool(ctx: ToolContext, name: str, arguments) -> ToolResult:
    started = time.monotonic()
    config = get_ai_config()
    tool = get_tool(name)
    if tool is None:
        return _error(name, "unknown_tool", "No such tool.")

    if get_current_organization_id() != str(ctx.organization.id):
        return _error(name, "tenant_context_mismatch", "Tool call rejected: tenant context is not established.")

    membership = Membership.all_objects.filter(
        organization_id=ctx.organization.id, user_id=getattr(ctx.user, "id", None), is_active=True
    ).first()
    if membership is None:
        return _error(name, "forbidden", "You do not have access to this organization.")
    missing = [perm for perm in tool.required_permissions if not role_has_permission(membership.role, perm)]
    if missing:
        return _error(name, "forbidden", "You do not have permission to view this data.")

    serializer = tool.input_serializer(data=arguments if arguments is not None else {})
    if not serializer.is_valid():
        # Field NAMES only — never echo model-supplied values back.
        fields = sorted(serializer.errors) if isinstance(serializer.errors, dict) else []
        return _error(name, "invalid_arguments", f"Invalid tool arguments: {', '.join(fields) or 'malformed'}.")

    try:
        with transaction.atomic():
            try:
                result = tool.handler(ctx, dict(serializer.validated_data))
            finally:
                transaction.set_rollback(True)
    except ToolError as exc:
        result = _error(name, exc.code, exc.message, status="not_found" if exc.code == "not_found" else "error")
    except ApplicationError as exc:
        codes = exc.get_codes()
        code = codes if isinstance(codes, str) else "tool_error"
        result = _error(name, code, str(exc.detail))
    except Exception:
        logger.exception("ai_tool_failed", extra={"tool": name, "request_id": ctx.request_id})
        result = _error(name, "tool_error", "The data could not be retrieved.")

    # Document passages get half the context budget (they ARE the evidence
    # for a document question); structured results get the tool budget.
    max_chars = config.max_context_chars // 2 if tool.category == "document" else config.max_tool_output_chars
    shaped = shape_result(result, max_chars=max_chars, max_rows=config.max_tool_rows)
    logger.info(
        "ai_tool_call",
        extra={
            "tool": name, "status": shaped.status, "request_id": ctx.request_id,
            "latency_ms": int((time.monotonic() - started) * 1000),
        },
    )
    return shaped
