from django.db import transaction

from accounting.models.account import Account
from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError

# Fields a caller may change on a system account without special authorization.
# code/account_type/parent are load-bearing for platform behavior (e.g. the
# opening-balance equity account the posting engine targets) and are frozen
# once the account exists.
SYSTEM_ACCOUNT_MUTABLE_FIELDS = {"name", "description", "account_subtype"}


def _assert_no_cycle(account: Account, new_parent: Account) -> None:
    node = new_parent
    seen = set()
    while node is not None:
        if node.pk == account.pk:
            raise ApplicationError(
                "Setting this parent would create a circular account hierarchy.",
                code="account_hierarchy_cycle",
            )
        if node.pk in seen:
            break
        seen.add(node.pk)
        node = node.parent


@transaction.atomic
def create_account(
    *,
    organization,
    code: str,
    name: str,
    account_type: str,
    account_subtype: str = "",
    parent: Account | None = None,
    description: str = "",
    is_system: bool = False,
    actor=None,
) -> Account:
    if parent is not None and parent.organization_id != organization.id:
        raise ApplicationError(
            "Parent account must belong to the same organization.", code="account_parent_cross_org"
        )

    account = Account.objects.create(
        organization=organization,
        code=code,
        name=name,
        account_type=account_type,
        account_subtype=account_subtype,
        parent=parent,
        description=description,
        is_system=is_system,
    )
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="accounting.Account",
        object_id=account.id,
        changes={"code": code, "name": name, "account_type": account_type},
    )
    return account


@transaction.atomic
def update_account(*, account: Account, actor=None, **fields) -> Account:
    if account.is_system:
        disallowed = set(fields) - SYSTEM_ACCOUNT_MUTABLE_FIELDS
        if disallowed:
            raise ApplicationError(
                f"System account fields cannot be changed: {', '.join(sorted(disallowed))}.",
                code="system_account_protected",
            )

    new_parent = fields.get("parent", "unset")
    if new_parent not in ("unset", None):
        if new_parent.organization_id != account.organization_id:
            raise ApplicationError(
                "Parent account must belong to the same organization.", code="account_parent_cross_org"
            )
        _assert_no_cycle(account, new_parent)

    changes = {}
    for field, value in fields.items():
        if getattr(account, field) != value:
            changes[field] = str(value.pk) if isinstance(value, Account) else value
        setattr(account, field, value)
    if not changes:
        return account

    account.save(update_fields=[*changes.keys(), "updated_at"])
    record_audit(
        organization_id=account.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="accounting.Account",
        object_id=account.id,
        changes=changes,
    )
    return account
