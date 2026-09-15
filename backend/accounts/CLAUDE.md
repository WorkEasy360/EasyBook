# ACCOUNTS

PURPOSE
Identity and tenancy root: who users are, what organizations exist, who belongs to which organization with what role, and org-scoped foundational reference data (fiscal years, number sequences, currency).

OWNS
- `User` (custom, email-based `AUTH_USER_MODEL`) — global identity, not org-scoped.
- `Organization` — the tenant itself; deliberately has no RLS policy (see DOES NOT OWN).
- `Membership` — links User↔Organization with a `Role` (from `authz`). RLS policy is "self-or-org", not plain org-scoped — see INVARIANTS.
- `Currency` — global ISO 4217 reference data, not org-scoped.
- `FiscalYear`, `NumberSequence` — org-scoped (`TenantScopedModel`), RLS-protected.
- `services.py` — `allocate_sequence_number()`: atomic, lock-based number allocation.

DOES NOT OWN
- Role/permission definitions (`authz.roles`).
- Audit trail writing (`audit.services.record`).
- `Organization` itself is NOT RLS-protected: a user must be able to discover which organizations they belong to before any org is selected, which is inherently a cross-tenant-of-self query. Isolation for "which orgs can I see" comes from joining through `Membership` (which IS RLS-protected via the self-or-org policy), not from RLS on `Organization`.

DEPENDENCIES
- `authz.roles` (Role choices, permission catalog)
- `core.models.TenantScopedModel`, `core.tenancy`

INVARIANTS
- `Membership` visibility: a row is visible if `organization_id` matches the current org GUC OR `user_id` matches the current user GUC (`enable_rls_self_or_org_scoped` in `migrations/0002_enable_rls.py`). Do not "simplify" this to plain org-scoping — it would break the "list my organizations" flow before an org is selected.
- `NumberSequence.allocate_sequence_number()` must always go through `select_for_update()` — never read-then-write the `next_number` field directly, or concurrent requests will hand out duplicate invoice/journal numbers.
- Service functions here (`allocate_sequence_number`) self-scope the tenant context via `core.tenancy.tenant_context` so they're safe to call from Celery tasks and management commands, not only from an already org-scoped request.

TESTS
- `accounts/tests/test_auth.py` — registration, login, org creation grants Owner role.
- `accounts/tests/test_number_sequence.py` — sequential + concurrent allocation uniqueness (uses `TransactionTestCase`, real threads/connections — required for `select_for_update` to actually contend).

SECURITY
- Passwords always via `User.objects.create_user()` (hashes via Django's password hasher) — never set `.password` directly.
- `RegisterView` and `LoginView` carry `throttle_scope = "auth"` — do not remove; these are the highest-value brute-force targets.

READ FIRST
- `models.py`, `services.py`, `views.py`

DO NOT READ BY DEFAULT
- `migrations/` unless the migration itself is in question
