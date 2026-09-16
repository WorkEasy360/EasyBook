from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("core.urls")),
    path("api/v1/", include("accounts.urls")),
    path("api/v1/", include("accounting.api.urls")),
    path("api/v1/", include("documents.api.urls")),
    path("api/v1/", include("items.api.urls")),
    path("api/v1/", include("inventory.api.urls")),
    path("api/v1/", include("sales.api.urls")),
    path("api/v1/", include("purchases.api.urls")),
    path("api/v1/", include("projects.api.urls")),
    path("api/v1/", include("banking.api.urls")),
    path("api/v1/", include("reports.api.urls")),
]
