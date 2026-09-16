"""Documents API views.

Every queryset is built in `get_queryset()`, never a bare
`queryset = Model.objects.all()` class attribute (core/CLAUDE.md — a
class-level queryset is evaluated at import time, before tenant context
exists, and permanently regresses tenant scoping).
"""

from io import BytesIO

from django.conf import settings
from django.core import signing
from django.http import FileResponse, Http404
from rest_framework import generics, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from authz.permissions import HasOrgPermission
from authz.roles import Permission
from core.exceptions import ApplicationError
from core.views import OrganizationScopedMixin
from documents.api.serializers import (
    DocumentLinkCreateSerializer,
    DocumentLinkSerializer,
    DocumentReviewSerializer,
    DocumentSerializer,
    DocumentUpdateSerializer,
    DocumentUploadSerializer,
    OCRResultSerializer,
    ReviewActionSerializer,
)
from documents.models.document import Document
from documents.models.folder import DocumentFolder
from documents.models.link import DocumentLink
from documents.models.ocr_result import OCRResult
from documents.models.review import ReviewStatus
from documents.search import search_documents
from documents.selectors import find_possible_duplicates
from documents.services.downloads import get_download_url
from documents.services.links import create_link, delete_link
from documents.services.ocr import request_ocr
from documents.services.review import approve_review, reject_review
from documents.services.uploads import archive_document, update_document, upload_document
from documents.storage.local import LocalDocumentStorage


def _get_or_404(model, pk, label: str):
    try:
        return model.objects.get(pk=pk)
    except model.DoesNotExist:
        raise ApplicationError(f"{label} not found.", code=f"{label.lower()}_not_found", status_code=404)


