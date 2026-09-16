"""The only provider that exists: a human uploads a file.

It cannot pull, because there is nothing to pull from — the data arrives
through services/imports.py::import_statement_file. Registering it keeps
`BankAccount.provider_key` meaningful (there is always a resolvable provider)
rather than leaving the field decorative until a live feed appears.
"""

from banking.providers.base import BankFeedProvider, ParsedTransaction, register_provider


class ManualImportProvider(BankFeedProvider):
    key = "manual"

    def supports_pull(self) -> bool:
        return False

    def fetch_transactions(self, *, bank_account, since=None, until=None) -> list[ParsedTransaction]:
        raise NotImplementedError(
            "The manual provider has no feed to pull from; upload a statement file instead."
        )


register_provider(ManualImportProvider())
