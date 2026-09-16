"""OCR provider interface (phase section 11). `documents` must never couple
to one vendor — every provider (a future AWS Textract/Google Document AI/
Azure Document Intelligence integration included) implements this and
nothing else in the codebase depends on provider internals."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ExtractionResult:
    provider: str
    provider_version: str
    raw_text: str
    # {"field_name": {"value": ..., "confidence": 0.0-1.0}, ...}
    fields: dict = field(default_factory=dict)
    # Overall confidence, used only for NEEDS_REVIEW/COMPLETED routing
    # (services/ocr.py) — never to auto-post anything.
    confidence: float | None = None
    error_code: str = ""
    error_message: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.error_code)


class OCRProvider(ABC):
    name: str = "base"
    version: str = "0"

    @abstractmethod
    def extract(self, *, content: bytes, mime_type: str) -> ExtractionResult: ...
