# TAX

PURPOSE
Indian GST engine: state master, GSTIN/PAN validation, supply-nature determination (IGST Act ss.7/8), CGST/SGST/UTGST/IGST/cess computation, tax-account mapping, rate table, and TDS/TCS withholding sections. Phase 7 complete: this app plus full integration into `sales`/`purchases` (component columns and per-component journal posting on the five documents that post — Invoice, CreditNote, Bill, VendorCredit, Expense — with the pre-existing single-account posting kept as an automatic fallback), plus `compliance` (registers, GSTR-1/3B summaries, e-Invoice/e-Way Bill payloads and providers). Owns no sales/purchases domain logic — those two modules call in, this module never calls out to them.

OWNS
- `models/state.py` — `StateCode`. GLOBAL reference data (like `accounts.Currency`), NOT tenant-scoped and deliberately absent from `migrations/0002_enable_rls.py` — see the model docstring. Seeded from `state_master.py` by `migrations/0003_seed_state_codes.py`; the ONE piece of compliance data this codebase seeds (root CLAUDE.md rule 5 otherwise forbids it — see `items/CLAUDE.md` on `HsnSacCode`). `state_master.py` is a plain module, not inlined in the migration, because `tax/testing.py::ensure_state_codes` needs the same data to repair a `TransactionTestCase` flush (Django does not re-run data migrations after a flush).
- `models/profile.py` — `TaxProfile`, one row per org (mirrors `inventory.InventorySettings`). Every threshold on it (`einvoice_reporting_window_days`, `ewaybill_threshold`) is org-configuration, never a hard-coded constant — both numbers have moved by notification before.
- `models/account_mapping.py` — `TaxAccountMapping`: `(organization, component, direction) -> Account`. A table, not FK columns on `TaxProfile`, because absence must stay cheap — see INVARIANTS.
- `models/tax_rate.py` — `TaxRate`. Combined rate (18, not 9+9), effective-dated, no slab whitelist — the 0/5/12/18/28 -> 0/5/18/40 rate reform (22 Sep 2025) and the compensation-cess Nil-out (1 Feb 2026) are exactly the case effective dating exists for.
- `models/withholding.py` — `WithholdingSection`. TDS/TCS, separate from GST components — see INVARIANTS.
- `enums.py` — `SupplyType` (the six NIC `TranDtls.SupTyp` values, verbatim — not ours to extend), `SupplyNature` (the computed answer), `TaxTreatment`, `TaxComponent`, `TaxDirection`, `RegistrationType`, `WithholdingKind`/`WithholdingAppliesTo`.
- `services/validation.py` — `validate_gstin`/`validate_pan`. Pure functions (no DB required unless the optional state check is used) implementing the MOD-36 checksum; verified against three published GSTINs including the canonical `27AAPFU0939F1ZV`.
- `services/determination.py` — `determine_supply_nature`. The ONLY place IGST Act ss.7/8 are interpreted. Does NOT derive place of supply (ss.10-13) — see the module docstring for why that boundary is deliberate.
- `services/computation.py` — `split_tax`/`apply_tax_components`/`component_totals`/`compute_withholding`. The cent-exactness invariant (`cgst + sgst == tax_amount` always) is by construction: CGST is rounded, SGST is the remainder, never two independent halves.
- `services/party.py` — `clean_party_tax_fields`, shared by `sales.Customer` and `purchases.Vendor` because peer modules must not import each other (backend/CLAUDE.md) and the validation must not drift between two copies.
- `services/documents.py` — `resolve_document_tax`/`carry_forward_tax`. The backward-compatibility seam: an org with no `TaxProfile.state` gets `UNSPECIFIED` for everything, which is what every pre-Phase-7 document reads as.
- `services/posting.py` — `build_output_tax_lines`/`build_input_tax_lines`/`build_reverse_charge_lines`. Builds journal LINES only; `accounting.services.posting.post_journal` remains the only authoritative posting path. Falls back to the document's own `tax_payable_account`/`tax_recoverable_account` when components are unmapped or only partially mapped — see INVARIANTS.
- `selectors.py` — read-side resolution (`get_tax_profile`, `get_tax_account_map`, `resolve_rate_for_date`, ...). No authoritative number is computed here.
- `testing.py` — `ensure_state_codes()`, used by every app whose tests build a document that needs the state master.

DOES NOT OWN
- Place-of-supply determination logic beyond the s.7/s.8 inter-vs-intra comparison (deliberate — see `services/determination.py`).
- HSN/SAC catalog data (`items.HsnSacCode`), rate-slab whitelisting, TDS/TCS section rates, e-invoice applicability turnover thresholds — all organization-entered, per root CLAUDE.md rule 5.
- Filing a return or transmitting a payload to GSTN/NIC — see `compliance/CLAUDE.md`.

INVARIANTS
- `TaxAccountMapping` absence is a first-class, cheap state. `tax.services.posting` falls back to the document's own account when a component is unmapped — and, on a PARTIAL mapping, falls back for ALL components rather than splitting some to their own accounts and the rest to the catch-all, because a half-split would reconcile to neither. This is what lets every pre-Phase-7 organization keep posting byte-for-byte the same single-line journal they always did; it is asserted by 434 pre-existing sales/purchases tests requiring zero assertion changes.
- SGST and UTGST are ONE `TaxComponent` (`SGST_UTGST`), not two — the label a supply wears is a property of the destination `StateCode.uses_utgst`, not a separate tax with a different rate or ledger treatment.
- Reverse charge posts on the BILL only, never on the recurring-bill template's generation path implicitly — `is_reverse_charge` is a document field, carried forward on conversion via `carry_forward_tax`, never re-determined.
- `core.money.calculate_line` is unchanged and untouched by this phase — it remains pure, domain-free money math. The GST split is a separate step (`apply_tax_components`), always run AFTER it, never folded in.
- Every tenant-scoped model here has a matching RLS migration (`migrations/0002_enable_rls.py`) EXCEPT `StateCode`, which is deliberately global — see `tax/tests/test_rls.py::test_state_code_is_deliberately_global`.
- No class-level tenant-scoped `.objects.all()` querysets.

RBAC NOTE
`tax.manage_settings`/`tax.manage_rates` stop at Accountant — configuring the GST profile, the chart-of-accounts tax mapping, and the rate table decides how every future document is taxed, the same weight as `MANAGE_ACCOUNTING`. `view_*` reaches Staff and Viewer. See `authz/roles.py` and `authz/tests/test_roles.py::TaxCompliancePermissionTests`.

TEST GOTCHA
`TaxTestsBase`/`TaxTransactionTestsBase` follow the `PurchasesFixtureMixin` split (`purchases/CLAUDE.md`). Additionally: any `TransactionTestCase` anywhere in a test RUN flushes `tax_statecode`, and Django does NOT re-run data migrations after a flush — so every fixture that resolves a `StateCode` calls `tax.testing.ensure_state_codes()` in `setUp`, which is a cheap no-op once the master is present. Skipping this call is how a state lookup silently starts failing in a test class that runs after an unrelated `TransactionTestCase` elsewhere in the suite.

READ FIRST
- `services/determination.py`, `services/computation.py`, `services/posting.py`, `models/state.py`

TOKEN DISCIPLINE
- Do not duplicate root or `core`/`accounting`/`items` CLAUDE.md content here.
