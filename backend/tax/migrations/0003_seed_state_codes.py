"""Seeds the GST state/UT code master from `tax.state_master`.

The data and its provenance live in that module, not here, because the test
suite must be able to restore the master after a `TransactionTestCase` flush
truncates it - and a flush does not re-run data migrations. See
tax/state_master.py.

This is the ONE piece of compliance data this codebase seeds; tax/models/state.py
records why it earns the exception that `items.HsnSacCode` does not.
"""

from django.db import migrations

from tax.state_master import STATE_CODE_VALUES, STATE_CODES


def seed(apps, schema_editor):
    StateCode = apps.get_model("tax", "StateCode")
    StateCode.objects.bulk_create(
        [
            StateCode(
                code=code,
                name=name,
                is_union_territory=is_ut,
                uses_utgst=uses_utgst,
                is_special=is_special,
            )
            for code, name, is_ut, uses_utgst, is_special in STATE_CODES
        ],
        # Idempotent: re-applying against a database that already holds the
        # master must not explode on the primary key.
        ignore_conflicts=True,
    )


def unseed(apps, schema_editor):
    StateCode = apps.get_model("tax", "StateCode")
    StateCode.objects.filter(code__in=STATE_CODE_VALUES).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("tax", "0002_enable_rls"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
