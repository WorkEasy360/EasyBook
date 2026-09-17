"""Upload validation: extension + MIME allowlist, magic-byte signature check,
size limits, filename sanitization. Never trusts the client `Content-Type`
header alone (phase section 4).

Scope is deliberately minimal (phase section 4: "Keep scope minimal") — add a
type only when a real document category needs it.
"""

import hashlib
import re
from pathlib import PurePosixPath

from django.conf import settings

from core.exceptions import ApplicationError

# extension -> (canonical mime type, size category, magic-byte signature or
# None when the format has no reliable signature to sniff).
ALLOWED_TYPES = {
    ".pdf": ("application/pdf", "pdf", b"%PDF-"),
    ".png": ("image/png", "image", b"\x89PNG\r\n\x1a\n"),
    ".jpg": ("image/jpeg", "image", b"\xff\xd8\xff"),
    ".jpeg": ("image/jpeg", "image", b"\xff\xd8\xff"),
    ".csv": ("text/csv", "default", None),
    ".txt": ("text/plain", "default", None),
}

_SAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
MAX_FILENAME_LENGTH = 200


def sanitize_filename(filename: str) -> str:
    """Strips any directory component and normalizes to a safe character
    set — the filename is client-supplied and must never be trusted as a
    path (phase section 4: "filename sanitization")."""
    name = PurePosixPath(filename.replace("\\", "/")).name
    name = _SAFE_FILENAME_CHARS.sub("_", name).strip("._") or "file"
    if len(name) > MAX_FILENAME_LENGTH:
        stem, dot, ext = name.rpartition(".")
        keep = MAX_FILENAME_LENGTH - len(ext) - 1 if dot else MAX_FILENAME_LENGTH
        name = (stem[:keep] + dot + ext) if dot else name[:MAX_FILENAME_LENGTH]
    return name


def compute_checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def max_upload_size(filename: str) -> int:
    """Resolves the size ceiling for `filename`'s extension without reading
    any file content — callable against `UploadedFile.size` (populated by
    Django's multipart parser without materializing the file) so an oversized
    upload is rejected before `DocumentUploadView.post` ever calls `.read()`
    and pulls the whole thing into memory. An unrecognized extension still
    gets the "default" ceiling here (not "no limit") — validate_upload()
    rejects it as unsupported either way, but that rejection must not require
    reading an arbitrarily large file into memory first."""
    extension = PurePosixPath(sanitize_filename(filename)).suffix.lower()
    entry = ALLOWED_TYPES.get(extension)
    size_category = entry[1] if entry else "default"
    return settings.DOCUMENT_MAX_UPLOAD_SIZES.get(size_category, settings.DOCUMENT_MAX_UPLOAD_SIZES["default"])


def validate_upload(*, filename: str, content: bytes, declared_content_type: str = "") -> tuple[str, str, str]:
    """Returns (extension, canonical_mime_type, sanitized_filename) or raises
    `ApplicationError` with a stable `code` for every rejection reason."""
    if not content:
        raise ApplicationError("Uploaded file is empty.", code="file_empty")

    safe_filename = sanitize_filename(filename)
    extension = PurePosixPath(safe_filename).suffix.lower()
    entry = ALLOWED_TYPES.get(extension)
    if entry is None:
        raise ApplicationError(
            f"File type {extension or '(none)'} is not allowed.", code="unsupported_file_type"
        )
    mime_type, size_category, signature = entry

    if declared_content_type and declared_content_type.split(";")[0].strip().lower() != mime_type:
        raise ApplicationError(
            "Declared content type does not match the file extension.", code="mime_mismatch"
        )

    if signature is not None and not content.startswith(signature):
        raise ApplicationError(
            "File signature does not match its declared type.", code="file_signature_mismatch"
        )

    max_size = settings.DOCUMENT_MAX_UPLOAD_SIZES.get(size_category, settings.DOCUMENT_MAX_UPLOAD_SIZES["default"])
    if len(content) > max_size:
        raise ApplicationError(
            f"File exceeds the maximum allowed size of {max_size} bytes.", code="file_too_large"
        )

    return extension, mime_type, safe_filename
