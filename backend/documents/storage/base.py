"""Storage backend interface. The domain layer (services/api) must only ever
talk to `DocumentStorage` — never to `boto3`, `django.core.files.storage`, or
a raw filesystem path directly. See documents/CLAUDE.md."""

from abc import ABC, abstractmethod


class DocumentStorage(ABC):
    @abstractmethod
    def put(self, *, key: str, content: bytes, content_type: str) -> None:
        """Writes `content` to `key`. Overwrites are never expected — callers
        generate a fresh, collision-resistant key per upload."""

    @abstractmethod
    def get_signed_url(self, *, key: str, expires_in: int) -> str:
        """Returns a time-limited URL for reading `key`. Never a permanent
        public URL. `expires_in` is in seconds."""

    @abstractmethod
    def delete(self, *, key: str) -> None:
        """Permanently removes the stored object. Callers should prefer
        archiving the Document row over calling this for anything that may be
        referenced by a financial record (see root CLAUDE.md rule 12)."""

    @abstractmethod
    def exists(self, *, key: str) -> bool:
        """Metadata lookup — used to detect a DB row with no backing object
        (see documents/CLAUDE.md storage-failure handling)."""

    @abstractmethod
    def get_bytes(self, *, key: str) -> bytes:
        """Reads the full object back. Used server-side only (OCR, the local
        backend's download-proxy view) — client downloads always go through
        `get_signed_url`, never this method directly."""
