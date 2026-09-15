from rest_framework.test import APITestCase


class HealthCheckTests(APITestCase):
    def test_health_check_reports_ok(self):
        response = self.client.get("/api/v1/health/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "ok")
        self.assertTrue(response.data["checks"]["database"])
        self.assertTrue(response.data["checks"]["cache"])
