from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from core.models import TenantScopedModel


class AIConversation(TenantScopedModel):
    """A user's Ask Books thread. Org-scoped by RLS AND owner-scoped in every
    query (ai/api/views.py, ai/orchestration/conversations.py) — a colleague
    in the same organization cannot read someone else's questions. Deleted
    by the retention task after AI_CONVERSATION_RETENTION_DAYS of inactivity.
    Never embedded or indexed (phase section 36)."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ai_conversations")
    title = models.CharField(max_length=200, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["organization", "user", "last_message_at"])]
        ordering = ["-last_message_at", "-created_at"]

    def __str__(self):
        return f"AIConversation({self.id})"


class MessageRole(models.TextChoices):
    USER = "user", "User"
    ASSISTANT = "assistant", "Assistant"


class AIMessage(TenantScopedModel):
    """Only what the conversation view needs: the question, the returned
    answer and its validated citations. Tool payloads and prompts are NOT
    stored (phase section 37)."""

    conversation = models.ForeignKey(AIConversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=10, choices=MessageRole.choices)
    content = models.TextField()
    sources = models.JSONField(default=list, blank=True, encoder=DjangoJSONEncoder)
    status = models.CharField(max_length=20, blank=True)
    request_id = models.CharField(max_length=64, blank=True)

    class Meta:
        indexes = [models.Index(fields=["organization", "conversation", "created_at"])]
        ordering = ["created_at"]

    def __str__(self):
        return f"AIMessage({self.conversation_id}, {self.role})"
