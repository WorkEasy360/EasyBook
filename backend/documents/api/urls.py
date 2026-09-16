from django.urls import path

from documents.api.views import (
    DocumentArchiveView,
    DocumentDetailView,
    DocumentDownloadView,
    DocumentLinkDeleteView,
    DocumentLinkListCreateView,
    DocumentListView,
    DocumentOCRView,
    DocumentReviewView,
    DocumentSearchView,
    DocumentUploadView,
    LocalStorageDownloadView,
)

urlpatterns = [
    path("documents/", DocumentListView.as_view(), name="documents-list"),
    path("documents/upload/", DocumentUploadView.as_view(), name="documents-upload"),
    path("documents/search/", DocumentSearchView.as_view(), name="documents-search"),
    path("documents/local-storage/<str:token>/", LocalStorageDownloadView.as_view(), name="documents-local-storage"),
    path("documents/<uuid:pk>/", DocumentDetailView.as_view(), name="documents-detail"),
    path("documents/<uuid:pk>/archive/", DocumentArchiveView.as_view(), name="documents-archive"),
    path("documents/<uuid:pk>/download/", DocumentDownloadView.as_view(), name="documents-download"),
    path("documents/<uuid:pk>/ocr/", DocumentOCRView.as_view(), name="documents-ocr"),
    path("documents/<uuid:pk>/review/", DocumentReviewView.as_view(), name="documents-review"),
    path("documents/<uuid:pk>/links/", DocumentLinkListCreateView.as_view(), name="documents-link-list-create"),
    path(
        "documents/<uuid:pk>/links/<uuid:link_id>/", DocumentLinkDeleteView.as_view(),
        name="documents-link-delete",
    ),
]
