from rest_framework.test import APITestCase

# Well beyond DEFAULT_THROTTLE_RATES["anon"] (60/min) — proves the exemption
# holds regardless of the configured limit, not just "happens not to trip it".
_REQUEST_COUNT_BEYOND_ANON_LIMIT = 80


class HealthCheckTests(APITestCase):
    def test_health_check_reports_ok(self):
        response = self.client.get("/api/v1/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "ok")
        self.assertTrue(response.data["checks"]["database"])
        self.assertTrue(response.data["checks"]["cache"])

    def test_health_check_is_never_throttled(self):
        """A burst of health probes (e.g. several ECS tasks starting at once)
        must never trip the global AnonRateThrottle baseline
        (config/settings/base.py) — core/views.py sets throttle_classes = []
        specifically so this holds regardless of the configured rate."""
        for _ in range(_REQUEST_COUNT_BEYOND_ANON_LIMIT):
            response = self.client.get("/api/v1/health/")
            self.assertEqual(response.status_code, 200)


class LivenessCheckTests(APITestCase):
    databases = []

    def test_liveness_check_reports_ok_without_touching_dependencies(self):
        response = self.client.get("/api/v1/health/live/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {"status": "ok"})

    def test_liveness_check_is_never_throttled(self):
        for _ in range(_REQUEST_COUNT_BEYOND_ANON_LIMIT):
            response = self.client.get("/api/v1/health/live/")
            self.assertEqual(response.status_code, 200)
