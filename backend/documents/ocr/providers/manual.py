"""Manual/local OCR provider — the only provider implemented for Phase 9.

It does NOT perform text recognition. There is no OCR engine, credential, or
verified third-party integration available in this environment, and root
CLAUDE.md rule 5/the phase spec (section 11: "Do not fabricate external API
verification") forbid claiming one works without being able to prove it.

It always returns zero confidence and an empty extraction, which
`services/ocr.py` routes straight to NEEDS_REVIEW — every document a human
must key in by hand until a real provider (Textract/Document AI/Azure) is
wired up behind the same `OCRProvider` interface.
"""

from documents.ocr.providers.base import ExtractionResult, OCRProvider


class ManualOCRProvider(OCRProvider):
    name = "manual"
    version = "1"

    def extract(self, *, content: bytes, mime_type: str) -> ExtractionResult:
        return ExtractionResult(
            provider=self.name, provider_version=self.version, raw_text="", fields={}, confidence=0.0,
        )
