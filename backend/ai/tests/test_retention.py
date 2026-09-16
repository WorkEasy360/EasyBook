import datetime

from django.test import override_settings
from django.utils import timezone

from ai.models import AIConversation, AIMessage, AIRequestLog
from ai.rag.tasks import purge_expired_ai_data
from ai.tests.base import AITestsBase, tenant


class RetentionTests(AITestsBase):
    @override_settings(AI_CONVERSATION_RETENTION_DAYS=30, AI_REQUEST_LOG_RETENTION_DAYS=90)
    def test_expired_conversations_and_logs_are_purged_per_tenant(self):
        old = timezone.now() - datetime.timedelta(days=45)
        ancient = timezone.now() - datetime.timedelta(days=120)
        for organization, user in ((self.org_a, self.user_a), (self.org_b, self.user_b)):
            with tenant(organization):
                stale = AIConversation.objects.create(organization=organization, user=user, last_message_at=old)
                AIMessage.objects.create(organization=organization, conversation=stale, role="user", content="q")
                AIConversation.objects.create(organization=organization, user=user, last_message_at=timezone.now())
                log = AIRequestLog.objects.create(organization=organization, feature="ask", status="ok")
                AIRequestLog.objects.filter(pk=log.pk).update(created_at=ancient)
                AIRequestLog.objects.create(organization=organization, feature="ask", status="ok")

        totals = purge_expired_ai_data()

        self.assertEqual(totals, {"conversations": 2, "request_logs": 2})
        for organization in (self.org_a, self.org_b):
            with tenant(organization):
                self.assertEqual(AIConversation.objects.count(), 1)
                self.assertEqual(AIMessage.objects.count(), 0)
                self.assertEqual(AIRequestLog.objects.count(), 1)
