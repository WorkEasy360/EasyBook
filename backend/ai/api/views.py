"""Ask Books REST API (phase sections 63-66).

Tenant comes ONLY from `OrganizationScopedMixin` (the verified membership for
the X-Organization-Id header); no request body field can select an
organization. Orchestration lives in ai/orchestration — views only validate
input, call a service, and render.

AI failures are RETURNED as error-envelope responses rather than raised:
ATOMIC_REQUESTS rolls back the request transaction on an exception, which
would also erase the AIRequestLog row recording the failure.
"""

import datetime
from decimal import Decimal

from django.conf import settings
from django.db.models import Count, Sum
from rest_framework import generics, serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from ai.models import AIConversation, AIMessage, AIRequestLog, DocumentIndex
from ai.orchestration.assist import draft_payment_reminder, suggest_expense_account
from ai.orchestration.errors import AskBooksError
from ai.orchestration.service import AskBooksService
from authz.permissions import HasOrgPermission
from authz.roles import Permission
from core.idempotency import _hash_body
from core.models import IdempotencyKey
from core.views import OrganizationScopedMixin
from documents.models.document import Document
from reports.selectors.params import parse_date


def _error_response(request, exc: AskBooksError) -> Response:
    return Response(
        {"error": {"code": exc.code, "message": exc.message, "details": None, "request_id": getattr(request, "request_id", "")}},
        status=exc.status_code,
    )


class AskSerializer(serializers.Serializer):
    question = serializers.CharField(max_length=20_000, trim_whitespace=True)
    conversation_id = serializers.UUIDField(required=False)
    document_id = serializers.UUIDField(required=False)


