from django.conf import settings

from documents.ocr.providers.base import OCRProvider


def get_provider() -> OCRProvider:
    backend = getattr(settings, "DOCUMENT_OCR_PROVIDER", "manual")
    if backend == "manual":
        from documents.ocr.providers.manual import ManualOCRProvider

        return ManualOCRProvider()
    raise ValueError(f"Unknown DOCUMENT_OCR_PROVIDER: {backend!r}")
