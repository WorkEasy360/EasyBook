# AUTOMATION

PURPOSE
Tenant-safe, event/schedule-driven automation engine (Phase 11): trigger -> conditions -> actions -> execution history. The top layer — sits above `ai` (calls `ai.orchestration.assist` for draft actions) and every module below it; nothing may import `automation`.

OWNS (Slice 1 — rule lifecycle; see IMPLEMENTATION SLICES for what's next)
- `models/rule.py` — `AutomationRule` (`RuleStatus`: DRAFT/ACTIVE/PAUSED/ARCHIVED, via `services/rules.py::_ALLOWED_TRANSITIONS`). Only ACTIVE rules are meant to ever be evaluated. `version` increments on any structural edit (trigger/conditions/actions) so a future `AutomationExecution` can record which definition it actually ran under.
- `models/condition.py` — `AutomationCondition`. `field`/`operator` validated against a strict per-trigger allowlist (`conditions/schemas.py`) — never arbitrary model attribute access, never `eval()`.
- `models/action.py` — `AutomationActionConfig`. `config` validated against the action's own JSON schema (`actions/registry.py::validate_action_config`) — unknown keys rejected.
- `triggers/registry.py` — trigger catalog: identifiers/labels/category only in this slice. Actually dispatching a domain event or evaluating a schedule against this catalog is a later slice.
- `conditions/operators.py`, `conditions/schemas.py` — the fixed operator set and the field allowlist per trigger type, with per-field-type value validation (Decimal/int/date/string).
- `actions/registry.py` — action catalog. `register()` refuses `safety_level >= 3` structurally, so a Level 3 action (post invoice, record payment, reconcile, submit GST, transfer stock, send payment) can never be reachable from this registry. Default catalog is Level 1 only: `send_notification`, `generate_report`, `draft_payment_reminder` (wraps `ai.orchestration.assist.draft_payment_reminder` — draft text only, never sent).
- `services/rules.py` — `create_rule`/`update_rule`, and `activate_rule`/`pause_rule`/`archive_rule`. Activation re-validates the rule's current conditions/actions against the live registries — it does not trust whatever was valid when last saved.
- `api/` — `/api/v1/automation/rules/` (list/create), `/rules/{id}/` (retrieve/update), `/rules/{id}/activate|pause|archive/`, `/catalog/triggers/`, `/catalog/actions/`.

DOES NOT OWN (deferred to later slices)
- Domain event dispatch / schedule evaluation, `AutomationExecution`/`AutomationStepExecution`/`AutomationEvent` models, idempotency keys, retries, causation/depth loop prevention, webhooks/SSRF.
- Accounting, inventory, sales/purchases, tax, banking reconciliation, AI financial truth — automation only ever reaches these through their own domain services.

INVARIANTS
- Every model here is `TenantScopedModel` with a matching RLS migration (`migrations/0002_enable_rls.py`) — `tests/test_rls.py` asserts this exhaustively.
- No arbitrary code/SQL/expression evaluation — `conditions/schemas.py` and `actions/registry.py` are the only places a string becomes a comparison or a config value, both via fixed allowlists.
- AI actions are advisory/text-only; automation remains the authoritative execution decision-maker.
- No class-level tenant-scoped `.objects.all()` querysets in `api/views.py` — always built in `get_queryset()` (core/CLAUDE.md).

TESTS
`tests/test_rls.py` (RLS coverage), `tests/test_rules.py` (lifecycle/validation/tenant isolation), `tests/test_api.py` (RBAC + API).

TOKEN DISCIPLINE
Do not duplicate root, `core`, `authz`, `audit`, or `ai` CLAUDE.md content here. Read `services/rules.py` first.
