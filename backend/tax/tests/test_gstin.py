"""GSTIN and PAN validation.

The three GSTINs used as positive fixtures are real, well-formed numbers whose
published check character this implementation reproduces independently.
`27AAPFU0939F1ZV` is the example used throughout GST documentation. Three
independent agreements is the evidence that the MOD-36 implementation is
correct: a wrong algorithm reproduces any given real check character with
probability 1/36, so three matches by chance is a 1-in-46656 event.
"""

from django.test import SimpleTestCase, TestCase

from core.exceptions import ApplicationError
from tax.services.validation import (
    compute_gstin_check_character,
    gstin_state_code,
    validate_gstin,
    validate_pan,
)
from tax.testing import ensure_state_codes

VALID_GSTINS = [
    "27AAPFU0939F1ZV",
    "29AAGCB7383J1Z4",
    "24AAACC1206D1ZM",
]


class GstinChecksumTests(SimpleTestCase):
    """No database - `validate_gstin` is a pure function by design, so that a
    data migration can call it in a loop without a query per row."""

    def test_known_gstins_reproduce_their_own_check_character(self):
        for gstin in VALID_GSTINS:
            with self.subTest(gstin=gstin):
                self.assertEqual(compute_gstin_check_character(gstin[:14]), gstin[14])

    def test_corrupting_any_character_breaks_the_checksum(self):
        # The point of a check digit is positional sensitivity: mutating any
        # one of the fourteen must change the fifteenth. A checksum that only
        # noticed some positions would pass typos in the others.
        gstin = VALID_GSTINS[0]
        for index in range(14):
            original = gstin[index]
            replacement = "1" if original != "1" else "2"
            mutated = gstin[:index] + replacement + gstin[index + 1 :]
            with self.subTest(index=index):
                self.assertNotEqual(compute_gstin_check_character(mutated[:14]), gstin[14])

    def test_blank_is_allowed(self):
        # Unregistered customers and vendors are ordinary. Blank means "not
        # recorded", which is not the same as invalid.
        self.assertEqual(validate_gstin(""), "")
        self.assertEqual(validate_pan(""), "")

    def test_value_is_normalized_to_uppercase(self):
        self.assertEqual(validate_gstin("27aapfu0939f1zv"), "27AAPFU0939F1ZV")
        self.assertEqual(validate_pan("aapfu0939f"), "AAPFU0939F")

    def test_wrong_length_rejected(self):
        with self.assertRaises(ApplicationError) as ctx:
            validate_gstin("27AAPFU0939F1Z")
        self.assertEqual(ctx.exception.get_codes(), "gstin_invalid_format")

    def test_fourteenth_character_must_be_z(self):
        broken = VALID_GSTINS[0][:13] + "X" + VALID_GSTINS[0][14]
        with self.assertRaises(ApplicationError) as ctx:
            validate_gstin(broken)
        self.assertEqual(ctx.exception.get_codes(), "gstin_invalid_format")

    def test_pan_block_must_be_five_letters_four_digits_one_letter(self):
        with self.assertRaises(ApplicationError) as ctx:
            validate_gstin("27AAPF00939F1ZV")
        self.assertEqual(ctx.exception.get_codes(), "gstin_invalid_format")

    def test_bad_check_character_rejected(self):
        gstin = VALID_GSTINS[0]
        wrong = gstin[:14] + ("A" if gstin[14] != "A" else "B")
        with self.assertRaises(ApplicationError) as ctx:
            validate_gstin(wrong)
        self.assertEqual(ctx.exception.get_codes(), "gstin_checksum_invalid")

    def test_malformed_pan_rejected(self):
        with self.assertRaises(ApplicationError) as ctx:
            validate_pan("AAPFU0939")
        self.assertEqual(ctx.exception.get_codes(), "pan_invalid_format")

    def test_state_code_helper_does_not_validate(self):
        self.assertEqual(gstin_state_code("27AAPFU0939F1ZV"), "27")
        self.assertEqual(gstin_state_code(""), "")


class GstinStateCodeTests(TestCase):
    """The optional state check needs the master, so it needs the database."""

    def setUp(self):
        ensure_state_codes()
        from tax.selectors import known_state_codes

        self.codes = known_state_codes()

    def test_unknown_state_code_rejected_when_codes_supplied(self):
        # 28 was Andhra Pradesh before the 2014 bifurcation and is not in the
        # current master. A well-formed number carrying it is still wrong.
        candidate = "28AAPFU0939F1Z" + compute_gstin_check_character("28AAPFU0939F1Z")
        with self.assertRaises(ApplicationError) as ctx:
            validate_gstin(candidate, known_state_codes=self.codes)
        self.assertEqual(ctx.exception.get_codes(), "gstin_unknown_state")

    def test_known_state_code_accepted(self):
        self.assertEqual(
            validate_gstin(VALID_GSTINS[0], known_state_codes=self.codes), VALID_GSTINS[0]
        )

    def test_state_check_is_skipped_when_codes_not_supplied(self):
        # Purity matters: the same function must work with no database at all.
        candidate = "28AAPFU0939F1Z" + compute_gstin_check_character("28AAPFU0939F1Z")
        self.assertEqual(validate_gstin(candidate), candidate)
