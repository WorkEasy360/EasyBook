"""Shared fixtures for the ai suite.

Documents are created through the real `documents.services.uploads`
pipeline (validation, malware scan, READY transition), and indexed through
the real `ai.rag.indexing` pipeline on the deterministic fake embedding
provider — never rows poked into ai tables — so every retrieval test proves
something about what ingestion actually writes.
"""

from django.test import TestCase, TransactionTestCase

from accounts.models import Membership
from ai.rag.indexing import index_document
from authz.roles import Role
from core.tenancy import tenant_context
from core.tests.factories import make_org_with_owner, make_user
from documents.models.document import DocumentType
from documents.services.uploads import upload_document


def tenant(organization, user=None):
    return tenant_context(organization_id=organization.id, user_id=getattr(user, "id", None))


def upload_text(organization, user, text: str, *, title="Doc", document_type=DocumentType.GENERAL, filename="doc.txt"):
    with tenant(organization, user):
        return upload_document(
            organization=organization, uploaded_by=user, content=text.encode("utf-8"),
            original_filename=filename, title=title, declared_content_type="text/plain", document_type=document_type,
        )


def index(organization, document, **kwargs):
    with tenant(organization):
        return index_document(document_id=document.pk, **kwargs)


def add_member(organization, email, role):
    user = make_user(email=email)
    with tenant_context(user_id=user.id):
        Membership.objects.create(organization=organization, user=user, role=role)
    return user


class AIFixtureMixin:
    def setUp(self):
        super().setUp()
        self.org_a, self.user_a, self.membership_a = make_org_with_owner("AI Org A", "ai-owner-a@example.com")
        self.org_b, self.user_b, self.membership_b = make_org_with_owner("AI Org B", "ai-owner-b@example.com")

    def member(self, organization, email, role=Role.VIEWER):
        return add_member(organization, email, role)


class AITestsBase(AIFixtureMixin, TestCase):
    pass


class AITransactionTestsBase(AIFixtureMixin, TransactionTestCase):
    pass
