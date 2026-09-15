from django.db import transaction

from accounting.models.account import Account, AccountType
from audit.models import AuditLog
from audit.services import record as record_audit
from core.exceptions import ApplicationError
from items.models.hsn_sac import HsnSacCode
from items.models.item import Item, ItemType
from items.models.unit import UnitOfMeasure

# Which accounting.AccountType each item account field must resolve to —
# enforced so, e.g., a sales_account can't silently point at a Liability
# account (see root CLAUDE.md: never invent/skip validation for money paths).
_ACCOUNT_FIELD_TYPES = {
    "sales_account": AccountType.INCOME,
    "purchase_account": AccountType.EXPENSE,
    "inventory_account": AccountType.ASSET,
    "cogs_account": AccountType.EXPENSE,
}


def _validate_organization_scoped_fk(*, organization, field_name: str, value) -> None:
    if value is not None and value.organization_id != organization.id:
        raise ApplicationError(
            f"{field_name} must belong to the same organization.", code="cross_org_reference"
        )


def _validate_account_fields(*, organization, fields: dict) -> None:
    for field_name, expected_type in _ACCOUNT_FIELD_TYPES.items():
        if field_name not in fields:
            continue
        account: Account | None = fields[field_name]
        if account is None:
            continue
        _validate_organization_scoped_fk(organization=organization, field_name=field_name, value=account)
        if account.account_type != expected_type:
            raise ApplicationError(
                f"{field_name} must reference an account of type '{expected_type}'.",
                code="invalid_account_type",
            )


def _validate_unit(*, organization, unit: UnitOfMeasure, require_active: bool) -> None:
    _validate_organization_scoped_fk(organization=organization, field_name="unit", value=unit)
    if require_active and not unit.is_active:
        raise ApplicationError("Inactive units cannot be assigned to items.", code="unit_inactive")


def _validate_service_cannot_track_inventory(*, item_type: str, track_inventory: bool) -> None:
    if item_type == ItemType.SERVICE and track_inventory:
        raise ApplicationError(
            "Service items cannot track inventory.", code="service_cannot_track_inventory"
        )


@transaction.atomic
def create_item(
    *,
    organization,
    item_type: str,
    name: str,
    unit: UnitOfMeasure,
    sku: str = "",
    description: str = "",
    is_sellable: bool = True,
    is_purchasable: bool = True,
    track_inventory: bool = False,
    hsn_sac_code: HsnSacCode | None = None,
    tax_category: str = "",
    actor=None,
    **account_and_price_fields,
) -> Item:
    _validate_service_cannot_track_inventory(item_type=item_type, track_inventory=track_inventory)
    _validate_unit(organization=organization, unit=unit, require_active=True)
    _validate_account_fields(organization=organization, fields=account_and_price_fields)
    if hsn_sac_code is not None:
        _validate_organization_scoped_fk(organization=organization, field_name="hsn_sac_code", value=hsn_sac_code)

    item = Item.objects.create(
        organization=organization,
        item_type=item_type,
        name=name,
        sku=sku,
        description=description,
        unit=unit,
        is_sellable=is_sellable,
        is_purchasable=is_purchasable,
        track_inventory=track_inventory,
        hsn_sac_code=hsn_sac_code,
        tax_category=tax_category,
        **account_and_price_fields,
    )
    record_audit(
        organization_id=organization.id,
        actor=actor,
        action=AuditLog.Action.CREATE,
        object_type="items.Item",
        object_id=item.id,
        changes={"name": name, "item_type": item_type, "sku": sku},
    )
    return item


@transaction.atomic
def update_item(*, item: Item, actor=None, **fields) -> Item:
    item_type = fields.get("item_type", item.item_type)
    track_inventory = fields.get("track_inventory", item.track_inventory)
    _validate_service_cannot_track_inventory(item_type=item_type, track_inventory=track_inventory)

    if "unit" in fields:
        _validate_unit(organization=item.organization, unit=fields["unit"], require_active=True)
    account_fields = {k: v for k, v in fields.items() if k in _ACCOUNT_FIELD_TYPES}
    _validate_account_fields(organization=item.organization, fields=account_fields)
    if "hsn_sac_code" in fields and fields["hsn_sac_code"] is not None:
        _validate_organization_scoped_fk(
            organization=item.organization, field_name="hsn_sac_code", value=fields["hsn_sac_code"]
        )

    changes = {}
    for field, value in fields.items():
        if getattr(item, field) == value:
            continue
        changes[field] = str(value.pk) if hasattr(value, "pk") else value
        setattr(item, field, value)
    if not changes:
        return item

    item.save(update_fields=[*changes.keys(), "updated_at"])
    record_audit(
        organization_id=item.organization_id,
        actor=actor,
        action=AuditLog.Action.UPDATE,
        object_type="items.Item",
        object_id=item.id,
        changes=changes,
    )
    return item


def archive_item(*, item: Item, actor=None) -> Item:
    return update_item(item=item, is_active=False, actor=actor)
