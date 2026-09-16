# COMPLIANCE

PURPOSE
Output/input tax registers, GSTR-1 and GSTR-3B return summaries, and e-Invoice (IRN)/e-Way Bill generation — all derived from posted `sales`/`purchases` documents and the `tax` engine's component split. Phase 7 complete: registers and return selectors, `EInvoiceDocument`/`EWayBill` models with immutable provider-reported fields, verified INV-01 (schema v1.1) and Generate-EWB (API v1.03) payload builders, a `manual` provider for both (no NIC HTTP client — see PROVIDERS below), and the RLS/concurrency/workflow-e2e acceptance gate.

OWNS
- `selectors.py` — `get_output_tax_register`/`get_input_tax_register`/`get_gstr1_summary`/`get_gstr3b_summary`. ALL derived on demand from posted `Invoice`/`CreditNote`/`Bill` rows — nothing here is stored, for the same reason `accounting/selectors.py` derives the General Ledger and Trial Balance rather than caching them: a cached return figure is one that can silently disagree with the documents behind it.
- `models/einvoice.py` — `EInvoiceDocument`. Points at exactly one of `Invoice`/`CreditNote` (`CheckConstraint`). IRP-reported fields (`irn`, `ack_no`, `ack_date`, `signed_invoice`, `signed_qr_code`) are FROZEN after first write — same treatment as `banking.BankTransaction._BANK_REPORTED_FIELDS`. Never hard-deleted (`delete()` raises); a cancelled filing is itself an audit fact.
- `models/ewaybill.py` — `EWayBill`. Points at exactly one of `Invoice`/`DeliveryChallan`. `valid_until` is STORED (computed once at generation, from the portal's response when available, from `compute_validity` otherwise) rather than recomputed on read — what governs a consignment on the road is the validity actually issued, not a later recalculation from an edited distance.
- `services/einvoice_payload.py` — `build_irn_payload`. INV-01 schema `Version` "1.1", verified against einv-apisandbox.nic.in 2026-09-15 (`docs/gst-research.md`). Validates BEFORE building (`supply_type` must be one of the six reportable NIC values — `UNSPECIFIED` must never reach a payload).
- `services/ewaybill_payload.py` — `build_ewb_payload`/`compute_validity`/`requires_ewaybill`. Validity = 1 day per 200 km, part thereof rounding UP; 4000 km ceiling; threshold read from `tax.TaxProfile.ewaybill_threshold`, never hard-coded (states set their own intra-State figure).
- `services/einvoice.py`/`services/ewaybill.py` — `prepare_*`/`record_*`/`generate_*`/`record_*_cancellation`. Idempotent by construction on `status` under `select_for_update()`, PLUS an `IntegrityError`-catch-and-reread fallback for the concurrent FIRST-INSERT race a lock cannot prevent (there is nothing to lock before the row exists) — see the module docstrings and `tests/test_concurrency.py`.
- `providers/` — `EInvoiceProvider`/`EWayBillProvider` ABCs + registry, mirroring `banking/providers/` exactly. `manual.py` registers a null-object provider for each (`supports_generate() -> False`) at import time.

DOES NOT OWN
- Determining supply nature or splitting tax components — see `tax/CLAUDE.md`.
- `projects`, `banking` — peer modules; `compliance` must not import either (backend/CLAUDE.md module map).
- Filing a return or transmitting an e-Invoice/e-Way Bill payload to a government server. See PROVIDERS below.

PROVIDERS — why there is no NIC HTTP client
Reporting to the IRP or generating an e-way bill means an authenticated call using credentials issued to a registered taxpayer or GSP, wrapped in a session-key/RSA handshake this codebase has no way to test. Root CLAUDE.md rule 5 forbids inventing an API; a client written from documentation alone would look finished, pass its own mocked tests, and fail the first real filing. So this phase ships everything that does NOT need those credentials — the verified payload builders, the storage/immutability model, the validity arithmetic, and the provider registry — and the `manual` provider closes the loop for a real user today (generate on the portal, record what came back via `record_irn`/`record_ewaybill`). A live integration is a new provider class plus a registry entry; the payload builders and models it would use are already proven. Same reasoning, same shape, as `banking/providers/base.py`'s absent Plaid/Yodlee clients.

INVARIANTS
- Nothing in `selectors.py` is AI-assisted, and it must never become so — GSTR-1/3B figures are authoritative tax numbers, which root CLAUDE.md's pipeline rule reserves for deterministic code.
- `EInvoiceDocument`/`EWayBill` are append-only in the sense that mirrors `accounting.JournalEntry`: IRP/portal-reported fields never change once set (`save()` raises), and rows are never deleted (`delete()` raises) — cancel instead, which is itself recorded.
- A duplicate IRN/EWB number is not a duplicate row to clean up — it is a second government filing. `record_irn`/`record_ewaybill` refuse a second, DIFFERENT identifier for one document; recording the SAME one twice is a no-op.
- GSTR-3B's reverse-charge figures are intentionally counted twice: once in `outward.inward_reverse_charge` (the liability, computed from BILLS under IGST Act s.5(3)/(4)) and once in `itc.reverse_charge` (the matching credit). That is what reverse charge does — do not "simplify" this to one figure.
- Every tenant-scoped model here has a matching RLS migration (`migrations/0002_enable_rls.py`) — an IRN/EWB number and its payload are among the most sensitive data in the system (customer list, amounts, GSTINs, government-issued identifiers).

TEST GOTCHA
`compliance/tests/base.py` builds fixtures by calling the REAL `sales`/`purchases` posting services (`post_invoice`, `post_bill`), never by writing rows straight into the tables — every selector here derives its figures from posted documents, so a register test against hand-built rows would prove nothing about what the posting services actually write. See `tax/CLAUDE.md`'s TEST GOTCHA for the `ensure_state_codes()` requirement, which applies here too.

READ FIRST
- `selectors.py`, `services/einvoice_payload.py`, `services/ewaybill_payload.py`, `providers/base.py`

TOKEN DISCIPLINE
- Do not duplicate root, `core`, `tax`, `sales` or `purchases` CLAUDE.md content here.
