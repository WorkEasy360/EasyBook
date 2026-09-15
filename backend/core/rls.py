"""Helpers for writing RLS-enabling data migrations (see core/CLAUDE.md)."""

from django.db import migrations


def enable_rls_org_scoped(table_name: str, policy_name: str | None = None):
    """Enable RLS on `table_name` and restrict rows to the current tenant GUC.

    Applies to tables owned exclusively by one organization (FiscalYear,
    NumberSequence, AuditLog, ...). Use `enable_rls_self_or_org_scoped` for
    tables a user must also see across organizations (Membership).
    """
    policy_name = policy_name or f"{table_name}_tenant_isolation"
    sql = f"""
        ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY;
        CREATE POLICY {policy_name} ON {table_name}
            USING (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid)
            WITH CHECK (organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid);
    """
    reverse_sql = f"""
        DROP POLICY IF EXISTS {policy_name} ON {table_name};
        ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY;
    """
    return migrations.RunSQL(sql=sql, reverse_sql=reverse_sql)


def enable_rls_self_or_org_scoped(table_name: str, user_column: str = "user_id", policy_name: str | None = None):
    """Row visible if it belongs to the current org OR to the current user.

    Needed for Membership: a user must be able to list their own memberships
    across organizations before an organization has been selected.
    """
    policy_name = policy_name or f"{table_name}_tenant_or_self_isolation"
    sql = f"""
        ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;
        ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY;
        CREATE POLICY {policy_name} ON {table_name}
            USING (
                organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid
                OR {user_column} = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            )
            WITH CHECK (
                organization_id = NULLIF(current_setting('app.current_organization_id', true), '')::uuid
                OR {user_column} = NULLIF(current_setting('app.current_user_id', true), '')::uuid
            );
    """
    reverse_sql = f"""
        DROP POLICY IF EXISTS {policy_name} ON {table_name};
        ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY;
    """
    return migrations.RunSQL(sql=sql, reverse_sql=reverse_sql)
