"""Row Level Security for every purchases table.

Every model in this app is a TenantScopedModel, so every one of its tables
needs a matching policy — the TenantManager filter is defense in depth, not
the primary control (see core/CLAUDE.md). A new purchases model without an
entry here is a bug.
"""

from django.db import migrations

from core.rls import enable_rls_org_scoped


class Migration(migrations.Migration):

    dependencies = [
        ("purchases", "0001_initial"),
    ]

    operations = [
        enable_rls_org_scoped("purchases_vendor"),
        enable_rls_org_scoped("purchases_purchaseorder"),
        enable_rls_org_scoped("purchases_purchaseorderline"),
        enable_rls_org_scoped("purchases_goodsreceipt"),
        enable_rls_org_scoped("purchases_goodsreceiptline"),
        enable_rls_org_scoped("purchases_bill"),
        enable_rls_org_scoped("purchases_billline"),
        enable_rls_org_scoped("purchases_expense"),
        enable_rls_org_scoped("purchases_vendorpayment"),
        enable_rls_org_scoped("purchases_vendorpaymentallocation"),
        enable_rls_org_scoped("purchases_vendorcredit"),
        enable_rls_org_scoped("purchases_vendorcreditline"),
        enable_rls_org_scoped("purchases_recurringbilltemplate"),
        enable_rls_org_scoped("purchases_recurringbilltemplateline"),
        enable_rls_org_scoped("purchases_recurringbillrun"),
        enable_rls_org_scoped("purchases_recurringexpensetemplate"),
        enable_rls_org_scoped("purchases_recurringexpenserun"),
    ]
