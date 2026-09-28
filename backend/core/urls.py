from django.db.transaction import non_atomic_requests
from django.urls import path

from core.views import HealthCheckView, LivenessCheckView

urlpatterns = [
    path("health/", HealthCheckView.as_view(), name="health-check"),
    # DATABASES['default']['ATOMIC_REQUESTS'] wraps every request in a
    # transaction before the view even runs (django/db/transaction.py),
    # which alone would make "does this open a DB connection" true for a
    # liveness probe regardless of what LivenessCheckView.get() does.
    # non_atomic_requests opts this one view out, matching
    # core/middleware.py's matching exemption for tenant-context cleanup.
    path("health/live/", non_atomic_requests(LivenessCheckView.as_view()), name="liveness-check"),
]
