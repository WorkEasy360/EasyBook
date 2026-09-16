"""S3DocumentStorage against a mocked boto3 client — no real AWS credentials
or network access, so this only proves the adapter calls the right botocore
operations with the right arguments, never that AWS itself behaves a given
way (root CLAUDE.md: never claim a live third-party integration works without
verifying it)."""

from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError
from django.test import SimpleTestCase, override_settings

from documents.storage.s3 import S3DocumentStorage


@override_settings(
    DOCUMENT_STORAGE_S3_BUCKET="test-bucket", DOCUMENT_STORAGE_S3_ENDPOINT_URL="", DOCUMENT_STORAGE_S3_REGION="ap-south-1",
)
class S3DocumentStorageTests(SimpleTestCase):
    def _storage_with_mock_client(self):
        with patch("boto3.client") as mock_client_factory:
            mock_client = MagicMock()
            mock_client_factory.return_value = mock_client
            storage = S3DocumentStorage()
        return storage, mock_client

    def test_put_calls_put_object_with_server_side_encryption(self):
        storage, client = self._storage_with_mock_client()
        storage.put(key="k1", content=b"hello", content_type="application/pdf")
        client.put_object.assert_called_once_with(
            Bucket="test-bucket", Key="k1", Body=b"hello", ContentType="application/pdf",
            ServerSideEncryption="AES256",
        )

    def test_get_signed_url_uses_presigned_get_object(self):
        storage, client = self._storage_with_mock_client()
        client.generate_presigned_url.return_value = "https://example.com/signed"
        url = storage.get_signed_url(key="k1", expires_in=120)
        self.assertEqual(url, "https://example.com/signed")
        client.generate_presigned_url.assert_called_once_with(
            "get_object", Params={"Bucket": "test-bucket", "Key": "k1"}, ExpiresIn=120,
        )

    def test_delete_calls_delete_object(self):
        storage, client = self._storage_with_mock_client()
        storage.delete(key="k1")
        client.delete_object.assert_called_once_with(Bucket="test-bucket", Key="k1")

    def test_get_bytes_reads_body(self):
        storage, client = self._storage_with_mock_client()
        client.get_object.return_value = {"Body": MagicMock(read=lambda: b"content")}
        self.assertEqual(storage.get_bytes(key="k1"), b"content")

    def test_exists_true_when_head_object_succeeds(self):
        storage, client = self._storage_with_mock_client()
        client.head_object.return_value = {}
        self.assertTrue(storage.exists(key="k1"))

    def test_exists_false_on_404(self):
        storage, client = self._storage_with_mock_client()
        client.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
        self.assertFalse(storage.exists(key="k1"))

    def test_exists_reraises_other_errors(self):
        storage, client = self._storage_with_mock_client()
        client.head_object.side_effect = ClientError({"Error": {"Code": "500"}}, "HeadObject")
        with self.assertRaises(ClientError):
            storage.exists(key="k1")
