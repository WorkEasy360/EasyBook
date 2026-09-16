"""Row Level Security for every banking table.

Every model in this app is a TenantScopedModel, so every one of its tables
needs a matching policy — the TenantManager filter is defense in depth, not
the primary control (see core/CLAUDE.md). A new banking model without an entry
here is a bug, and banking/tests/test_rls.py fails if one appears.

This app holds the most sensitive data in the product: a bank statement
discloses every counterparty an organization deals with and every amount. A
missing policy here leaks more than a missing policy anywhere else.
"""

from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):

    dependencies = [
        ("banking", "0001_initial"),
    ]

    operations = [
        enable_rls_org_scoped("banking_bankaccount"),
        enable_rls_org_scoped("banking_statementimport"),
        enable_rls_org_scoped("banking_banktransaction"),
        enable_rls_org_scoped("banking_banktransactionmatch"),
        enable_rls_org_scoped("banking_banktransfer"),
        enable_rls_org_scoped("banking_bankrule"),
        enable_rls_org_scoped("banking_bankreconciliation"),
    ]
