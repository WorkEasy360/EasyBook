from io import StringIO

from django.core.management import call_command

from ai.models import DocumentIndex, IndexStatus
from ai.tests.base import AITestsBase, tenant, upload_text


class ReindexCommandTests(AITestsBase):
    def test_batch_reindex_is_tenant_scoped_and_idempotent(self):
        doc_a = upload_text(self.org_a, self.user_a, "Alpha payment terms are thirty days.")
        doc_b = upload_text(self.org_b, self.user_b, "Beta payment terms are sixty days.")
        out = StringIO()
        call_command("ai_reindex_documents", "--inline", stdout=out)
        call_command("ai_reindex_documents", "--inline", "--organization", str(self.org_a.id), stdout=out)
        for organization, document in ((self.org_a, doc_a), (self.org_b, doc_b)):
            with tenant(organization):
                index = DocumentIndex.objects.get(document=document)
                self.assertEqual(index.status, IndexStatus.INDEXED)
                self.assertEqual(index.organization_id, organization.id)
        self.assertIn("Indexed 2 document(s).", out.getvalue())
        self.assertIn("Indexed 1 document(s).", out.getvalue())
