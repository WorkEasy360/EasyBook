# GST / e-Invoice / e-Way Bill research — Phase 7

Cross-module: this backs decisions in `backend/tax/`, `backend/compliance/`, and touches `backend/sales/`, `backend/purchases/`, `backend/items/`. Each module's own CLAUDE.md states the resulting rule; this document exists to carry the citations root CLAUDE.md's VERIFICATION RULE and `docs/CLAUDE.md` require for GST research, in one place rather than scattered across module docs.

All facts below were checked against the source cited on **2026-09-15**. GST rates, thresholds and schema versions change by notification — re-verify before relying on any figure here after a significant time gap, and especially before any of the "known upcoming change" dates listed.

---

## 1. Inter-State vs intra-State supply (IGST Act ss.7, 8, 5)

**Source:** CBIC tax information portal, IGST Act 2017 full text — `taxinformation.cbic.gov.in` (chapter IV s.7, chapter V s.10) and `cbic-gst.gov.in/hindi/IGST-bill-e.html` (ss.5, 7, 8 summary), checked 2026-09-15.

- **s.7(1)/(3):** a supply of goods or services is INTER-STATE where the location of the supplier and the place of supply are in two different States, two different Union territories, or a State and a Union territory.
- **s.8(1)/(2):** a supply is INTRA-STATE where the location of the supplier and the place of supply are in the SAME State or Union territory — **EXCEPT**:
  - supply to or by a Special Economic Zone developer or unit (remains inter-State, IGST, even within one state);
  - goods imported into India until they cross the customs frontier;
  - supply to a tourist (s.15).
- **s.5(3):** reverse charge — government may notify categories where the recipient pays tax instead of the supplier.
- **s.5(4):** reverse charge — an unregistered supplier to a registered recipient shifts liability to the recipient.

**Implementation:** `tax/services/determination.py::determine_supply_nature`. The SEZ exception is the case a bare `supplier_state == place_of_supply` comparison gets wrong — covered explicitly in `tax/tests/test_determination.py::test_sez_in_the_same_state_is_still_inter_state`.

**Deliberately not implemented:** the place-of-supply clause trees themselves (ss.10–13), which turn on facts (goods movement, delivery instructions, service performance location) this system does not hold. Place of supply is a document field, defaulted and user-overridable — see `tax/CLAUDE.md`.

---

## 2. GSTIN structure and check-digit algorithm

**Source:** GSTIN structure is publicly documented (15 characters: 2-digit state code, 10-character PAN, entity code, fixed "Z", 1 check character). The check-digit algorithm (MOD-36 / Luhn-mod-36 family, alternating-weight scheme over a 36-character alphabet) was implemented and independently VERIFIED by computing it against three published, well-formed GSTINs and confirming a match — including `27AAPFU0939F1ZV`, the canonical example used throughout Indian GST documentation. Checked 2026-09-15.

**Implementation:** `tax/services/validation.py::compute_gstin_check_character`, `validate_gstin`. Test vectors in `tax/tests/test_gstin.py`.

**Note:** validation is structural only — it proves a GSTIN is well-formed, not that the registration is active. Only the GST portal (`services.gst.gov.in`) can confirm that, and this codebase has no credentials to query it (root CLAUDE.md rule 5).

---

## 3. GST state code master

**Source:** NIC e-Invoice master codes, `einvoice1.gst.gov.in/Others/MasterCodes`, checked 2026-09-15.

38 active codes (01–38, with 25/28 historical/discontinued as the merged/reorganized states now register under different codes) plus special reporting buckets 96 ("Other Country"), 97 ("Other Territory"), 99 ("Other Country (OIDAR)"). Reproduced verbatim in `backend/tax/state_master.py`, including the fact that NIC's own master still lists code 25 (Daman and Diu) separately from 26 (the current Dadra and Nagar Haveli and Daman and Diu) even though the two merged in 2020 — kept because pre-2020 documents carry 25 and must stay resolvable.

`uses_utgst` (whether the state-half of an intra-State supply is levied as UTGST rather than SGST) is not part of the NIC master; it is a general-knowledge fact about which Union Territories lack their own legislature (Chandigarh, Lakshadweep, Andaman & Nicobar, Dadra & Nagar Haveli/Daman & Diu, Ladakh — UTGST; Delhi, Puducherry, Jammu & Kashmir — SGST, despite being UTs, because they have legislatures).

**Implementation:** `tax/models/state.py::StateCode`, seeded by `tax/migrations/0003_seed_state_codes.py` from `tax/state_master.py`.

---

## 4. e-Invoice (IRN) schema and API

**Source:** NIC e-Invoice API developer sandbox, `einv-apisandbox.nic.in`, checked 2026-09-15.

