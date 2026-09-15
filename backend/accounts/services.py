from accounts.models import NumberSequence
from core.tenancy import tenant_context


def allocate_sequence_number(*, organization_id, key: str, prefix: str = "", padding: int = 4) -> str:
    """Atomically allocates and formats the next number for (organization, key).

    Locks the sequence row (select_for_update) so concurrent requests never
    hand out the same invoice/journal number. Self-scopes the tenant context
    (which itself opens a transaction, see core.tenancy) so it is safe to
    call from Celery tasks/management commands as well as requests that
    already carry one.
    """
    with tenant_context(organization_id=organization_id):
        sequence, _ = NumberSequence.all_objects.select_for_update().get_or_create(
            organization_id=organization_id,
            key=key,
            defaults={"prefix": prefix, "padding": padding},
        )
        value = sequence.next_number
        sequence.next_number = value + 1
        sequence.save(update_fields=["next_number", "updated_at"])
        return sequence.format(value)
