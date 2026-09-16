"""The seam between this system and the government portals.

WHY THERE IS ONLY A MANUAL PROVIDER
-----------------------------------
Reporting an invoice to the IRP or generating an e-way bill means an
authenticated call to a NIC endpoint: an auth token obtained with credentials
issued to a registered taxpayer or GSP, and a payload wrapped in a
session-key/RSA handshake. This repository has none of those credentials and no
sandbox to test against, and root CLAUDE.md rule 5 forbids inventing an API -
a client written from guesswork would look finished, pass its own mocked
tests, and fail on the first real filing.

So what ships is everything that does NOT depend on those credentials: the
verified payload builders, the IRN/EWB storage with its immutability rules, the
validity arithmetic, and this registry. The `manual` provider closes the loop
for a real user today - they generate on the portal and record what came back,
which is what most small taxpayers do anyway - and a future integration is a
new class plus a registry entry, with the payload builders and models already
proven.

This is the same reasoning, and the same shape, as `banking/providers/base.py`
and its absent Plaid/Yodlee clients.

CONTRACT: implementations must NOT write to the database. Persistence, status
transitions and audit belong to `services/einvoice.py` and
`services/ewaybill.py`, so every provider inherits identical guarantees.
"""

import abc
from dataclasses import dataclass, field


@dataclass(frozen=True)
class IrnResult:
    """What an IRP returns for a successful IRN generation. Field names mirror
    the NIC response (Irn, AckNo, AckDt, SignedInvoice, SignedQRCode)."""

    irn: str
    ack_no: str = ""
    ack_date=None
    signed_invoice: str = ""
    signed_qr_code: str = ""
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EWayBillResult:
    """What the e-way bill portal returns. `valid_until` comes FROM the portal
    rather than from our own arithmetic - `compute_validity` predicts it, the
    portal decides it."""

    ewb_no: str
    ewb_date=None
    valid_until=None
    raw: dict = field(default_factory=dict)


class EInvoiceProvider(abc.ABC):
    key: str = ""

    @abc.abstractmethod
    def generate_irn(self, *, payload: dict) -> IrnResult:
        """Reports one document and returns the IRP's answer.

        Implementations must NOT touch the database.
        """

    @abc.abstractmethod
    def cancel_irn(self, *, irn: str, reason: str) -> dict:
        """Cancels a reported IRN. The IRP allows this only within a limited
        window after generation; enforcing that window is the portal's job, not
        ours, so this does not second-guess it."""

    def supports_generate(self) -> bool:
        """False for a provider that cannot reach a portal itself, so callers
        can tell "not configured" from "failed"."""
        return True


class EWayBillProvider(abc.ABC):
    key: str = ""

    @abc.abstractmethod
    def generate_ewaybill(self, *, payload: dict) -> EWayBillResult:
        """Implementations must NOT touch the database."""

    @abc.abstractmethod
    def cancel_ewaybill(self, *, ewb_no: str, reason: str) -> dict:
        ...

    def supports_generate(self) -> bool:
        return True


_EINVOICE_REGISTRY: dict[str, EInvoiceProvider] = {}
_EWAYBILL_REGISTRY: dict[str, EWayBillProvider] = {}


def register_einvoice_provider(provider: EInvoiceProvider) -> None:
    _EINVOICE_REGISTRY[provider.key] = provider


def register_ewaybill_provider(provider: EWayBillProvider) -> None:
    _EWAYBILL_REGISTRY[provider.key] = provider


def get_einvoice_provider(key: str) -> EInvoiceProvider | None:
    return _EINVOICE_REGISTRY.get(key)


def get_ewaybill_provider(key: str) -> EWayBillProvider | None:
    return _EWAYBILL_REGISTRY.get(key)


def available_einvoice_providers() -> list[str]:
    return sorted(_EINVOICE_REGISTRY)


def available_ewaybill_providers() -> list[str]:
    return sorted(_EWAYBILL_REGISTRY)
