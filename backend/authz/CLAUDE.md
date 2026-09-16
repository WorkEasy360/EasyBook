# AUTHZ

PURPOSE
Role and permission catalog and enforcement. Deliberately a fixed, code-defined set for v1 — not a database-backed, per-organization customizable RBAC system.

OWNS
- `roles.py` — `Role` (TextChoices: owner/admin/accountant/staff/viewer), `Permission` (string constants), `ROLE_PERMISSIONS` mapping, `role_has_permission()`.
- `permissions.py` — `HasOrgPermission` DRF permission class, reads `view.required_permission` against `request.membership.role` (set by `core.views.OrganizationScopedMixin`).

DOES NOT OWN
- Who belongs to which organization (`accounts.Membership`) — authz only defines what a role CAN do, not who HAS a role.
- Custom/per-organization roles — explicitly out of scope until Phase 12 (scale features). Do not add a `Role` database model or per-org permission overrides without revisiting this decision; it's deliberate, not an oversight.

DEPENDENCIES
None on other domain apps — `roles.py` is pure Python so it can be imported from `accounts.models` without a circular import.

INVARIANTS
- `ROLE_PERMISSIONS` must stay a strict hierarchy in practice (owner ⊇ admin ⊇ everything else) — if you add a permission, decide deliberately which roles get it rather than defaulting everyone in.
- A view enforcing a permission sets `required_permission = Permission.X` as a class attribute and includes `HasOrgPermission` in `permission_classes`; it does nothing on its own without `OrganizationScopedMixin` having already set `request.membership`.
- Detail views split `required_permission` by HTTP method via a `@property` (GET -> the VIEW_ permission, writes -> the MANAGE_/action permission). Hardcoding the write permission on a detail view silently blocks viewers from retrieving a single record — that was a real bug in `sales.CreditNoteDetailView`, and `purchases/tests/test_api.py` carries a regression guard for the same shape.
- One deliberate non-mirror between the sales and purchases grants: `Role.STAFF` may `RECORD_PAYMENT` (money in) but NOT `RECORD_VENDOR_PAYMENT` (money out). That is the segregation-of-duties line; see purchases/CLAUDE.md and the assertions in tests/test_roles.py before changing it.

TESTS
Any change to `ROLE_PERMISSIONS` needs a test asserting the new matrix — a silent permission grant/revoke here is a security-relevant regression, not a refactor.

READ FIRST
- `roles.py`, `permissions.py`
