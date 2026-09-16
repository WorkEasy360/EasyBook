import itertools

from django.test import SimpleTestCase

from ai.config import ChunkingConfig
from ai.rag.chunking import chunk_text, normalize_text

CONFIG = ChunkingConfig(target_chars=300, overlap_chars=60, max_chars=500)

CONTRACT = """MASTER SERVICES AGREEMENT

1. Term
This agreement starts on the effective date and continues for twelve months. It renews automatically unless either party gives notice.

2. Termination
Either party may terminate this agreement with thirty days written notice. Termination for material breach may be immediate if the breach is not cured within ten days.

3. Payment Terms:
Invoices are payable within forty five days. Late payments accrue interest as permitted by law.
\f
SCHEDULE A

Fees are listed per service line. The vendor GSTIN is 27AAPFU0939F1ZV and invoices reference INV-1024 style numbers.
"""


class ChunkingTests(SimpleTestCase):
    def test_chunking_is_deterministic(self):
        normalized = normalize_text(CONTRACT)
        self.assertEqual(chunk_text(normalized, CONFIG), chunk_text(normalize_text(CONTRACT), CONFIG))

    def test_every_chunk_is_an_exact_slice_within_bounds(self):
        normalized = normalize_text(CONTRACT)
        chunks = chunk_text(normalized, CONFIG)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertEqual(chunk.text, normalized[chunk.char_start:chunk.char_end])
            self.assertLessEqual(len(chunk.text), CONFIG.max_chars)
            self.assertTrue(chunk.text.strip())
        self.assertEqual([c.index for c in chunks], list(range(len(chunks))))

    def test_page_numbers_follow_form_feeds_and_chunks_never_span_pages(self):
        normalized = normalize_text(CONTRACT)
        chunks = chunk_text(normalized, CONFIG)
        self.assertEqual({c.page_number for c in chunks}, {1, 2})
        for chunk in chunks:
            self.assertNotIn("\f", chunk.text)
        schedule = [c for c in chunks if "27AAPFU0939F1ZV" in c.text]
        self.assertEqual(len(schedule), 1)
        self.assertEqual(schedule[0].page_number, 2)
        self.assertEqual(schedule[0].section, "SCHEDULE A")

    def test_unpaged_text_has_no_page_number(self):
        chunks = chunk_text(normalize_text("Just one paragraph of text."), CONFIG)
        self.assertEqual(len(chunks), 1)
        self.assertIsNone(chunks[0].page_number)

    def test_sections_are_detected_from_headings(self):
        chunks = chunk_text(normalize_text(CONTRACT), CONFIG)
        termination = [c for c in chunks if "thirty days written notice" in c.text]
        self.assertTrue(termination)
        self.assertIn(termination[0].section, {"2. Termination", "1. Term", "MASTER SERVICES AGREEMENT"})

    def test_overlap_repeats_tail_of_previous_chunk_on_a_word_boundary(self):
        text = normalize_text("\n\n".join(f"Paragraph {i} " + "word " * 40 for i in range(6)))
        chunks = chunk_text(text, CONFIG)
        self.assertGreater(len(chunks), 1)
        for previous, current in itertools.pairwise(chunks):
            self.assertLess(current.char_start, previous.char_end)
            self.assertTrue(current.char_start == 0 or text[current.char_start - 1].isspace())

    def test_single_oversized_sentence_is_hard_split(self):
        text = normalize_text("x" * 1300)
        chunks = chunk_text(text, CONFIG)
        self.assertGreater(len(chunks), 2)
        self.assertTrue(all(len(c.text) <= CONFIG.max_chars for c in chunks))

    def test_normalization(self):
        self.assertEqual(normalize_text("a\r\n\r\n\r\n\r\nb   c\x00"), "a\n\nb c")
        self.assertEqual(normalize_text(""), "")
