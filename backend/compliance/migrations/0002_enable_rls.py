"""Row Level Security for every compliance table.

An IRN and an e-way bill number are government-issued identifiers tied to one
taxpayer's filings; cross-tenant leakage here would expose another
organization's tax position. A new compliance model without an entry here is a
bug, and compliance/tests/test_rls.py fails if one appears.
"""

from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):
    dependencies = [
        ("compliance", "0001_initial"),
    ]

    operations = [
        enable_rls_org_scoped("compliance_einvoicedocument"),
        enable_rls_org_scoped("compliance_ewaybill"),
    ]
