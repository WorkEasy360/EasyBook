from rest_framework.test import APITestCase

from authz.roles import Role
from core.tests.factories import make_membership, make_org_with_owner, make_user


class AutomationRuleAPITests(APITestCase):
    def setUp(self):
        self.org_a, self.owner_a, _ = make_org_with_owner("Org A", "automation-api-owner-a@example.com")
        self.org_b, self.owner_b, _ = make_org_with_owner("Org B", "automation-api-owner-b@example.com")
        self.viewer = make_user("automation-api-viewer@example.com")
        make_membership(self.org_a, self.viewer, role=Role.VIEWER)

    def _headers(self, org):
        return {"HTTP_X_ORGANIZATION_ID": str(org.id)}

    def _payload(self, **overrides):
        payload = {
            "name": "Remind overdue customers",
            "trigger_type": "invoice.overdue",
            "conditions": [{"field": "amount_due", "operator": "greater_than", "value": "10000.00"}],
            "actions": [{"action_id": "draft_payment_reminder", "config": {}}],
        }
        payload.update(overrides)
        return payload

    def test_owner_can_create_and_activate_rule(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.post(
            "/api/v1/automation/rules/", self._payload(), format="json", **self._headers(self.org_a)
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "draft")
        rule_id = response.data["id"]

        activate_response = self.client.post(
            f"/api/v1/automation/rules/{rule_id}/activate/", **self._headers(self.org_a)
        )
        self.assertEqual(activate_response.status_code, 200, activate_response.data)
        self.assertEqual(activate_response.data["status"], "active")

        pause_response = self.client.post(f"/api/v1/automation/rules/{rule_id}/pause/", **self._headers(self.org_a))
        self.assertEqual(pause_response.status_code, 200, pause_response.data)
        self.assertEqual(pause_response.data["status"], "paused")

    def test_viewer_cannot_create_rule(self):
        self.client.force_authenticate(user=self.viewer)
        response = self.client.post(
            "/api/v1/automation/rules/", self._payload(), format="json", **self._headers(self.org_a)
        )
        self.assertEqual(response.status_code, 403)

    def test_viewer_can_list_but_not_activate(self):
        self.client.force_authenticate(user=self.owner_a)
        create_response = self.client.post(
            "/api/v1/automation/rules/", self._payload(), format="json", **self._headers(self.org_a)
        )
        rule_id = create_response.data["id"]

        self.client.force_authenticate(user=self.viewer)
        list_response = self.client.get("/api/v1/automation/rules/", **self._headers(self.org_a))
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data["count"], 1)

        activate_response = self.client.post(
            f"/api/v1/automation/rules/{rule_id}/activate/", **self._headers(self.org_a)
        )
        self.assertEqual(activate_response.status_code, 403)

    def test_rule_from_other_org_is_not_visible(self):
        self.client.force_authenticate(user=self.owner_a)
        self.client.post("/api/v1/automation/rules/", self._payload(), format="json", **self._headers(self.org_a))

        self.client.force_authenticate(user=self.owner_b)
        response = self.client.get("/api/v1/automation/rules/", **self._headers(self.org_b))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)

    def test_invalid_condition_field_rejected_at_create(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.post(
            "/api/v1/automation/rules/",
            self._payload(conditions=[{"field": "not_allowed", "operator": "equals", "value": "x"}]),
            format="json",
            **self._headers(self.org_a),
        )
        self.assertEqual(response.status_code, 400)

    def test_catalog_endpoints_return_metadata(self):
        self.client.force_authenticate(user=self.owner_a)
        triggers = self.client.get("/api/v1/automation/catalog/triggers/", **self._headers(self.org_a))
        actions = self.client.get("/api/v1/automation/catalog/actions/", **self._headers(self.org_a))
        self.assertEqual(triggers.status_code, 200)
        self.assertEqual(actions.status_code, 200)
        self.assertIn("invoice.overdue", [t["id"] for t in triggers.data])
        self.assertIn("send_notification", [a["id"] for a in actions.data])
