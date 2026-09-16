"""Output guards applied to every model answer before it leaves the backend.

1. System-prompt leakage — any 8-word run copied from a runtime prompt
   blocks the answer (phase section 79: "no system prompt exposure").
2. Numeric grounding — every figure in the answer must appear in the data
   the backend supplied for THIS request (tool payloads, retrieved document
   text, the question itself). A figure that does not is, by definition,
   model arithmetic or invention (phase sections 33/35), and the answer is
   replaced with the authoritative figures rendered deterministically.
   Small integers (< 100) and years are exempt: they are day counts, list
   sizes and dates far more often than amounts. This is a heuristic backstop
   BEHIND the structured_data contract, not a proof — clients should display
   financial values from `structured_data`, never parse them out of prose.
3. Presentation safety — HTML tags and javascript:/data: links are removed;
   the answer is still untrusted Markdown that the frontend must sanitize.
"""

import re
from decimal import Decimal, InvalidOperation

_DATE_PATTERNS = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}\b"),
)
_NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")
_WORD = re.compile(r"[a-z0-9]+")
_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_UNSAFE_LINK = re.compile(r"\]\(\s*(?:javascript|data|vbscript):(?:[^()]|\([^()]*\))*\)", re.IGNORECASE)
SHINGLE_WORDS = 8


def _strip_dates(text: str) -> str:
    for pattern in _DATE_PATTERNS:
        text = pattern.sub(" ", text)
    return text


def _normalize_number(token: str) -> str | None:
    cleaned = token.replace(",", "").lstrip("-")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None
    normalized = format(value.normalize(), "f")
    return normalized


def numbers_in(text: str) -> set[str]:
    found = set()
    for token in _NUMBER.findall(_strip_dates(text)):
        normalized = _normalize_number(token)
        if normalized is not None:
            found.add(normalized)
    return found


def _exempt(normalized: str) -> bool:
    if "." in normalized:
        return False
    value = int(normalized)
    return value < 100 or 1900 <= value <= 2100


def ungrounded_numbers(answer: str, allowed_texts: list[str]) -> list[str]:
    allowed = set()
    for text in allowed_texts:
        allowed |= numbers_in(text)
    return sorted(n for n in numbers_in(answer) if not _exempt(n) and n not in allowed)


def leaks_prompt(answer: str, prompts: list[str]) -> bool:
    answer_words = _WORD.findall(answer.lower())
    if len(answer_words) < SHINGLE_WORDS:
        return False
    answer_shingles = {
        " ".join(answer_words[i:i + SHINGLE_WORDS]) for i in range(len(answer_words) - SHINGLE_WORDS + 1)
    }
    for prompt in prompts:
        words = _WORD.findall(prompt.lower())
        for i in range(len(words) - SHINGLE_WORDS + 1):
            if " ".join(words[i:i + SHINGLE_WORDS]) in answer_shingles:
                return True
    return False


def sanitize_markdown(answer: str) -> str:
    answer = _HTML_TAG.sub("", answer)
    return _UNSAFE_LINK.sub("]()", answer)
