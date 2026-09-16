from django.db import IntegrityError

from core.exceptions import ApplicationError
from documents.models.folder import DocumentFolder
from documents.services.folders import create_folder
from documents.services.tags import assign_tag, get_or_create_tag, remove_tag
from documents.services.uploads import upload_document
from documents.tests.base import DocumentsTestsBase, tenant

PDF_BYTES = b"%PDF-1.4\n%mock\n"


class FolderTests(DocumentsTestsBase):
    def test_create_folder_and_subfolder(self):
        with tenant(self.org_a):
            parent = create_folder(organization=self.org_a, name="Receipts")
            child = create_folder(organization=self.org_a, name="2026", parent=parent)
        self.assertEqual(child.parent_id, parent.id)

    def test_duplicate_name_under_same_parent_rejected(self):
        with tenant(self.org_a):
            create_folder(organization=self.org_a, name="Receipts")
            with self.assertRaises(IntegrityError):
                create_folder(organization=self.org_a, name="Receipts")

    def test_cross_tenant_parent_rejected(self):
        with tenant(self.org_b):
            other_folder = create_folder(organization=self.org_b, name="Other")
        with tenant(self.org_a):
            with self.assertRaises(ApplicationError):
                create_folder(organization=self.org_a, name="Mine", parent=other_folder)

    def test_document_can_be_filed_into_folder(self):
        with tenant(self.org_a):
            folder = create_folder(organization=self.org_a, name="Receipts")
            document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES,
                original_filename="r.pdf", folder=folder,
            )
        self.assertEqual(document.folder_id, folder.id)

    def test_tenant_isolation(self):
        with tenant(self.org_a):
            create_folder(organization=self.org_a, name="Receipts")
        with tenant(self.org_b):
            self.assertEqual(DocumentFolder.objects.count(), 0)


class TagTests(DocumentsTestsBase):
    def test_assign_and_remove_tag(self):
        with tenant(self.org_a):
            document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES, original_filename="r.pdf",
            )
            tag = get_or_create_tag(organization=self.org_a, name="urgent")
            assignment = assign_tag(document=document, tag=tag)
            self.assertEqual(document.tag_assignments.count(), 1)
            remove_tag(document=document, tag=tag)
            self.assertEqual(document.tag_assignments.count(), 0)
        self.assertIsNotNone(assignment.id)

    def test_assigning_same_tag_twice_is_idempotent(self):
        with tenant(self.org_a):
            document = upload_document(
                organization=self.org_a, uploaded_by=self.user_a, content=PDF_BYTES, original_filename="r.pdf",
            )
            tag = get_or_create_tag(organization=self.org_a, name="urgent")
            assign_tag(document=document, tag=tag)
            assign_tag(document=document, tag=tag)
            self.assertEqual(document.tag_assignments.count(), 1)
