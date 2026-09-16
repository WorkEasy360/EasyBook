"""Conversation persistence. Every lookup is scoped to the requesting user
on top of the organization scoping RLS/TenantManager already apply — an
id belonging to another user or organization is simply not found."""

from django.utils import timezone

from ai.models import AIConversation, AIMessage, MessageRole
from ai.orchestration.errors import AskBooksError
from ai.providers.base import Message

MAX_HISTORY_MESSAGE_CHARS = 4_000


def get_conversation(*, user, conversation_id) -> AIConversation:
    conversation = AIConversation.objects.filter(pk=conversation_id, user_id=user.id).first()
    if conversation is None:
        raise AskBooksError("conversation_not_found", "Conversation not found.", 404)
    return conversation


def history_messages(conversation: AIConversation | None, limit: int) -> tuple[Message, ...]:
    """Prior question/answer TEXT only (no tool payloads, no documents) —
    enough for follow-ups like "and last month?" while never re-feeding
    stale figures as if they were tool results."""
    if conversation is None or limit <= 0:
        return ()
    rows = list(AIMessage.objects.filter(conversation=conversation).order_by("-created_at")[:limit])
    rows.reverse()
    return tuple(Message(role=row.role, text=row.content[:MAX_HISTORY_MESSAGE_CHARS]) for row in rows)


def append_exchange(*, conversation, user, organization, question: str, answer: str, sources: list, status: str, request_id: str) -> AIConversation:
    now = timezone.now()
    if conversation is None:
        conversation = AIConversation.objects.create(organization=organization, user=user, title=question[:80])
    AIMessage.objects.create(
        organization=organization, conversation=conversation, role=MessageRole.USER, content=question, request_id=request_id[:64],
    )
    AIMessage.objects.create(
        organization=organization, conversation=conversation, role=MessageRole.ASSISTANT, content=answer,
        sources=sources, status=status, request_id=request_id[:64],
    )
    conversation.last_message_at = now
    conversation.save(update_fields=["last_message_at", "updated_at"])
    return conversation
