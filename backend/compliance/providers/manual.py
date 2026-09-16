"""The manual provider: the operator uses the government portal, and records
here what it returned.

A null object, exactly like `banking.providers.manual.ManualImportProvider`.
Its job is to keep `provider_key` always resolvable and to make "we have no
automated integration" an explicit, queryable state rather than a null.

`supports_generate()` returns False, which is how `services/einvoice.py` knows
to route callers to `record_irn(...)` instead of attempting a portal call.
"""

from compliance.providers.base import (
    EInvoiceProvider,
    EWayBillProvider,
    register_einvoice_provider,
    register_ewaybill_provider,
)

_NO_PORTAL = (
    "The manual provider cannot reach the {portal}. Generate the document on the "
    "government portal and record the result with {recorder}."
)


class ManualEInvoiceProvider(EInvoiceProvider):
    key = "manual"

    def supports_generate(self) -> bool:
        return False

    def generate_irn(self, *, payload: dict):
        raise NotImplementedError(
            _NO_PORTAL.format(portal="IRP", recorder="compliance.services.einvoice.record_irn")
        )

    def cancel_irn(self, *, irn: str, reason: str):
        raise NotImplementedError(
            _NO_PORTAL.format(
                portal="IRP", recorder="compliance.services.einvoice.record_irn_cancellation"
            )
        )


class ManualEWayBillProvider(EWayBillProvider):
    key = "manual"

    def supports_generate(self) -> bool:
        return False

    def generate_ewaybill(self, *, payload: dict):
        raise NotImplementedError(
            _NO_PORTAL.format(
                portal="e-way bill portal", recorder="compliance.services.ewaybill.record_ewaybill"
            )
        )

    def cancel_ewaybill(self, *, ewb_no: str, reason: str):
        raise NotImplementedError(
            _NO_PORTAL.format(
                portal="e-way bill portal",
                recorder="compliance.services.ewaybill.record_ewaybill_cancellation",
            )
        )


# Import-time registration, as in banking/providers/manual.py. The package
# __init__ imports this module, so importing anything from the package
# populates both registries.
register_einvoice_provider(ManualEInvoiceProvider())
register_ewaybill_provider(ManualEWayBillProvider())