- Payload schema `Version`: **"1.1"**.
- API versions: **v1.03** for the core APIs (Generate IRN, Cancel IRN, Get IRN Details), **v1.04** for the vital APIs (Authentication, Get GSTIN Details).
- Top-level payload groups: `Version`, `TranDtls`, `DocDtls`, `SellerDtls`, `BuyerDtls`, `ItemList`, `ValDtls` (mandatory), plus optional `DispDtls`, `ShipDtls`, `PayDtls`, `RefDtls`, `AddlDocDtls`, `ExpDtls`, `EwbDtls`.
- `TranDtls.SupTyp` ∈ `{B2B, SEZWP, SEZWOP, EXPWP, EXPWOP, DEXP}`. `RegRev`, `IgstOnIntra` ∈ `{Y, N}`.
- `DocDtls.Typ` ∈ `{INV, CRN, DBN}`.
- Successful response carries `Irn` (64-char), `AckNo`, `AckDt`, `SignedInvoice`, `SignedQRCode`, `Status`.
- **30-day reporting window** effective 2025-04-01 (GSTN advisory dated 2024-11-05) for most taxpayers; **3-day window** for taxpayers with aggregate annual turnover above ₹10 crore. Both are configurable per organization (`tax.TaxProfile.einvoice_reporting_window_days`), never hard-coded — the applicability turnover threshold itself has moved multiple times (most recently reported toward ₹2 crore from October 2025) and is not encoded in this codebase at all, per root CLAUDE.md rule 5.

**Implementation:** `compliance/services/einvoice_payload.py::build_irn_payload`. No NIC HTTP client — see `compliance/CLAUDE.md`'s PROVIDERS section for why.

---

## 5. e-Way Bill schema and API

**Source:** NIC e-Way Bill API developer portal, `docs.ewaybillgst.gov.in/apidocs/version1.03/`, checked 2026-09-15.

- API version **1.03**.
- Consignment-value threshold: **₹50,000** (all-India default; several states set a different figure for intra-State movement — configurable via `tax.TaxProfile.ewaybill_threshold`, never hard-coded).
- Validity: **one day per 200 km**, with any part of a further 200 km adding one more day (e.g. 200 km → 1 day; 201 km → 2 days).
- Maximum permitted distance in the Generate EWB request: **4000 km**.
- `vehicleType` ∈ `{R (Regular), O (Over-Dimensional Cargo)}`, meaningful only alongside a vehicle number.

**Implementation:** `compliance/services/ewaybill_payload.py::build_ewb_payload`/`compute_validity`. Validity arithmetic unit-tested at the 200/201/400/401 km boundaries in `compliance/tests/test_ewaybill_payload.py`.

---

## 6. GST rate structure and rate reform timeline

**Source:** PIB press releases and GST Council 56th meeting documents, `pib.gov.in` and `gstcouncil.gov.in`, checked 2026-09-15.

- Effective **22 September 2025**: GST rate slabs simplified from {0, 5, 12, 18, 28}% to **{0, 5, 18}%** plus a **40%** rate for luxury/sin goods (pan masala, tobacco products other than cigarettes/bidi at first, aerated drinks, high-end cars, yachts, private aircraft).
- Effective **1 February 2026**: bidi moved to the 18% slab; pan masala, remaining tobacco forms, cigarettes and nicotine inhalation products moved to 40%; compensation cess on these was withdrawn to **Nil** (the loan/interest liabilities behind the original cess having been discharged).
- Compensation cess otherwise LARGELY ABOLISHED as part of the September 2025 reform, with the tobacco-category exception above continuing on the pre-reform basis until the February 2026 notification.

**Implementation:** `tax.TaxRate` has NO slab whitelist and no hard-coded rate list — `rate`/`cess_rate` are freeform org-entered Decimals with effective-dating (`effective_from`/`effective_to`), specifically because this timeline shows the slabs changing twice within one financial year. See `tax/models/tax_rate.py` docstring.

---

## Deliberately unresearched / unimplemented (root CLAUDE.md rule 5)

- **HSN/SAC catalog data** — no master list is seeded; `items.HsnSacCode` remains manual-entry only, per the precedent already set before this phase.
- **TDS/TCS section rates and thresholds** (194Q, 206C(1H), etc.) — these are Finance Act figures that change annually; `tax.WithholdingSection.rate`/`threshold_amount` are organization-entered with no defaults.
- **e-Invoice/e-Way Bill applicability turnover thresholds** — actively moving (most recently reported toward ₹2 crore from October 2025); not encoded anywhere in this codebase. `tax.TaxProfile.einvoice_enabled` is an explicit organization opt-in, not threshold-derived.
- **The NIC authentication/encryption handshake** (session key exchange, RSA/AES payload wrapping) for either portal — no credentials exist in this repository to test against, and root CLAUDE.md rule 5 forbids inventing an API from documentation alone. See `compliance/CLAUDE.md`'s PROVIDERS section.
