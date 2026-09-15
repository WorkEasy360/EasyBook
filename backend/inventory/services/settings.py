from inventory.models.settings import InventorySettings


def get_or_create_inventory_settings(organization) -> InventorySettings:
    settings, _ = InventorySettings.objects.get_or_create(organization=organization)
    return settings


def allows_negative_stock(organization) -> bool:
    return get_or_create_inventory_settings(organization).allow_negative_stock
