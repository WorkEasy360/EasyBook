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


class AutomationExecutionAPITests(APITestCase):
    def setUp(self):
        self.org_a, self.owner_a, _ = make_org_with_owner("Org A", "automation-exec-api-owner-a@example.com")
        self.org_b, self.owner_b, _ = make_org_with_owner("Org B", "automation-exec-api-owner-b@example.com")
        self.viewer = make_user("automation-exec-api-viewer@example.com")
        make_membership(self.org_a, self.viewer, role=Role.VIEWER)

    def _headers(self, org):
        return {"HTTP_X_ORGANIZATION_ID": str(org.id)}

    def _create_and_activate_manual_rule(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.post(
            "/api/v1/automation/rules/",
            {
                "name": "Manual notify", "trigger_type": "manual", "conditions": [],
                "actions": [{"action_id": "send_notification", "config": {"message": "hi"}}],
            },
            format="json", **self._headers(self.org_a),
        )
        rule_id = response.data["id"]
        self.client.post(f"/api/v1/automation/rules/{rule_id}/activate/", **self._headers(self.org_a))
        return rule_id

    def test_owner_can_run_rule_manually_and_view_history(self):
        rule_id = self._create_and_activate_manual_rule()

        self.client.force_authenticate(user=self.owner_a)
        with self.captureOnCommitCallbacks(execute=True):
            run_response = self.client.post(f"/api/v1/automation/rules/{rule_id}/run/", **self._headers(self.org_a))
        self.assertEqual(run_response.status_code, 201, run_response.data)
        # Enqueued on commit, so the response can only ever report it pending.
        self.assertEqual(run_response.data["status"], "pending")
        self.assertEqual(run_response.data["trigger_source"], "manual")
        execution_id = run_response.data["id"]

        list_response = self.client.get("/api/v1/automation/executions/", **self._headers(self.org_a))
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.data["count"], 1)

        detail_response = self.client.get(f"/api/v1/automation/executions/{execution_id}/", **self._headers(self.org_a))
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["status"], "succeeded")
        self.assertEqual(len(detail_response.data["steps"]), 1)
        self.assertEqual(detail_response.data["steps"][0]["status"], "succeeded")

    def test_viewer_can_view_history_but_cannot_run_or_retry(self):
        rule_id = self._create_and_activate_manual_rule()

        self.client.force_authenticate(user=self.viewer)
        run_response = self.client.post(f"/api/v1/automation/rules/{rule_id}/run/", **self._headers(self.org_a))
        self.assertEqual(run_response.status_code, 403)

        list_response = self.client.get("/api/v1/automation/executions/", **self._headers(self.org_a))
        self.assertEqual(list_response.status_code, 200)

    def test_execution_from_other_org_is_not_visible(self):
        rule_id = self._create_and_activate_manual_rule()
        self.client.force_authenticate(user=self.owner_a)
        self.client.post(f"/api/v1/automation/rules/{rule_id}/run/", **self._headers(self.org_a))

        self.client.force_authenticate(user=self.owner_b)
        response = self.client.get("/api/v1/automation/executions/", **self._headers(self.org_b))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)

    def test_retry_endpoint_rejects_a_succeeded_execution(self):
        rule_id = self._create_and_activate_manual_rule()
        self.client.force_authenticate(user=self.owner_a)
        with self.captureOnCommitCallbacks(execute=True):
            run_response = self.client.post(f"/api/v1/automation/rules/{rule_id}/run/", **self._headers(self.org_a))
        execution_id = run_response.data["id"]

        retry_response = self.client.post(
            f"/api/v1/automation/executions/{execution_id}/retry/", **self._headers(self.org_a)
        )
        self.assertEqual(retry_response.status_code, 400)

    # --- Regressions -------------------------------------------------------

    def test_invalid_rule_filter_is_a_validation_error_not_a_500(self):
        self.client.force_authenticate(user=self.owner_a)
        response = self.client.get("/api/v1/automation/executions/", {"rule": "notauuid"}, **self._headers(self.org_a))
        self.assertEqual(response.status_code, 400)

    def test_record_based_manual_run_requires_a_valid_entity_id(self):
        # Previously both requests returned 201 and left a permanently
        # pending execution behind.
        self.client.force_authenticate(user=self.owner_a)
        create = self.client.post(
            "/api/v1/automation/rules/",
            {
                "name": "Posted invoice notify", "trigger_type": "invoice.posted", "conditions": [],
                "actions": [{"action_id": "send_notification", "config": {"message": "posted"}}],
            },
            format="json", **self._headers(self.org_a),
        )
        self.assertEqual(create.status_code, 201, create.data)
        rule_id = create.data["id"]
        self.client.post(f"/api/v1/automation/rules/{rule_id}/activate/", **self._headers(self.org_a))

        missing = self.client.post(f"/api/v1/automation/rules/{rule_id}/run/", {}, format="json", **self._headers(self.org_a))
        self.assertEqual(missing.status_code, 400, missing.data)
        malformed = self.client.post(
            f"/api/v1/automation/rules/{rule_id}/run/", {"entity_id": "not-a-uuid"}, format="json",
            **self._headers(self.org_a),
        )
        self.assertEqual(malformed.status_code, 400, malformed.data)

        history = self.client.get("/api/v1/automation/executions/", **self._headers(self.org_a))
        self.assertEqual(history.data["count"], 0)


class AutomationStepSecretMaskingTests(APITestCase):
    def test_execution_step_never_returns_a_webhook_secret(self):
        from automation.api.serializers import AutomationStepExecutionSerializer
        from automation.models.step_execution import AutomationStepExecution

        step = AutomationStepExecution(
            order=1, action_id="call_webhook",
            config_snapshot={"url": "https://hooks.example.com/x", "secret": "probe-secret-value"},
        )
        data = AutomationStepExecutionSerializer(step).data
        self.assertEqual(data["config_snapshot"]["secret"], "********")
        self.assertEqual(data["config_snapshot"]["url"], "https://hooks.example.com/x")
        self.assertNotIn("probe-secret-value", str(data))

    def test_other_actions_are_returned_unchanged(self):
        from automation.api.serializers import AutomationStepExecutionSerializer
        from automation.models.step_execution import AutomationStepExecution

        step = AutomationStepExecution(order=1, action_id="send_notification", config_snapshot={"message": "hi"})
        self.assertEqual(AutomationStepExecutionSerializer(step).data["config_snapshot"], {"message": "hi"})
