"""Organization access gates: checks other apps add to every organization-scoped request, without core
importing them (``accounts`` and ``core`` never import ``sso``).

``sso`` registers the Workeasy360 entitlement re-check (sso/entitlement.py) from ``SsoConfig.ready``.
A gate is called with the request and the view once the tenant is resolved (``request.organization`` is
set), before the view's own permission classes, and refuses the request by raising an ``APIException``.
"""

from collections.abc import Callable

Gate = Callable[[object, object], None]
#: (organization, task name) -> may the task produce its customer-visible effect for this organization?
BackgroundGate = Callable[[object, str], bool]

_gates: list[Gate] = []
_background_gates: list[BackgroundGate] = []


def register(gate: Gate) -> None:
    if gate not in _gates:
        _gates.append(gate)


def check(request, view) -> None:
    for gate in _gates:
        gate(request, view)


def register_background(gate: BackgroundGate) -> None:
    if gate not in _background_gates:
        _background_gates.append(gate)


def background_allowed(organization, *, task: str) -> bool:
    """Background work (a Celery task or a schedule, no request) asks this before it produces a customer
    business effect for ``organization``. False: skip the work quietly - the gate has logged why - and
    leave the schedule as it was so the work resumes once access returns. Never raises."""
    return all(gate(organization, task) for gate in _background_gates)


def background_allowed_for_id(organization_id, *, task: str) -> bool:
    """``background_allowed`` for a task that was handed only an organization id. No gate registered: no
    query."""
    if not _background_gates:
        return True
    from accounts.models import Organization  # not tenant-scoped: readable outside a tenant context

    organization = Organization.objects.only("id", "workeasy360_organization_id").filter(pk=organization_id).first()
    return organization is None or background_allowed(organization, task=task)
