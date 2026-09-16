"""Local filesystem storage backend — the dev/test default (no S3-compatible
service is provisioned in `infrastructure/docker-compose.yml` yet).

Files are written under a private directory NOT served by Django's static/media
machinery, so there is no path that exposes them without going through
`get_signed_url` + the proxied download view (documents/api/views.py). The
"signed URL" here is a short-lived, tamper-evident token (`django.core.signing`)
that the same process verifies — the local equivalent of an S3 presigned URL,
not a real network-servable link on its own.
"""

from django.conf import settings
from django.core import signing
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage

from documents.storage.base import DocumentStorage

SIGNING_SALT = "documents.storage.local"


class LocalDocumentStorage(DocumentStorage):
    def __init__(self):
        self._fs = FileSystemStorage(location=settings.DOCUMENT_LOCAL_STORAGE_ROOT, base_url=None)

    def put(self, *, key: str, content: bytes, content_type: str) -> None:
        self._fs.save(key, ContentFile(content))

    def get_signed_url(self, *, key: str, expires_in: int) -> str:
        if not self._fs.exists(key):
            raise FileNotFoundError(key)
        token = signing.dumps({"key": key}, salt=SIGNING_SALT)
        return f"/api/v1/documents/local-storage/{token}/"

    def delete(self, *, key: str) -> None:
        if self._fs.exists(key):
            self._fs.delete(key)

    def exists(self, *, key: str) -> bool:
        return self._fs.exists(key)

    def get_bytes(self, *, key: str) -> bytes:
        with self._fs.open(key, "rb") as fh:
            return fh.read()

    @staticmethod
    def verify_token(token: str, *, max_age: int) -> str:
        """Returns the storage key encoded in a signed URL token, or raises
        `signing.BadSignature`/`signing.SignatureExpired`."""
        return signing.loads(token, salt=SIGNING_SALT, max_age=max_age)["key"]
