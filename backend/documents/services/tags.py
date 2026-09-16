from django.db import transaction

from documents.models.document import Document
from documents.models.tag import DocumentTag, DocumentTagAssignment


@transaction.atomic
def get_or_create_tag(*, organization, name: str) -> DocumentTag:
    tag, _ = DocumentTag.objects.get_or_create(organization=organization, name=name)
    return tag


@transaction.atomic
def assign_tag(*, document: Document, tag: DocumentTag) -> DocumentTagAssignment:
    assignment, _ = DocumentTagAssignment.objects.get_or_create(
        document=document, tag=tag, defaults={"organization": document.organization}
    )
    return assignment


def remove_tag(*, document: Document, tag: DocumentTag) -> None:
    DocumentTagAssignment.objects.filter(document=document, tag=tag).delete()
