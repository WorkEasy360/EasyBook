from audit.models import AuditLog
from core.request_context import get_request_id
from core.tenancy import tenant_context


def record(*, organization_id, action, object_type, object_id, actor=None, changes=None, request=None):
    """Creates an audit trail entry. Call this from services after a mutation
    commits successfully — never from signals on models that may roll back.
    Self-scopes the tenant context so it works regardless of caller (request,
    Celery task, management command).

    The request correlation id comes from `request` when one is passed, else
    from the ambient request/task context (core.request_context), so every
    audit row written while serving a request — or running a task — can be
    matched to its log lines. Both sources are already bounded to 64 chars."""
    with tenant_context(organization_id=organization_id):
        return AuditLog.objects.create(
            organization_id=organization_id,
            actor=actor,
            action=action,
            object_type=object_type,
            object_id=str(object_id),
            changes=changes or {},
            request_id=(getattr(request, "request_id", "") if request else get_request_id()) or "",
            ip_address=request.META.get("REMOTE_ADDR") if request else None,
        )
