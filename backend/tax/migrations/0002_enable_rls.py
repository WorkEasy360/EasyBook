"""Row Level Security for every tenant-scoped tax table.

`tax_statecode` is deliberately ABSENT: it is global reference data with no
`organization` column (see tax/models/state.py), exactly like
`accounts_currency`. An org-scoped policy on it would match nothing and make
the state master invisible to every tenant.

A new tenant-scoped tax model without an entry here is a bug, and
tax/tests/test_rls.py fails if one appears.
"""

from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):
    dependencies = [
        ("tax", "0001_initial"),
    ]

    operations = [
        enable_rls_org_scoped("tax_taxprofile"),
        enable_rls_org_scoped("tax_taxrate"),
        enable_rls_org_scoped("tax_taxaccountmapping"),
        enable_rls_org_scoped("tax_withholdingsection"),
    ]