class AskView(OrganizationScopedMixin, APIView):
    """POST /api/v1/ai/ask

    Duplicate submissions: an `Idempotency-Key` header replays the first
    response for the same key + body without a second model call (and
    without consuming rate limit). Without a key, each submission is an
    independent, separately rate-limited question — asking is read-only, so
    a duplicate can never double-apply anything."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.USE_AI_ASSISTANT

    def post(self, request):
        serializer = AskSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        key = request.headers.get("Idempotency-Key")
        body_hash = _hash_body(request.data)
        # Scoped per USER, not just per organization: an answer is shaped by
        # the asker's own permissions and conversation, so a colleague reusing
        # the same key and question must never receive it.
        scope = f"{request.path}#user:{request.user.id}"
        if key:
            existing = IdempotencyKey.objects.filter(key=key, request_path=scope, request_body_hash=body_hash).first()
            if existing is not None:
                return Response(existing.response_body, status=existing.response_status)

        try:
            result = AskBooksService().answer(
                user=request.user, organization=request.organization, question=data["question"],
                conversation_id=data.get("conversation_id"), document_id=data.get("document_id"),
                request_id=request.request_id,
            )
        except AskBooksError as exc:
            return _error_response(request, exc)

        payload = result.to_response()
        if key:
            IdempotencyKey.objects.create(
                organization=request.organization, key=key, request_path=scope,
                request_body_hash=body_hash, response_status=200, response_body=payload,
            )
        return Response(payload)


class ConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIConversation
        fields = ["id", "title", "created_at", "last_message_at"]


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = AIMessage
        fields = ["id", "role", "content", "sources", "status", "request_id", "created_at"]


class ConversationListView(OrganizationScopedMixin, generics.ListAPIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.USE_AI_ASSISTANT
    serializer_class = ConversationSerializer

    def get_queryset(self):
        # Owner-only: organization membership does not grant reading a
        # colleague's questions.
        return AIConversation.objects.filter(user_id=self.request.user.id)


class ConversationDetailView(OrganizationScopedMixin, APIView):
    permission_classes = [HasOrgPermission]
    required_permission = Permission.USE_AI_ASSISTANT

    def get(self, request, pk):
        conversation = AIConversation.objects.filter(pk=pk, user_id=request.user.id).first()
        if conversation is None:
            return _error_response(request, AskBooksError("conversation_not_found", "Conversation not found.", 404))
        return Response({
            **ConversationSerializer(conversation).data,
            "messages": MessageSerializer(conversation.messages.all(), many=True).data,
        })


def _index_payload(document, index) -> dict:
    return {
        "document_id": str(document.pk),
        "status": index.status if index else "not_indexed",
        "reason_code": index.reason_code if index else "",
        "chunk_count": index.chunk_count if index else 0,
        "embedding_config_key": index.embedding_config_key if index else "",
        "indexed_at": index.indexed_at if index else None,
    }


class DocumentIndexView(OrganizationScopedMixin, APIView):
    """POST /ai/documents/{id}/index    — idempotent; a no-op for unchanged content
    POST /ai/documents/{id}/reindex  — force a rebuild
    GET  /ai/documents/{id}/index    — current index state

    Indexing is enqueued on Celery (never embedded inline in the request)."""

    permission_classes = [HasOrgPermission]
    force = False

    @property
    def required_permission(self):
        return Permission.VIEW_DOCUMENTS if self.request.method == "GET" else Permission.MANAGE_AI_INDEX

    def _document(self, pk):
        return Document.objects.filter(pk=pk).first()  # tenant-scoped; another org's id is simply not found

    def get(self, request, pk):
        document = self._document(pk)
        if document is None:
            return _error_response(request, AskBooksError("document_not_found", "Document not found.", 404))
        return Response(_index_payload(document, DocumentIndex.objects.filter(document=document).first()))

    def post(self, request, pk):
        from django.db import transaction

        from ai.rag.tasks import index_document_task

        document = self._document(pk)
        if document is None:
            return _error_response(request, AskBooksError("document_not_found", "Document not found.", 404))
        document_id, organization_id, actor_id = str(document.pk), str(document.organization_id), str(request.user.id)
        force = self.force
        transaction.on_commit(lambda: index_document_task.delay(document_id, organization_id, force, actor_id))
        return Response(
            {**_index_payload(document, DocumentIndex.objects.filter(document=document).first()), "enqueued": True, "force": force},
            status=202,
        )


class DocumentReindexView(DocumentIndexView):
    force = True
    http_method_names = ["post", "options"]


class UsageView(OrganizationScopedMixin, APIView):
    """GET /ai/usage?from_date=&to_date= — organization-level usage metadata.
    `estimated_cost` appears only when AI_MODEL_PRICING is configured, and is
    an estimate at CURRENT configured prices, never a billing record."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.VIEW_AI_USAGE

    def get(self, request):
        to_date = parse_date(request.query_params.get("to_date"), param_name="to_date") or datetime.date.today()
        from_date = parse_date(request.query_params.get("from_date"), param_name="from_date") or to_date.replace(day=1)
        logs = AIRequestLog.objects.filter(created_at__date__gte=from_date, created_at__date__lte=to_date)
        totals = logs.aggregate(
            requests=Count("id"), input_tokens=Sum("input_tokens"), output_tokens=Sum("output_tokens"),
            cached_input_tokens=Sum("cached_input_tokens"), embedding_tokens=Sum("embedding_tokens"),
        )
        by_model = list(
            logs.values("provider", "model").annotate(
                requests=Count("id"), input_tokens=Sum("input_tokens"), output_tokens=Sum("output_tokens")
            ).order_by("provider", "model")
        )
        pricing = getattr(settings, "AI_MODEL_PRICING", {}) or {}
        for row in by_model:
            price = pricing.get(row["model"])
            if price:
                million = Decimal("1000000")
                row["estimated_cost"] = str(
                    Decimal(str(row["input_tokens"] or 0)) * Decimal(str(price.get("input_per_million", "0"))) / million
                    + Decimal(str(row["output_tokens"] or 0)) * Decimal(str(price.get("output_per_million", "0"))) / million
                )
        return Response({
            "from_date": from_date, "to_date": to_date, "totals": totals, "by_model": by_model,
            "by_status": list(logs.values("status").annotate(requests=Count("id")).order_by("status")),
            "by_feature": list(logs.values("feature").annotate(requests=Count("id")).order_by("feature")),
        })


class ReminderDraftSerializer(serializers.Serializer):
    invoice_id = serializers.UUIDField()


class PaymentReminderDraftView(OrganizationScopedMixin, APIView):
    """POST /ai/drafts/payment-reminder — returns a draft; never sends it."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.USE_AI_ASSISTANT

    def post(self, request):
        serializer = ReminderDraftSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            return Response(draft_payment_reminder(
                user=request.user, organization=request.organization,
                invoice_id=serializer.validated_data["invoice_id"], request_id=request.request_id,
            ))
        except AskBooksError as exc:
            return _error_response(request, exc)


class ExpenseAccountSuggestionSerializer(serializers.Serializer):
    description = serializers.CharField(max_length=1000)
    vendor_name = serializers.CharField(max_length=255, required=False, allow_blank=True)


class ExpenseAccountSuggestionView(OrganizationScopedMixin, APIView):
    """POST /ai/suggestions/expense-account — a suggestion only; applies nothing."""

    permission_classes = [HasOrgPermission]
    required_permission = Permission.USE_AI_ASSISTANT

    def post(self, request):
        serializer = ExpenseAccountSuggestionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            return Response(suggest_expense_account(
                user=request.user, organization=request.organization,
                description=serializer.validated_data["description"],
                vendor_name=serializer.validated_data.get("vendor_name", ""), request_id=request.request_id,
            ))
        except AskBooksError as exc:
            return _error_response(request, exc)
