"""Document retrieval tools (category "document").

These are invoked by the Ask Books orchestrator itself — never offered to
the model during tool planning — so retrieved, untrusted document text never
shares a model turn in which tools can be called (ai/orchestration/service.py).
They still go through ai/tools/executor.py, so permission, tenant and schema
checks are identical to every other tool.

Document access in EasyBook is organization-level RBAC (`VIEW_DOCUMENTS`);
there is no per-document ACL in the documents module to honour. If one is
added, it must be enforced inside ai/retrieval/search.py::retrievable_chunks
(in the SQL), not as a post-filter here.
"""

from rest_framework import serializers

from ai.retrieval.search import document_excerpts, hybrid_search
from ai.tools.base import Tool, ToolContext, ToolError, ToolResult
from ai.tools.registry import register
from ai.tools.schemas import StrictSerializer
from authz.roles import Permission
from documents.models.document import Document, DocumentType, UploadStatus


def _chunk_payload(chunk) -> dict:
    # Full chunk text: chunks are already bounded by AI_CHUNK_MAX_CHARS, and
    # the number of chunks by AI_MAX_RETRIEVED_CHUNKS.
    return {
        "source_id": chunk.source.source_id,
        "document_title": chunk.document_title,
        "document_type": chunk.document_type,
        "page": chunk.page_number,
        "section": chunk.section or None,
        "text": chunk.text,
    }


class SearchDocumentsArgs(StrictSerializer):
    query = serializers.CharField(max_length=500, help_text="What to look for in the organization's documents.")
    document_type = serializers.ChoiceField(choices=DocumentType.values, required=False)
    document_id = serializers.UUIDField(required=False, help_text="Restrict the search to one document.")
    limit = serializers.IntegerField(required=False, min_value=1, max_value=20)


def _search_documents(ctx: ToolContext, args: dict) -> ToolResult:
    document_ids = None
    if args.get("document_id"):
        if not Document.objects.filter(pk=args["document_id"], upload_status=UploadStatus.READY).exists():
            raise ToolError("not_found", "Document not found.")
        document_ids = [args["document_id"]]
    result = hybrid_search(
        organization=ctx.organization, query=args["query"], limit=args.get("limit"),
        document_ids=document_ids, document_type=args.get("document_type"),
    )
    return ToolResult(
        tool="search_documents", status="ok" if result.chunks else "not_found",
        summary={"match_count": len(result.chunks), "retrieval_mode": result.mode},
        data={"matches": [_chunk_payload(chunk) for chunk in result.chunks]},
        sources=[chunk.source for chunk in result.chunks],
        message="" if result.chunks else "No relevant document passage was found.",
        error_code="" if result.chunks else "no_relevant_documents",
        telemetry={"retrieval_count": len(result.chunks), "embedding_tokens": result.embedding_tokens,
                   "retrieval_latency_ms": result.latency_ms},
    )


class DocumentExcerptArgs(StrictSerializer):
    document_id = serializers.UUIDField()
    limit = serializers.IntegerField(required=False, min_value=1, max_value=20)


def _document_excerpts(ctx: ToolContext, args: dict) -> ToolResult:
    from ai.config import get_ai_config

    document = Document.objects.filter(pk=args["document_id"], upload_status=UploadStatus.READY).first()
    if document is None:
        raise ToolError("not_found", "Document not found.")
    limit = min(args.get("limit") or get_ai_config().max_retrieved_chunks, get_ai_config().max_retrieved_chunks)
    chunks = document_excerpts(organization=ctx.organization, document_id=document.pk, limit=limit)
    if not chunks:
        raise ToolError("not_found", "This document has no indexed text yet.")
    return ToolResult(
        tool="get_document_excerpts", status="ok",
        summary={"document_title": document.title, "excerpt_count": len(chunks)},
        data={"matches": [_chunk_payload(chunk) for chunk in chunks]},
        sources=[chunk.source for chunk in chunks],
        telemetry={"retrieval_count": len(chunks)},
    )


register(Tool(
    name="search_documents",
    description="Hybrid (semantic + keyword + exact identifier) search over the organization's indexed documents.",
    required_permissions=(Permission.VIEW_DOCUMENTS,),
    input_serializer=SearchDocumentsArgs,
    handler=_search_documents,
    category="document",
))
register(Tool(
    name="get_document_excerpts",
    description="The opening indexed passages of one document, in reading order (for summarizing it).",
    required_permissions=(Permission.VIEW_DOCUMENTS,),
    input_serializer=DocumentExcerptArgs,
    handler=_document_excerpts,
    category="document",
))
