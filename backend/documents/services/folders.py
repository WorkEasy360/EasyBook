from django.db import transaction

from core.exceptions import ApplicationError
from documents.models.folder import DocumentFolder


@transaction.atomic
def create_folder(*, organization, name: str, parent: DocumentFolder | None = None) -> DocumentFolder:
    if parent is not None and parent.organization_id != organization.id:
        raise ApplicationError("parent folder must belong to the same organization.", code="cross_org_reference")
    return DocumentFolder.objects.create(organization=organization, name=name, parent=parent)
