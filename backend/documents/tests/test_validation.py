from django.test import SimpleTestCase, override_settings

from core.exceptions import ApplicationError
from documents.services.validation import sanitize_filename, validate_upload

PDF_BYTES = b"%PDF-1.4\n%mock pdf content\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 20
JPEG_BYTES = b"\xff\xd8\xff" + b"0" * 20


class SanitizeFilenameTests(SimpleTestCase):
    def test_strips_directory_traversal(self):
        self.assertEqual(sanitize_filename("../../etc/passwd"), "passwd")
        self.assertEqual(sanitize_filename("..\\..\\windows\\system32\\evil.exe"), "evil.exe")

    def test_replaces_unsafe_characters(self):
        self.assertEqual(sanitize_filename("my file (2)!.pdf"), "my_file_2_.pdf")

    def test_empty_after_sanitizing_falls_back(self):
        self.assertEqual(sanitize_filename("..."), "file")

    def test_truncates_long_names(self):
        name = ("a" * 300) + ".pdf"
        result = sanitize_filename(name)
        self.assertLessEqual(len(result), 200)
        self.assertTrue(result.endswith(".pdf"))


class ValidateUploadTests(SimpleTestCase):
    def test_valid_pdf_accepted(self):
        extension, mime_type, filename = validate_upload(filename="receipt.pdf", content=PDF_BYTES)
        self.assertEqual(extension, ".pdf")
        self.assertEqual(mime_type, "application/pdf")
        self.assertEqual(filename, "receipt.pdf")

    def test_valid_image_accepted(self):
        extension, mime_type, _ = validate_upload(filename="scan.png", content=PNG_BYTES)
        self.assertEqual(extension, ".png")
        self.assertEqual(mime_type, "image/png")

    def test_blocked_extension_rejected(self):
        with self.assertRaises(ApplicationError) as ctx:
            validate_upload(filename="script.exe", content=b"MZ" + b"0" * 20)
        self.assertEqual(ctx.exception.get_codes(), "unsupported_file_type")

    def test_html_rejected(self):
        with self.assertRaises(ApplicationError) as ctx:
            validate_upload(filename="page.html", content=b"<html></html>")
        self.assertEqual(ctx.exception.get_codes(), "unsupported_file_type")

    def test_zero_byte_file_rejected(self):
        with self.assertRaises(ApplicationError) as ctx:
            validate_upload(filename="empty.pdf", content=b"")
        self.assertEqual(ctx.exception.get_codes(), "file_empty")

    def test_mime_mismatch_rejected(self):
        with self.assertRaises(ApplicationError) as ctx:
            validate_upload(filename="receipt.pdf", content=PDF_BYTES, declared_content_type="image/png")
        self.assertEqual(ctx.exception.get_codes(), "mime_mismatch")

    def test_signature_mismatch_rejected(self):
        # .pdf extension but the content is actually a PNG signature.
        with self.assertRaises(ApplicationError) as ctx:
            validate_upload(filename="fake.pdf", content=PNG_BYTES)
        self.assertEqual(ctx.exception.get_codes(), "file_signature_mismatch")

    @override_settings(DOCUMENT_MAX_UPLOAD_SIZES={"default": 10, "image": 10, "pdf": 10})
    def test_oversized_file_rejected(self):
        with self.assertRaises(ApplicationError) as ctx:
            validate_upload(filename="receipt.pdf", content=PDF_BYTES)
        self.assertEqual(ctx.exception.get_codes(), "file_too_large")

    def test_jpeg_both_extensions_accepted(self):
        for name in ("photo.jpg", "photo.jpeg"):
            _, mime_type, _ = validate_upload(filename=name, content=JPEG_BYTES)
            self.assertEqual(mime_type, "image/jpeg")
