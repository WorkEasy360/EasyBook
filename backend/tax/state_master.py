"""The GST state/UT code master, as published data.

SOURCE: NIC e-Invoice master codes, einvoice1.gst.gov.in/Others/MasterCodes,
checked 2026-09-15. See docs/gst-research.md.

This lives in a plain module rather than inline in the seed migration because
two callers need it: `migrations/0003_seed_state_codes.py`, and
`tax.testing.ensure_state_codes`, which restores the master after a
`TransactionTestCase` flush has truncated it (Django's flush does not re-run
data migrations, and the master must outlive every test in the run).

Importing a constant from app code into a migration follows the precedent
already set by `core.rls`, which every RLS migration imports.

NOTES ON THE DATA
- NIC still lists 25 (Daman and Diu) and 26 separately although the two Union
  Territories merged in 2020 and now register under 26. Both are kept: pre-2020
  documents carry 25 and must stay resolvable.
- 96/97/99 are reporting bucket codes, not places. `is_special` marks them so
  `services/determination.py` can refuse to treat one as a supplier's state.
- `uses_utgst` marks the Union Territories WITHOUT a legislature, whose state
  half is levied under the UTGST Act. Delhi (07), Puducherry (34) and
  Jammu & Kashmir (01) have legislatures and levy SGST - which is why this
  cannot be derived from `is_union_territory`.
"""

# (code, name, is_union_territory, uses_utgst, is_special)
STATE_CODES = [
    ("01", "Jammu and Kashmir", True, False, False),
    ("02", "Himachal Pradesh", False, False, False),
    ("03", "Punjab", False, False, False),
    ("04", "Chandigarh", True, True, False),
    ("05", "Uttarakhand", False, False, False),
    ("06", "Haryana", False, False, False),
    ("07", "Delhi", True, False, False),
    ("08", "Rajasthan", False, False, False),
    ("09", "Uttar Pradesh", False, False, False),
    ("10", "Bihar", False, False, False),
    ("11", "Sikkim", False, False, False),
    ("12", "Arunachal Pradesh", False, False, False),
    ("13", "Nagaland", False, False, False),
    ("14", "Manipur", False, False, False),
    ("15", "Mizoram", False, False, False),
    ("16", "Tripura", False, False, False),
    ("17", "Meghalaya", False, False, False),
    ("18", "Assam", False, False, False),
    ("19", "West Bengal", False, False, False),
    ("20", "Jharkhand", False, False, False),
    ("21", "Odisha", False, False, False),
    ("22", "Chhattisgarh", False, False, False),
    ("23", "Madhya Pradesh", False, False, False),
    ("24", "Gujarat", False, False, False),
    ("25", "Daman and Diu", True, True, False),
    ("26", "Dadra and Nagar Haveli and Daman and Diu", True, True, False),
    ("27", "Maharashtra", False, False, False),
    ("29", "Karnataka", False, False, False),
    ("30", "Goa", False, False, False),
    ("31", "Lakshadweep", True, True, False),
    ("32", "Kerala", False, False, False),
    ("33", "Tamil Nadu", False, False, False),
    ("34", "Puducherry", True, False, False),
    ("35", "Andaman and Nicobar Islands", True, True, False),
    ("36", "Telangana", False, False, False),
    ("37", "Andhra Pradesh", False, False, False),
    ("38", "Ladakh", True, True, False),
    ("96", "Other Country", False, False, True),
    ("97", "Other Territory", False, False, True),
    ("99", "Other Country (OIDAR)", False, False, True),
]

STATE_CODE_VALUES = [row[0] for row in STATE_CODES]