class DocumentListView(OrganizationScopedMixin, generics.ListAPIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_DOCUMENTS
    serializer_class = DocumentSerializer

    def get_queryset(self):
        qs = Document.objects.all()
        document_type = self.request.query_params.get("document_type")
        if document_type:
            qs = qs.filter(document_type=document_type)
        upload_status = self.request.query_params.get("upload_status")
        if upload_status:
            qs = qs.filter(upload_status=upload_status)
        return qs


class DocumentUploadView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.UPLOAD_DOCUMENT
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        serializer = DocumentUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        folder = None
        if data.get("folder_id"):
            folder = _get_or_404(DocumentFolder, data["folder_id"], "Folder")

        uploaded = data["file"]
        document = upload_document(
            organization=request.organization,
            uploaded_by=request.user,
            content=uploaded.read(),
            original_filename=uploaded.name,
            title=data.get("title", ""),
            declared_content_type=uploaded.content_type or "",
            document_type=data.get("document_type"),
            folder=folder,
            actor=request.user,
        )

        duplicates = list(
            find_possible_duplicates(checksum=document.checksum_sha256, exclude_id=document.id).values_list(
                "id", flat=True
            )
        )
        response = DocumentSerializer(document).data
        response["possible_duplicate_ids"] = [str(d) for d in duplicates]
        return Response(response, status=status.HTTP_201_CREATED)


class DocumentDetailView(OrganizationScopedMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [HasOrgPermission]
    serializer_class = DocumentSerializer

    @property
    def required_permission(self):
        return Permission.VIEW_DOCUMENTS if self.request.method == "GET" else Permission.MANAGE_DOCUMENTS

    def get_queryset(self):
        return Document.objects.all()

    def update(self, request, *args, **kwargs):
        document = self.get_object()
        serializer = DocumentUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        fields = dict(serializer.validated_data)
        if "folder_id" in fields:
            folder_id = fields.pop("folder_id")
            fields["folder"] = _get_or_404(DocumentFolder, folder_id, "Folder") if folder_id else None
        document = update_document(document=document, actor=request.user, **fields)
        return Response(DocumentSerializer(document).data)


class DocumentArchiveView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.DELETE_DOCUMENT

    def post(self, request, pk):
        document = _get_or_404(Document, pk, "Document")
        document = archive_document(document=document, actor=request.user)
        return Response(DocumentSerializer(document).data)


class DocumentDownloadView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.DOWNLOAD_DOCUMENT

    def post(self, request, pk):
        document = _get_or_404(Document, pk, "Document")
        return Response(get_download_url(document=document, actor=request.user))


class LocalStorageDownloadView(APIView):
    """Serves a file from the local storage backend given a short-lived
    signed token minted by `get_download_url` (documents/storage/local.py).
    Deliberately NOT `OrganizationScopedMixin`-gated: possession of a valid,
    unexpired token is the credential, exactly like a real S3 presigned URL —
    access control (including the quarantine check) was already enforced by
    `services/downloads.py::get_download_url` when the token was minted, not
    re-checked here. This route has no tenant context, so it cannot safely
    re-query `Document` anyway — a `TenantScopedModel` query with no
    organization GUC set is blocked by RLS regardless of manager (fail
    closed, see core/CLAUDE.md), which is the correct default but means this
    view must not depend on one succeeding.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, token):
        try:
            key = LocalDocumentStorage.verify_token(token, max_age=settings.DOCUMENT_SIGNED_URL_TTL_SECONDS)
        except (signing.BadSignature, signing.SignatureExpired):
            raise Http404

        storage = LocalDocumentStorage()
        if not storage.exists(key=key):
            raise Http404
        return FileResponse(BytesIO(storage.get_bytes(key=key)), content_type="application/octet-stream")


class DocumentOCRView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_DOCUMENTS if self.request.method == "GET" else Permission.UPLOAD_DOCUMENT

    def get(self, request, pk):
        document = _get_or_404(Document, pk, "Document")
        result = OCRResult.objects.filter(document=document).first()
        if result is None:
            raise ApplicationError("No OCR result yet.", code="ocr_result_not_found", status_code=404)
        return Response(OCRResultSerializer(result).data)

    def post(self, request, pk):
        document = _get_or_404(Document, pk, "Document")
        document = request_ocr(document=document, actor=request.user)
        return Response(DocumentSerializer(document).data, status=status.HTTP_202_ACCEPTED)


class DocumentReviewView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.REVIEW_DOCUMENT_OCR

    def post(self, request, pk):
        document = _get_or_404(Document, pk, "Document")
        serializer = ReviewActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if data["status"] == ReviewStatus.APPROVED:
            review = approve_review(
                document=document, reviewer=request.user,
                corrected_fields=data.get("corrected_fields"), notes=data.get("notes", ""),
            )
        else:
            review = reject_review(document=document, reviewer=request.user, notes=data.get("notes", ""))
        return Response(DocumentReviewSerializer(review).data)


class DocumentSearchView(OrganizationScopedMixin, generics.ListAPIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_DOCUMENTS
    serializer_class = DocumentSerializer

    def get_queryset(self):
        params = self.request.query_params
        tags = params.get("tags")
        return search_documents(
            query=params.get("q", ""),
            document_type=params.get("document_type") or None,
            folder_id=params.get("folder_id") or None,
            tag_names=tags.split(",") if tags else None,
        )


class DocumentLinkListCreateView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]

    @property
    def required_permission(self):
        return Permission.VIEW_DOCUMENTS if self.request.method == "GET" else Permission.MANAGE_DOCUMENTS

    def get(self, request, pk):
        document = _get_or_404(Document, pk, "Document")
        links = DocumentLink.objects.filter(document=document)
        return Response(DocumentLinkSerializer(links, many=True).data)

    def post(self, request, pk):
        document = _get_or_404(Document, pk, "Document")
        serializer = DocumentLinkCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        link = create_link(document=document, actor=request.user, **serializer.validated_data)
        return Response(DocumentLinkSerializer(link).data, status=status.HTTP_201_CREATED)


class DocumentLinkDeleteView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.MANAGE_DOCUMENTS

    def delete(self, request, pk, link_id):
        document = _get_or_404(Document, pk, "Document")
        link = _get_or_404(DocumentLink, link_id, "Link")
        if link.document_id != document.id:
            raise ApplicationError("Link does not belong to this document.", code="link_document_mismatch")
        delete_link(link=link, actor=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
