from django.db import migrations

CURRENCIES = [
    ("INR", "Indian Rupee", "₹", 2),
    ("USD", "US Dollar", "$", 2),
    ("EUR", "Euro", "€", 2),
    ("GBP", "Pound Sterling", "£", 2),
    ("AED", "UAE Dirham", "د.إ", 2),
    ("SGD", "Singapore Dollar", "S$", 2),
]


def seed_currencies(apps, schema_editor):
    Currency = apps.get_model("accounts", "Currency")
    for code, name, symbol, decimal_places in CURRENCIES:
        Currency.objects.update_or_create(
            code=code, defaults={"name": name, "symbol": symbol, "decimal_places": decimal_places}
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_enable_rls"),
    ]

    operations = [
        migrations.RunPython(seed_currencies, noop_reverse),
    ]
