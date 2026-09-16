from django.contrib import admin

from documents.models import Document, DocumentFolder, DocumentLink, DocumentReview, DocumentTag


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ["title", "organization", "document_type", "upload_status", "ocr_status", "created_at"]
    list_filter = ["document_type", "upload_status", "ocr_status"]
    search_fields = ["title", "original_filename", "checksum_sha256"]


@admin.register(DocumentFolder)
class DocumentFolderAdmin(admin.ModelAdmin):
    list_display = ["name", "organization", "parent"]


@admin.register(DocumentTag)
class DocumentTagAdmin(admin.ModelAdmin):
    list_display = ["name", "organization"]


@admin.register(DocumentLink)
class DocumentLinkAdmin(admin.ModelAdmin):
    list_display = ["document", "entity_type", "entity_id", "organization"]
    list_filter = ["entity_type"]


@admin.register(DocumentReview)
class DocumentReviewAdmin(admin.ModelAdmin):
    list_display = ["document", "status", "reviewer", "reviewed_at", "organization"]
    list_filter = ["status"]
