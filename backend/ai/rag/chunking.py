"""Deterministic, provider-agnostic text chunking.

Pure functions: the same text + ChunkingConfig always yields the same
chunks, byte for byte (tested). Sizes are in CHARACTERS, not tokens —
token counts are model-specific and would make chunk boundaries (and so
the index content hash) change whenever the embedding model changes.
Defaults and their rationale: ai/CLAUDE.md "CHUNKING".

Boundaries, strongest first:
  1. pages   — `\\f` (form feed) separates pages in extracted text; a chunk
               never spans two pages, so a page citation is always exact.
  2. paragraphs (blank line)
  3. sentences
  4. hard split at a word boundary (only for a single oversized sentence)

Every chunk is an exact slice `normalized[char_start:char_end]` of the
normalized text, so its source location is reproducible.
"""

import re
import unicodedata
from dataclasses import dataclass

from ai.config import ChunkingConfig

_HEADING_CAPS = re.compile(r"^(\d+(\.\d+)*[.)]?\s+)?[A-Z][A-Z0-9 &/,'()\-]{2,}$")
_HEADING_NUMBERED = re.compile(r"^\d+(\.\d+)*[.)]?\s+[A-Z][^.!?]{1,70}$")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    page_number: int | None
    section: str
    char_start: int
    char_end: int


def _normalize_page(page: str) -> str:
    page = page.replace("\r\n", "\n").replace("\r", "\n")
    page = "".join(ch for ch in page if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in page.split("\n")]
    page = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", page).strip()


def normalize_text(text: str) -> str:
    """NFC, unified newlines, control characters removed, runs of spaces
    collapsed, at most one blank line between paragraphs. Form feeds (page
    breaks) are preserved as the page separator."""
    text = unicodedata.normalize("NFC", text or "")
    pages = [_normalize_page(page) for page in text.split("\f")]
    # Drop trailing empty pages (a trailing form feed is common in PDF text).
    while len(pages) > 1 and not pages[-1]:
        pages.pop()
    return "\f".join(pages).strip("\n ")


def _is_heading(paragraph: str) -> bool:
    if "\n" in paragraph or len(paragraph) > 80:
        return False
    if paragraph.startswith("#"):
        return True
    if paragraph.endswith(":") and len(paragraph) <= 60:
        return True
    return bool(_HEADING_CAPS.match(paragraph) or _HEADING_NUMBERED.match(paragraph))


def _heading_label(paragraph: str) -> str:
    return paragraph.lstrip("#").strip().rstrip(":").strip()[:255]


def _paragraph_spans(text: str, start: int, end: int):
    position = start
    for match in re.finditer(r"\n\n", text[start:end]):
        paragraph_end = start + match.start()
        if paragraph_end > position:
            yield position, paragraph_end
        position = start + match.end()
    if position < end:
        yield position, end


def _split_oversized(text: str, start: int, end: int, limit: int):
    """Sentence spans; any sentence still longer than `limit` is hard-split
    at the last space before the limit (or exactly at it if there is none)."""
    sentence_start = start
    boundaries = [start + m.end() for m in _SENTENCE_END.finditer(text[start:end])] + [end]
    for boundary in boundaries:
        span_start = sentence_start
        span_end = boundary
        while span_end - span_start > limit:
            cut = text.rfind(" ", span_start + 1, span_start + limit)
            cut = cut if cut > span_start else span_start + limit
            yield span_start, cut
            span_start = cut
            while span_start < span_end and text[span_start].isspace():
                span_start += 1
        if span_end > span_start:
            # Trim trailing whitespace that the sentence regex left attached.
            trimmed_end = span_end
            while trimmed_end > span_start and text[trimmed_end - 1].isspace():
                trimmed_end -= 1
            if trimmed_end > span_start:
                yield span_start, trimmed_end
        sentence_start = boundary


def chunk_text(normalized: str, config: ChunkingConfig) -> list[Chunk]:
    piece_limit = config.max_chars - config.overlap_chars
    pages = normalized.split("\f")
    paged = len(pages) > 1
    chunks: list[Chunk] = []

    page_start = 0
    for page_index, page in enumerate(pages):
        page_end = page_start + len(page)
        page_number = page_index + 1 if paged else None

        # (start, end, section_at_that_point)
        pieces: list[tuple[int, int, str]] = []
        section = ""
        for para_start, para_end in _paragraph_spans(normalized, page_start, page_end):
            paragraph = normalized[para_start:para_end]
            if _is_heading(paragraph):
                section = _heading_label(paragraph)
            if para_end - para_start > piece_limit:
                pieces.extend((s, e, section) for s, e in _split_oversized(normalized, para_start, para_end, piece_limit))
            else:
                pieces.append((para_start, para_end, section))

        previous_end = None
        cursor = 0
        while cursor < len(pieces):
            content_start, _, chunk_section = pieces[cursor]
            content_end = pieces[cursor][1]
            cursor += 1
            while cursor < len(pieces) and pieces[cursor][1] - content_start <= config.target_chars:
                content_end = pieces[cursor][1]
                cursor += 1

            chunk_start = content_start
            if previous_end is not None and config.overlap_chars > 0:
                overlap_start = max(page_start, previous_end - config.overlap_chars)
                # Start the overlap on a word boundary.
                while (
                    overlap_start > page_start
                    and overlap_start < previous_end
                    and not normalized[overlap_start - 1].isspace()
                ):
                    overlap_start += 1
                while overlap_start < previous_end and normalized[overlap_start].isspace():
                    overlap_start += 1
                if overlap_start < previous_end:
                    chunk_start = overlap_start

            chunks.append(
                Chunk(
                    index=len(chunks),
                    text=normalized[chunk_start:content_end],
                    page_number=page_number,
                    section=chunk_section,
                    char_start=chunk_start,
                    char_end=content_end,
                )
            )
            previous_end = content_end

        page_start = page_end + 1  # skip the \f separator
    return chunks
