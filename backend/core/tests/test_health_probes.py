"""Liveness probes as ECS and the ALB actually send them.

Regression (phase 12 P0 remediation): neither probe carries the public host.
The ALB target-group health check sends the task's private IP as Host
(`10.20.x.y:8000`) and the ECS container healthCheck sends `127.0.0.1:8000`
(infrastructure/terraform/ecs.tf). In production DJANGO_ALLOWED_HOSTS is the
public domain only, and CommonMiddleware validates Host on every request, so
both probes got 400 DisallowedHost: every API task would be marked unhealthy
and replaced in a loop, and the target group would never have a healthy
target. The existing liveness tests passed only because the test client sends
`testserver`, which the test settings allow.

SimpleTestCase: any database access fails the test, which is itself part of
the contract — liveness must not depend on the database.
"""

from django.test import SimpleTestCase, override_settings

PRODUCTION_LIKE = {
    "ALLOWED_HOSTS": ["api.easybook.example"],
    "SECURE_SSL_REDIRECT": True,
    "SECURE_PROXY_SSL_HEADER": ("HTTP_X_FORWARDED_PROTO", "https"),
}


@override_settings(**PRODUCTION_LIKE)
class LivenessProbeTests(SimpleTestCase):
    def test_alb_probe_addressed_to_the_task_ip_is_answered(self):
        response = self.client.get("/api/v1/health/live/", HTTP_HOST="10.20.100.37:8000")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_ecs_container_probe_from_localhost_is_answered(self):
        response = self.client.get("/api/v1/health/live/", HTTP_HOST="127.0.0.1:8000")
        self.assertEqual(response.status_code, 200)

    def test_plain_http_probe_is_not_redirected_to_https(self):
        response = self.client.get("/api/v1/health/live/", HTTP_HOST="10.20.100.37:8000")
        self.assertNotIn(response.status_code, (301, 302, 307, 308))

    def test_head_probe_is_answered(self):
        response = self.client.head("/api/v1/health/live/", HTTP_HOST="10.20.100.37:8000")
        self.assertEqual(response.status_code, 200)

    def test_every_other_path_still_rejects_an_unknown_host(self):
        response = self.client.get(
            "/api/v1/organizations/", HTTP_HOST="10.20.100.37:8000", HTTP_X_FORWARDED_PROTO="https"
        )
        self.assertEqual(response.status_code, 400)

    def test_writes_to_the_probe_path_get_no_special_treatment(self):
        response = self.client.post("/api/v1/health/live/", HTTP_HOST="10.20.100.37:8000")
        self.assertEqual(response.status_code, 400)
