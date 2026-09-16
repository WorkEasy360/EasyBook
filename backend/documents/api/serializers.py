"""Documents API serializers.

Tenant-scoped model references (`folder_id`, `tag` names) are resolved
inside the view/serializer at request time via `.objects.get(...)` under the
ambient tenant context — never a `PrimaryKeyRelatedField(queryset=Model.objects.all())`
class attribute, which bakes in `.none()` at import time before any tenant
context exists (core/CLAUDE.md, mirrors purchases/api/serializers.py).

`storage_key` is never serialized — it is an internal storage reference, not
something a client should read or write (documents/CLAUDE.md).
"""

from rest_framework import serializers

from documents.models.document import Document, DocumentType
from documents.models.link import DocumentLink, LinkedEntityType
from documents.models.ocr_result import OCRResult
from documents.models.review import DocumentReview, ReviewStatus


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = [
            "id", "title", "original_filename", "mime_type", "file_size", "checksum_sha256",
            "document_type", "upload_status", "ocr_status", "folder", "uploaded_by",
            "retention_until", "legal_hold", "archived_at", "created_at", "updated_at",
        ]
        read_only_fields = fields


class DocumentUpdateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=255, required=False)
    document_type = serializers.ChoiceField(choices=DocumentType.choices, required=False)
    folder_id = serializers.UUIDField(required=False, allow_null=True)


class DocumentUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    title = serializers.CharField(max_length=255, required=False, allow_blank=True)
    document_type = serializers.ChoiceField(choices=DocumentType.choices, required=False, default=DocumentType.GENERAL)
    folder_id = serializers.UUIDField(required=False, allow_null=True)


class OCRResultSerializer(serializers.ModelSerializer):
    class Meta:
        model = OCRResult
        fields = [
            "provider", "provider_version", "raw_text", "structured_payload", "confidence",
            "error_code", "error_message", "processed_at",
        ]
        read_only_fields = fields


class DocumentReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentReview
        fields = ["status", "reviewer", "reviewed_at", "corrected_fields", "notes"]
        read_only_fields = fields


class ReviewActionSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[ReviewStatus.APPROVED, ReviewStatus.REJECTED])
    corrected_fields = serializers.JSONField(required=False)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class DocumentLinkSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentLink
        fields = ["id", "entity_type", "entity_id", "created_by", "created_at"]
        read_only_fields = fields


class DocumentLinkCreateSerializer(serializers.Serializer):
    entity_type = serializers.ChoiceField(choices=LinkedEntityType.choices)
    entity_id = serializers.UUIDField()
