"""S3-compatible object storage backend (production target — root CLAUDE.md
stack: "S3-compatible storage"). `boto3` is imported lazily so dev/test, which
default to `LocalDocumentStorage`, never need it installed.

Never grants public/anonymous access: the bucket is expected to be fully
private, and every read goes through a short-lived presigned URL.
"""

from django.conf import settings

from documents.storage.base import DocumentStorage


class S3DocumentStorage(DocumentStorage):
    def __init__(self):
        import boto3

        self._bucket = settings.DOCUMENT_STORAGE_S3_BUCKET
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.DOCUMENT_STORAGE_S3_ENDPOINT_URL or None,
            region_name=settings.DOCUMENT_STORAGE_S3_REGION or None,
        )

    def put(self, *, key: str, content: bytes, content_type: str) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=content,
            ContentType=content_type,
            ServerSideEncryption="AES256",
        )

    def get_signed_url(self, *, key: str, expires_in: int) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def delete(self, *, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def get_bytes(self, *, key: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        return response["Body"].read()

    def exists(self, *, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey"):
                return False
            raise
